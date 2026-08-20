"""The current time, in one place, so tests can move it.

Every module that needs "now" calls :func:`now` rather than
``datetime.now()``. That is what lets the whole suite run against a fixed
instant -- a setup detector whose answer depends on the wall clock is a detector
nobody can write a regression test for.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import settings


def now(tz: ZoneInfo | None = None) -> datetime:
    """Timezone-aware current time.

    ``STOCK_DESK_NOW`` pins it to an ISO8601 instant. That override exists for
    tests and for replaying a run; it is never set in production.
    """
    zone = tz or settings.timezone()
    pinned = os.environ.get("STOCK_DESK_NOW")
    if pinned:
        parsed = datetime.fromisoformat(pinned)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return datetime.now(zone)


def today(tz: ZoneInfo | None = None) -> date:
    return now(tz).date()


def iso(moment: datetime) -> str:
    return moment.isoformat()
