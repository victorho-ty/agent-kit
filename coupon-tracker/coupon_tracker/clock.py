"""The only source of "now" in this package.

No other module may call ``datetime.now()``. Every function that needs the
current time takes it as an argument, so tests can freeze it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Hong_Kong"


def tz(name: str = DEFAULT_TZ) -> ZoneInfo:
    return ZoneInfo(name)


def now(tz_name: str = DEFAULT_TZ) -> datetime:
    """Current instant as an aware datetime in the configured zone."""
    return datetime.now(tz(tz_name))


def to_local(dt: datetime, tz_name: str = DEFAULT_TZ) -> datetime:
    """Move an aware datetime into the configured zone; assume local if naive."""
    zone = tz(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def today(now_dt: datetime, tz_name: str = DEFAULT_TZ) -> date:
    return to_local(now_dt, tz_name).date()


def parse_date(value: str) -> date:
    """Parse a strict ISO date. Raises ValueError on anything else."""
    return date.fromisoformat(value)


def parse_datetime(value: str, tz_name: str = DEFAULT_TZ) -> datetime:
    """Parse ISO8601 or a unix epoch into an aware local datetime.

    A bare date means midnight local; a naive datetime is read as local time.
    """
    text = value.strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz(tz_name))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.combine(date.fromisoformat(text), time(0, 0))
    return to_local(parsed, tz_name)


def iso(dt: datetime) -> str:
    """Timestamp form used for every ``*_at`` column."""
    return dt.isoformat(timespec="seconds")


def iso_date(d: date) -> str:
    return d.isoformat()


def days_between(earlier: date, later: date) -> int:
    return (later - earlier).days


def end_of_month(d: date) -> date:
    first_next = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return first_next - timedelta(days=1)
