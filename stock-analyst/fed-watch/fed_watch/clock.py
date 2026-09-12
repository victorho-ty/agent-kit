"""The current time, in one place, so tests can move it.

Every module that needs "now" calls :func:`now` rather than ``datetime.now()``.
That is what lets the whole suite run against a fixed instant -- and which
meeting counts as "the next one" depends entirely on the wall clock.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import settings


def now(tz: ZoneInfo | None = None) -> datetime:
    """Timezone-aware current time.

    ``FED_WATCH_NOW`` pins it to an ISO8601 instant. That override exists for
    tests and for replaying a snapshot; it is never set in production.
    """
    zone = tz or settings.timezone()
    pinned = os.environ.get("FED_WATCH_NOW")
    if pinned:
        parsed = datetime.fromisoformat(pinned)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return datetime.now(zone)


def today(tz: ZoneInfo | None = None) -> date:
    return now(tz).date()
