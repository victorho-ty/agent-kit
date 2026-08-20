"""Which market a ticker trades on, and when that market opens.

A watchlist spanning US and HK listings has **two** report times, not one, and
they are not a fixed offset apart -- daylight saving moves New York twice a year
and Hong Kong never. "30 minutes before the open" is therefore a question only a
market calendar can answer, per ticker, per day.

``exchange_calendars`` is imported lazily. The detector, the portfolio maths and
the whole test suite never touch this module, and loading a package that builds
decades of holiday schedules is not free.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

# Suffix -> (market label, calendar code, trading timezone). Suffixes are
# Yahoo's, because that is what the watchlist is written in.
SUFFIX_MARKETS: dict[str, tuple[str, str, str]] = {
    ".HK": ("HK", "XHKG", "Asia/Hong_Kong"),
    ".SS": ("CN", "XSHG", "Asia/Shanghai"),
    ".SZ": ("CN", "XSHE", "Asia/Shanghai"),
    ".T": ("JP", "XTKS", "Asia/Tokyo"),
    ".L": ("UK", "XLON", "Europe/London"),
}
DEFAULT_MARKET = ("US", "XNYS", "America/New_York")


def market_of(ticker: str) -> tuple[str, str, str]:
    """``(label, calendar code, timezone)``. Unsuffixed tickers are US listings."""
    upper = ticker.strip().upper()
    for suffix, entry in SUFFIX_MARKETS.items():
        if upper.endswith(suffix):
            return entry
    return DEFAULT_MARKET


def market_label(ticker: str) -> str:
    return market_of(ticker)[0]


def timezone_of(ticker: str) -> ZoneInfo:
    return ZoneInfo(market_of(ticker)[2])


@lru_cache(maxsize=8)
def _calendar(code: str):
    import exchange_calendars as xcals

    return xcals.get_calendar(code)


def is_session(ticker: str, day: date) -> bool:
    """Did this market trade on ``day``? A holiday is an empty result, not a fault."""
    try:
        return bool(_calendar(market_of(ticker)[1]).is_session(day.isoformat()))
    except Exception:
        # A calendar that cannot answer must not take the run down with it; a
        # weekday is the safe assumption and the bar fetch will show the truth.
        return day.weekday() < 5


def next_open(ticker: str, after: datetime) -> datetime | None:
    """The next session open, in the market's own timezone."""
    code, zone = market_of(ticker)[1], timezone_of(ticker)
    try:
        calendar = _calendar(code)
    except Exception:
        return None
    cursor = after.astimezone(zone)
    for offset in range(0, 10):
        day = (cursor + timedelta(days=offset)).date()
        if not calendar.is_session(day.isoformat()):
            continue
        opening = calendar.session_open(day.isoformat()).to_pydatetime().astimezone(zone)
        if opening > after.astimezone(zone):
            return opening
    return None


def report_due_at(ticker: str, after: datetime, minutes_before: int) -> datetime | None:
    """When the daily report for this ticker's market should be sent."""
    opening = next_open(ticker, after)
    return opening - timedelta(minutes=minutes_before) if opening else None


def group_by_market(tickers: list[str]) -> dict[str, list[str]]:
    """Split a watchlist into the market groups that share a report time."""
    groups: dict[str, list[str]] = {}
    for ticker in tickers:
        groups.setdefault(market_label(ticker), []).append(ticker)
    return groups
