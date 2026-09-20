"""Runtime configuration, all overridable by environment variable.

*What* is watched is not here -- that lives in ``config/sources.json`` so a new
source or a recategorised one never means a code change. This module only holds
where things are stored and how the client behaves.

Adapted from education-radar/education_radar/settings.py; the env prefix and the
paths differ, nothing else does.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "hermes-news-radar" / "news_radar.db"
DEFAULT_TZ = "Asia/Hong_Kong"
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
# Seconds between requests within one scan. Conditional GET already makes a
# scan of an unchanged feed nearly free; this is what keeps a burst of ten
# sources from arriving as ten simultaneous requests.
DEFAULT_DELAY_SECONDS = 2.0


def db_path() -> Path:
    return Path(os.environ.get("NEWS_RADAR_DB", str(DEFAULT_DB_PATH))).expanduser()


def timezone() -> ZoneInfo:
    """Fallback timezone. ``sources.json`` may override it with its own key."""
    return ZoneInfo(os.environ.get("NEWS_RADAR_TZ", DEFAULT_TZ))


def config_path() -> Path:
    from .config import sources as _sources

    return Path(os.environ.get("NEWS_RADAR_CONFIG", str(_sources.CONFIG_FILE))).expanduser()


def http_timeout() -> float:
    return float(os.environ.get("NEWS_RADAR_TIMEOUT", DEFAULT_TIMEOUT))


def http_retries() -> int:
    return int(os.environ.get("NEWS_RADAR_RETRIES", DEFAULT_RETRIES))


def request_delay() -> float | None:
    """Per-request pacing override; ``None`` means "use the value in sources.json"."""
    raw = os.environ.get("NEWS_RADAR_DELAY")
    return float(raw) if raw is not None else None


def headless() -> bool:
    return os.environ.get("NEWS_RADAR_HEADLESS", "1") != "0"
