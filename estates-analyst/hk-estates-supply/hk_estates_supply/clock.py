"""The one place that reads the wall clock.

Everything else is handed a time. That is what lets the tests assert on
"published, overdue, or merely quiet" without waiting a quarter for it.
"""

from __future__ import annotations

from datetime import date, datetime

from . import settings


def now() -> datetime:
    return datetime.now(tz=settings.timezone())


def today() -> date:
    return now().date()
