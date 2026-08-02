"""Travel scopes for the round-trip flight price monitor.

A scope is one trip being shopped for: a route, a departure-date window, a return-date window and
the filters that make a fare relevant (stops, cabin, trip length). Scopes live in ``scope.json`` so
travel plans can change without touching code.

The unit of work is a **(departure date, return date) pair** -- one Google Flights round-trip search
-- because a true round-trip fare only exists for a specific pair of dates.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta
from pathlib import Path

SCOPE_FILE = Path(__file__).parent / "scope.json"

SEAT_TYPES = ("economy", "premium-economy", "business", "first")


class ConfigError(ValueError):
    """scope.json is malformed, or names a scope that does not exist."""


@dataclasses.dataclass(frozen=True)
class TravelScope:
    """One trip to monitor. Dates are inclusive on both ends of each window."""
    name: str
    from_airport: str
    to_airport: str
    depart_from: date
    depart_to: date
    return_from: date
    return_to: date
    max_stops: int = 0                  # 0 = direct only, 1 = at most one stop
    seat: str = "economy"
    min_nights: int | None = None
    max_nights: int | None = None
    adults: int = 1
    children: int = 0
    currency: str = "HKD"
    language: str = "en"
    # Quality knob: how many outbound options to open per date pair. Each one opened yields that
    # (airline, departure bucket) against *every* return bucket, so this bounds the grid width.
    max_outbounds_per_pair: int = 6
    # Safety knob: hard ceiling on page loads for this scope in one run. Hitting it stops the scope
    # early and is reported as budget_exhausted -- never silently.
    max_searches_per_run: int = 100

    def __post_init__(self):
        if self.seat not in SEAT_TYPES:
            raise ConfigError(f"{self.name}: seat must be one of {SEAT_TYPES}, got {self.seat!r}")
        if self.depart_from > self.depart_to:
            raise ConfigError(f"{self.name}: depart_from is after depart_to")
        if self.return_from > self.return_to:
            raise ConfigError(f"{self.name}: return_from is after return_to")
        if self.max_outbounds_per_pair < 1:
            raise ConfigError(f"{self.name}: max_outbounds_per_pair must be >= 1")

    def date_pairs(self) -> list[tuple[date, date]]:
        """Every (departure, return) pair in the windows that is a trip of an allowed length."""
        pairs = []
        for depart in _date_range(self.depart_from, self.depart_to):
            for arrive_back in _date_range(self.return_from, self.return_to):
                nights = (arrive_back - depart).days
                if nights < 1:
                    continue
                if self.min_nights is not None and nights < self.min_nights:
                    continue
                if self.max_nights is not None and nights > self.max_nights:
                    continue
                pairs.append((depart, arrive_back))
        return pairs


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


DATE_FIELDS = ("depart_from", "depart_to", "return_from", "return_to")


def load_scopes(path: Path | str = SCOPE_FILE, names: list[str] | None = None) -> list[TravelScope]:
    """Read ``scope.json``. ``names`` selects a subset; unknown names raise :class:`ConfigError`."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        entries = raw["scopes"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigError(f"could not read scopes from {path}: {exc}") from exc

    scopes = []
    for entry in entries:
        entry = dict(entry)
        for field in DATE_FIELDS:
            try:
                entry[field] = date.fromisoformat(entry[field])
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(f"{entry.get('name', '<unnamed>')}: bad {field}: {exc}") from exc
        try:
            scopes.append(TravelScope(**entry))
        except TypeError as exc:
            raise ConfigError(f"{entry.get('name', '<unnamed>')}: {exc}") from exc

    if names is None:
        return scopes

    by_name = {scope.name: scope for scope in scopes}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise ConfigError(f"unknown scope(s) {unknown}; {path} defines {sorted(by_name)}")
    return [by_name[name] for name in names]
