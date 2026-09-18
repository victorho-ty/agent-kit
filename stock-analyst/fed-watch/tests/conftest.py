"""Shared fixtures. Nothing here touches the network or the wall clock."""

from __future__ import annotations

from datetime import date

import pytest

from fed_watch.models import PolicyRate, Quote
from fed_watch.quotes import INTRADAY_SOURCE

# The state of the world on 2026-09-11, as published by the New York Fed.
# Target range 3.50-3.75%, effective rate 3.63% -- not the 3.625% midpoint.
EFFR = 3.63
TARGET_LOW = 3.50
TARGET_HIGH = 3.75

# CME's own "MID PRICE" column for ZQU6 on the FedWatch page, alongside the
# probabilities it published from it: 13.3% no change, 86.7% a 25bp hike.
CME_MID_ZQU26 = 96.2688
CME_PUBLISHED_NO_CHANGE = 13.3
CME_PUBLISHED_HIKE_25 = 86.7

# Yahoo's last trade for the same contracts, which is what the bundle actually
# reads and is about a point of probability away from CME's mid.
YAHOO_ZQU26 = 96.269997
YAHOO_ZQV26 = 96.139999
YAHOO_ZQX26 = 96.040001

# 2026-09-16 then 2026-10-28, then a clear November, then 2026-12-09.
CALENDAR = [
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
    date(2027, 1, 27),
]


# Every fixture below describes the world on 2026-09-11, and the calendar's
# first meeting is five days after it. Since storage now filters decided
# meetings out on the way back (`db._hydrate`), a suite that read the real
# clock would start dropping 2026-09-16 from its own fixtures on 2026-09-17 and
# fail for reasons having nothing to do with the code. Pinning is what
# `FED_WATCH_NOW` is for.
PINNED_NOW = "2026-09-12T09:00:00+08:00"


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    monkeypatch.setenv("FED_WATCH_NOW", PINNED_NOW)


@pytest.fixture
def policy() -> PolicyRate:
    return PolicyRate(
        effr=EFFR, as_of=date(2026, 9, 10), target_low=TARGET_LOW, target_high=TARGET_HIGH
    )


@pytest.fixture
def calendar() -> list[date]:
    return list(CALENDAR)


@pytest.fixture
def month_has_meeting(calendar):
    def check(year: int, month: int) -> bool:
        return any(m.year == year and m.month == month for m in calendar)

    return check


def quote(symbol: str, price: float, source: str = INTRADAY_SOURCE) -> Quote:
    return Quote(symbol=symbol, price=price, as_of=None, source=source)


@pytest.fixture
def quotes() -> dict[str, Quote]:
    return {
        "ZQU26.CBT": quote("ZQU26.CBT", YAHOO_ZQU26),
        "ZQV26.CBT": quote("ZQV26.CBT", YAHOO_ZQV26),
        "ZQX26.CBT": quote("ZQX26.CBT", YAHOO_ZQX26),
    }


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "fed_watch.db"
    monkeypatch.setenv("FED_WATCH_DB", str(path))
    return path
