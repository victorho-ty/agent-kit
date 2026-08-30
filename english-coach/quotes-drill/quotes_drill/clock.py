"""The one place that reads the wall clock.

Timestamps are stored as ISO-8601 with the app timezone's offset. Asia/Hong_Kong
has no daylight saving, so the offset never changes and text ordering on
`last_tested_at` is chronological ordering -- which is what lets the queue be a
plain `ORDER BY`. Comparisons that decide something (is this entry due?) still
parse, so a future timezone change cannot silently reorder anything.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def to_iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def parse(text: str, tz: ZoneInfo | None = None) -> datetime:
    """Parse a stored timestamp, or an operator-supplied `--now`.

    A value without an offset is read as local to `tz`; the stored form always
    carries one.
    """
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        if tz is None:
            raise ValueError(f"timestamp {text!r} has no timezone and none was given")
        parsed = parsed.replace(tzinfo=tz)
    return parsed
