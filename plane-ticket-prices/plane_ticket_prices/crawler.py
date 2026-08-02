"""Playwright crawler for Google Flights round-trip prices.

The driver is deliberately thin: every decision lives in pure functions at the
top of this module (importable and testable without a browser), and Playwright
is imported lazily so ``--dry-run`` and the test suite never need Chromium.

Flow per (depart, return) date pair:

1. Build the search URL with ``fast_flights.querying.create_query`` -- the tfs
   protobuf encodes both legs, seat, stops, passengers and currency.
2. Load it, dismiss the consent banner if present, wait for outbound options.
3. Parse every aria-label on the page; keep only ``origin -> dest`` options.
4. Dedupe to one option per (airline, departure 3h-bucket), cheapest first,
   capped at ``max_outbounds_per_pair``.
5. For each selected outbound: reload the search, click the row, read the
   return grid, parse ``dest -> origin`` options with their round-trip prices.
6. Aggregate into grid cells (airline, dep_bucket) x ret_bucket and full
   itinerary rows; the caller upserts them into SQLite.

Browsing state (consent) persists in a disposable Chromium profile next to the
database, per the README. The profile may be deleted freely.
"""
from __future__ import annotations

import os
import random
import time
from datetime import date, timedelta
from pathlib import Path

from .config.scope import TravelScope
from .db import bucket_from_hour
from .parsing import (
    OptionLabel,
    extract_outbound_options,
    extract_return_options,
    learn_airport_codes,
)

SEARCH_URL = "https://www.google.com/travel/flights/search?tfs={tfs}&hl={hl}&curr={curr}"

# Fixed pacing between page loads; set TICKET_PRICES_DELAY to tune (seconds).
DEFAULT_DELAY = float(os.environ.get("TICKET_PRICES_DELAY", "2.0"))

# aria-label elements we consider "option rows" -- anything with a leg phrase.
_CONSENT_SELECTORS = (
    "button[aria-label*='Accept all']",
    "button[aria-label*='accept all']",
    "button[aria-label*='I agree']",
    "button[aria-label*='Agree to the use of cookies']",
    "button:has-text('Accept all')",
    "button:has-text('I agree')",
)


# ---------------------------------------------------------------------------
# Pure helpers (no browser)
# ---------------------------------------------------------------------------

def build_search_url(scope: TravelScope, depart: date, returnd: date) -> str:
    """Round-trip Google Flights search URL for one (depart, return) pair.

    The tfs parameter is the base64 protobuf built by fast_flights; it encodes
    both legs, so a decode round-trip test asserts ``trip == ROUND_TRIP``.
    """
    from fast_flights.querying import FlightQuery, Passengers, create_query

    query = create_query(
        flights=[
            FlightQuery(
                date=depart.isoformat(),
                from_airport=scope.from_airport,
                to_airport=scope.to_airport,
                max_stops=scope.max_stops,
            ),
            FlightQuery(
                date=returnd.isoformat(),
                from_airport=scope.to_airport,
                to_airport=scope.from_airport,
                max_stops=scope.max_stops,
            ),
        ],
        seat=scope.seat,
        trip="round-trip",
        passengers=Passengers(adults=scope.adults, children=scope.children),
        language=scope.language,
        currency=scope.currency,
    )
    return SEARCH_URL.format(tfs=query.to_str(), hl=scope.language, curr=scope.currency)


def decode_tfs(tfs: str) -> "object":
    """Decode a tfs parameter back to its protobuf Info (for tests/triage)."""
    import base64

    from fast_flights.pb import flights_pb2 as pb

    info = pb.Info()
    info.ParseFromString(base64.b64decode(tfs))
    return info


def _price(option: OptionLabel) -> float | None:
    return option.price


def _price_or_inf(option: OptionLabel) -> float:
    return option.price if option.price is not None else float("inf")


def select_outbound_options(options: list[OptionLabel], max_cells: int) -> list[OptionLabel]:
    """Cheapest option per (airline, dep_bucket), deterministic order.

    Keeps the cheapest round-trip price per distinct (outbound carrier,
    departure bucket) cell, ties broken by departure time, then caps the list
    at ``max_cells`` so a run cannot explode in page loads. Unpriced labels
    ("Leaves ..." duplicates) sort as +inf and never displace a priced card.
    """
    best: dict[tuple[str, str], OptionLabel] = {}
    for option in options:
        leg = option.legs[0]
        key = (leg.airline, bucket_from_hour(leg.depart_hour))
        current = best.get(key)
        if current is None or _price_or_inf(option) < _price_or_inf(current):
            best[key] = option
        elif _price_or_inf(option) == _price_or_inf(current) and leg.depart_time < current.legs[0].depart_time:
            best[key] = option
    ordered = sorted(
        best.values(),
        key=lambda o: (_price_or_inf(o), o.legs[0].depart_time, o.legs[0].airline),
    )
    return ordered[:max_cells]


def cells_and_itineraries(
    outbound: OptionLabel,
    returns: list[OptionLabel],
    *,
    scope: TravelScope,
    run_date: str,
    depart: date,
    returnd: date,
) -> tuple[list[dict], list[dict]]:
    """Aggregate one outbound x its return options into grid cells + itinerary rows.

    A grid cell is (airline=outbound carrier, dep_bucket) x ret_bucket; the
    price is the cheapest round-trip total across return options in that
    return bucket. Return carrier is carried, not part of the key.
    """
    out_leg = outbound.legs[0]
    dep_bucket = bucket_from_hour(out_leg.depart_hour)
    cells: dict[tuple, dict] = {}
    itineraries: list[dict] = []

    for ret in returns:
        ret_leg = ret.legs[0]
        price = _price(ret)
        if price is None:
            continue
        ret_bucket = bucket_from_hour(ret_leg.depart_hour)
        key = (ret_bucket, ret_leg.stops)
        cell = cells.get(key)
        if cell is None or price < cell["min_price"]:
            cells[key] = {
                "run_date": run_date,
                "scope": scope.name,
                "origin": scope.from_airport,
                "dest": scope.to_airport,
                "depart_date": depart,
                "return_date": returnd,
                "airline": out_leg.airline,
                "return_airline": ret_leg.airline,
                "dep_bucket": dep_bucket,
                "ret_bucket": ret_bucket,
                "out_stops": out_leg.stops,
                "ret_stops": ret_leg.stops,
                "seat": scope.seat,
                "currency": scope.currency,
                "min_price": price,
            }
        itineraries.append({
            "run_date": run_date,
            "scope": scope.name,
            "origin": scope.from_airport,
            "dest": scope.to_airport,
            "depart_date": depart,
            "return_date": returnd,
            "out_airline": out_leg.airline,
            "ret_airline": ret_leg.airline,
            "out_depart": _iso_local(depart, out_leg.depart_time),
            "out_arrive": _iso_local(
                depart + timedelta(days=out_leg.arrive_day_shift), out_leg.arrive_time)
            if out_leg.arrive_time
            else _iso_local(depart, _add_minutes(out_leg.depart_time, out_leg.duration_min)),
            "ret_depart": _iso_local(returnd, ret_leg.depart_time),
            "ret_arrive": _iso_local(
                returnd + timedelta(days=ret_leg.arrive_day_shift), ret_leg.arrive_time)
            if ret_leg.arrive_time
            else _iso_local(returnd, _add_minutes(ret_leg.depart_time, ret_leg.duration_min)),
            "out_stops": out_leg.stops,
            "ret_stops": ret_leg.stops,
            "dep_bucket": dep_bucket,
            "ret_bucket": ret_bucket,
            "seat": scope.seat,
            "currency": scope.currency,
            "price": price,
        })

    cell_rows = []
    for key, cell in cells.items():
        ret_bucket, ret_stops = key
        cell = dict(cell)
        cell["ret_bucket"] = ret_bucket
        cell["ret_stops"] = ret_stops
        cell["n_itineraries"] = sum(1 for it in itineraries if it["ret_bucket"] == ret_bucket)
        cell_rows.append(cell)
    cell_rows.sort(key=lambda c: (c["ret_bucket"], c["min_price"]))
    itineraries.sort(key=lambda i: (i["ret_depart"], i["price"]))
    return cell_rows, itineraries


def _price(option: OptionLabel) -> float | None:
    return option.price


def _add_minutes(hhmm: str, minutes: int) -> str:
    hour, minute = (int(part) for part in hhmm.split(":"))
    total = hour * 60 + minute + minutes
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def _iso_local(day: date, hhmm: str) -> str:
    return f"{day.isoformat()}T{hhmm}:00"


# ---------------------------------------------------------------------------
# Browser driver
# ---------------------------------------------------------------------------

class Crawler:
    """One headless Chromium session per run. Usable as a context manager."""

    def __init__(self, *, headless: bool = True, profile_dir: Path | None = None,
                 timezone_id: str = "Asia/Hong_Kong", delay: float = DEFAULT_DELAY,
                 timeout_ms: int = 45_000):
        self.headless = headless
        self.profile_dir = profile_dir or (Path(os.environ.get(
            "TICKET_PRICES_DB", "~/.local/share/hermes-ticket-prices/ticket_prices.db"
        )).expanduser().parent / ".browser_profile")
        self.timezone_id = timezone_id
        self.delay = delay
        self.timeout_ms = timeout_ms
        self._context = None
        self._page = None
        self._rng = random.Random(0xC0FFEE)  # fixed seed -> reproducible pacing

    def __enter__(self) -> "Crawler":
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            locale="en-US",
            timezone_id=self.timezone_id,
            viewport={"width": 1440, "height": 2200},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if getattr(self, "_playwright", None) is not None:
                self._playwright.stop()

    # -- low-level page helpers --------------------------------------------

    def _goto(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self._dismiss_consent()
        time.sleep(self.delay)

    def _dismiss_consent(self) -> None:
        for selector in _CONSENT_SELECTORS:
            try:
                button = self._page.locator(selector).first
                if button.is_visible(timeout=1500):
                    button.click(timeout=3000)
                    time.sleep(1.0)
                    return
            except Exception:
                continue

    def _aria_labels(self) -> list[str]:
        """Every aria-label on the page, deduplicated, order preserved."""
        labels = self._page.eval_on_selector_all(
            "[aria-label]",
            "els => [...new Set(els.map(e => e.getAttribute('aria-label')).filter(Boolean))]",
        )
        return list(labels)

    def _wait_for_options(self, scope: TravelScope, timeout_ms: int | None = None,
                          direction: str = "outbound") -> tuple[list[str], dict[str, str]]:
        """Poll until at least one option in ``direction`` parses.

        Returns (labels, airport_codes); codes are learned from the page's
        "Where from? / Where to?" labels, present from the first paint.
        After selecting an outbound, poll the ``return`` direction -- the
        selected outbound card lingers in the summary bar, so waiting for
        outbound options would return before the return grid renders.
        """
        codes: dict[str, str] = {}
        extract = (extract_outbound_options if direction == "outbound"
                   else extract_return_options)
        deadline = time.monotonic() + (timeout_ms or self.timeout_ms) / 1000
        while time.monotonic() < deadline:
            labels = self._aria_labels()
            codes = learn_airport_codes(labels)
            if extract(labels, scope.from_airport, scope.to_airport, codes):
                return labels, codes
            time.sleep(1.0)
        return self._aria_labels(), codes

    def _click_row(self, option: OptionLabel) -> None:
        """Select the option card via keyboard activation.

        The card is a ``role=link`` div whose children intercept pointer
        events (Playwright's actionability check fails), but focus + Enter
        activates it reliably. The locator matches the raw label's own
        phrasing ("Leaves <airport> at <time>") -- the card is the element
        whose aria-label starts with "From ", which excludes the bare
        "Leaves ..." / "Flight details." duplicates of the same flight.
        """
        phrase = option.raw.split("Leaves", 1)[1].split(" on ", 1)[0].strip()
        locator = self._page.locator(f"[aria-label^='From '][aria-label*='{phrase}']").first
        locator.focus(timeout=self.timeout_ms)
        self._page.keyboard.press("Enter")
        time.sleep(self.delay)

    # -- the crawl ----------------------------------------------------------

    def collect_pair(self, scope: TravelScope, depart: date, returnd: date, run_date: str) -> dict:
        """Crawl one (depart, return) pair; returns cells + itineraries + stats."""
        url = build_search_url(scope, depart, returnd)
        self._goto(url)
        labels, codes = self._wait_for_options(scope)
        outbounds = extract_outbound_options(labels, scope.from_airport, scope.to_airport, codes)
        # Only priced cards ("From ... round trip total") are actionable:
        # bare "Leaves ..." / "Flight details" duplicates carry no price and
        # cannot contribute cells, so they are never selected or clicked.
        outbounds = [o for o in outbounds if o.price is not None]
        if not outbounds:
            return {"pairs_ok": False, "cells": [], "itineraries": [],
                    "detail": "no outbound options parsed"}

        selected = select_outbound_options(outbounds, scope.max_outbounds_per_pair)
        cells: list[dict] = []
        itineraries: list[dict] = []
        searches = 1

        for outbound in selected:
            self._goto(url)  # fresh page per outbound: deterministic, no stale DOM
            searches += 1
            try:
                self._click_row(outbound)
                ret_labels, _ = self._wait_for_options(scope, direction="return")
            except Exception as exc:  # noqa: BLE001 -- one bad row must not kill the pair
                return {"pairs_ok": False, "cells": cells, "itineraries": itineraries,
                        "detail": f"return grid failed for {outbound.legs[0].airline}: {exc}"}

            returns = extract_return_options(ret_labels, scope.from_airport, scope.to_airport, codes)
            returns = [r for r in returns if r.price is not None]
            if not returns:
                continue
            pair_cells, pair_its = cells_and_itineraries(
                outbound, returns, scope=scope, run_date=run_date, depart=depart, returnd=returnd
            )
            cells.extend(pair_cells)
            itineraries.extend(pair_its)

        if not cells:
            return {"pairs_ok": False, "cells": [], "itineraries": [],
                    "detail": "outbound options parsed but no priced return cells"}
        return {"pairs_ok": True, "cells": cells, "itineraries": itineraries,
                "searches": searches, "detail": None}
