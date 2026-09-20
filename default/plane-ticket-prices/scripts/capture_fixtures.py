"""Capture real Google Flights aria-labels into tests/fixtures/gf_labels_2026.json.

Runs three searches headlessly and saves every option-ish label:
  - HKG->PEN nonstop, outbound list + return grid after selecting the cheapest
  - HKG->DXB with 1 stop allowed (captures "1 stop flight with ..." labels)

The fixture is the README-mandated "real captured aria-label set" the parser
is pinned against. Rerun any time Google changes the label schema.
"""
import json
import tempfile
from datetime import date
from pathlib import Path

from plane_ticket_prices.config.scope import TravelScope
from plane_ticket_prices.crawler import Crawler, build_search_url

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gf_labels_2026.json"


def capture(crawler: Crawler, url: str, scope: TravelScope) -> dict:
    crawler._goto(url)
    labels = crawler._wait_for_options(scope, timeout_ms=45_000)

    def optionish(l: str) -> bool:
        low = l.lower()
        return ("leaves" in low and ("arrives" in low or "at " in low)) \
            or low.startswith("from ") or low.startswith("flight details")

    out = {
        "search_boxes": [l for l in labels if l.startswith("Where from") or l.startswith("Where to")],
        "outbound": sorted({l for l in labels if optionish(l) and "Leaves" in l and scope.from_airport in _cities(l) or (optionish(l) and "From " in l)}),
        "noise": sorted({l for l in labels if not optionish(l) and len(l) > 8})[:12],
    }
    # keep the option cards (start with "From ") and flight-details variants
    out["outbound"] = sorted({l for l in labels if optionish(l)})[:25]
    return out


def _cities(label: str) -> str:
    return label


def main() -> None:
    pen_scope = TravelScope(
        name="HKG-PEN-Probe", from_airport="HKG", to_airport="PEN",
        depart_from=date(2026, 12, 18), depart_to=date(2026, 12, 20),
        return_from=date(2026, 12, 22), return_to=date(2026, 12, 26),
        max_stops=0, seat="economy", currency="HKD",
    )
    dxb_scope = TravelScope(
        name="HKG-DXB-Probe", from_airport="HKG", to_airport="DXB",
        depart_from=date(2026, 12, 18), depart_to=date(2026, 12, 20),
        return_from=date(2026, 12, 22), return_to=date(2026, 12, 28),
        max_stops=1, seat="economy", currency="HKD",
    )

    profile = Path(tempfile.mkdtemp(prefix="gf-capture-")) / "profile"
    fixture: dict = {}
    with Crawler(headless=True, profile_dir=profile, delay=1.0, timeout_ms=45_000) as crawler:
        pen_url = build_search_url(pen_scope, date(2026, 12, 18), date(2026, 12, 22))
        fixture["HKG-PEN-nonstop"] = capture(crawler, pen_url, pen_scope)

        # select the cheapest outbound, capture the return grid
        labels = crawler._aria_labels()
        target = next(l for l in labels if l.startswith("From ") and "Leaves" in l)
        sig = target.split("Leaves")[1].split(" on ")[0]
        locator = crawler._page.locator(f"[aria-label*='{sig}']").first
        locator.focus()
        crawler._page.keyboard.press("Enter")
        crawler._page.wait_for_timeout(6000)
        ret_labels = crawler._aria_labels()
        fixture["HKG-PEN-return-grid"] = {
            "return": sorted({l for l in ret_labels if optionish_ret(l)})[:25],
            "noise": sorted({l for l in ret_labels if not optionish_ret(l) and len(l) > 8})[:12],
        }

        dxb_url = build_search_url(dxb_scope, date(2026, 12, 18), date(2026, 12, 22))
        fixture["HKG-DXB-1stop"] = capture(crawler, dxb_url, dxb_scope)

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {FIXTURE} with", {k: len(v) for k, v in fixture.items()})


def optionish_ret(l: str) -> bool:
    low = l.lower()
    return ("leaves" in low and "arrives" in low) or low.startswith("from ") \
        or low.startswith("flight details")


if __name__ == "__main__":
    main()
