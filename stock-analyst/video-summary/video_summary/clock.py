# Copied verbatim from news-radar/news_radar/clock.py (2026-08-23).
# It carries no skill-specific content; keep fixes in sync by hand.
"""The one place that reads the wall clock.

Every function that needs the time takes ``now`` as an argument; only the CLI
calls :func:`now` to produce it. That is what lets the tests run a whole check
at a fixed instant without freezing anything globally.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)
