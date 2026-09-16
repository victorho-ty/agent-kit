"""Runtime configuration, all overridable by environment variable.

*When* the FOMC meets is not here -- that lives in ``config/fomc.json`` so next
year's calendar never means a code change. This module only holds where things
are stored and how the clients behave.

**Paths are scoped to the profile, not to the bundle.** State lives under
``hermes-stock-analyst/`` alongside the stock-desk bundle's, so everything this
profile knows sits in one directory and nothing belonging to another profile can
read it.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

PROFILE_STATE_DIR = Path.home() / ".local" / "share" / "hermes-stock-analyst"
DEFAULT_DB_PATH = PROFILE_STATE_DIR / "fed_watch.db"
# Deliberately *not* the stock-desk bundle's `charts/`. That bundle sweeps every
# PNG in its own directory on a retention timer, and sharing one would have it
# delete these behind our back.
DEFAULT_CHART_DIR = PROFILE_STATE_DIR / "fedwatch-charts"
DEFAULT_TZ = "Asia/Hong_Kong"
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
DEFAULT_CHART_RETENTION_DAYS = 7
DEFAULT_CHART_ORIENTATION = "portrait"
# Percentage points a probability must move before anybody is told. This is the
# alerting gate: below it, `check-changes` stays quiet and wakes nothing.
# See changes.py for why the baseline does not move until something is actually
# reported -- that is what stops a slow drift hiding under this gate forever.
DEFAULT_CHANGE_THRESHOLD = 4.0
# How many changes a chart shows when the caller does not say.
DEFAULT_CHANGE_WINDOW = 10


def db_path() -> Path:
    return Path(os.environ.get("FED_WATCH_DB", str(DEFAULT_DB_PATH))).expanduser()


def chart_dir() -> Path:
    return Path(os.environ.get("FED_WATCH_CHART_DIR", str(DEFAULT_CHART_DIR))).expanduser()


def chart_orientation() -> str:
    """`portrait` or `landscape`. Portrait by default, and that is not cosmetic.

    The report is delivered to Telegram and read on a phone held upright. A
    landscape chart arrives there as a letterboxed strip a centimetre tall, in
    which ten plotted points are unreadable precisely when a probability swing
    is worth looking at.
    """
    value = os.environ.get("FED_WATCH_CHART_ORIENTATION", DEFAULT_CHART_ORIENTATION)
    return value if value in ("portrait", "landscape") else DEFAULT_CHART_ORIENTATION


def chart_retention_days() -> int:
    return int(os.environ.get("FED_WATCH_CHART_RETENTION", DEFAULT_CHART_RETENTION_DAYS))


def change_threshold() -> float:
    return float(os.environ.get("FED_WATCH_CHANGE_THRESHOLD", DEFAULT_CHANGE_THRESHOLD))


def timezone() -> ZoneInfo:
    """Fallback timezone for stamping snapshots.

    A missing zone is a closed error rather than a traceback. Windows and slim
    containers ship no system tz database, so this fails on exactly the machines
    where the cause is least obvious -- and the remedy is one package.
    """
    from zoneinfo import ZoneInfoNotFoundError

    from .errors import ConfigError

    name = os.environ.get("FED_WATCH_TZ", DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"no timezone data for {name}",
            timezone=name,
            remedy="install the tzdata package (it is a declared dependency; run uv sync)",
        ) from exc


def calendar_path() -> Path:
    from .config import fomc as _fomc

    return Path(os.environ.get("FED_WATCH_CALENDAR", str(_fomc.CALENDAR_FILE))).expanduser()


def http_timeout() -> float:
    return float(os.environ.get("FED_WATCH_TIMEOUT", DEFAULT_TIMEOUT))


def http_retries() -> int:
    return int(os.environ.get("FED_WATCH_RETRIES", DEFAULT_RETRIES))
