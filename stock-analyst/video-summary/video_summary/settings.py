"""Runtime configuration, all overridable by environment variable.

*What* is watched is not here -- that lives in ``config/feeds.json`` so a new
channel never means a code change. This module only holds where things are
stored and how the client behaves.

Adapted from news-radar/news_radar/settings.py; the env prefix and the paths
differ, and there is one addition -- the transcript cache, which is a directory
of plain text files rather than a column, because a forty-minute video is forty
thousand characters and nobody wants that inside a JSON payload or a SQLite row
they might accidentally SELECT *.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_STATE_DIR = Path.home() / ".local" / "share" / "hermes-video-summary"
DEFAULT_DB_PATH = DEFAULT_STATE_DIR / "video_summary.db"
DEFAULT_TRANSCRIPT_DIR = DEFAULT_STATE_DIR / "transcripts"
DEFAULT_TZ = "Asia/Hong_Kong"
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
# Seconds between requests within one check. Conditional GET already makes a
# check of an unchanged feed nearly free; this is what keeps a burst of ten
# channels from arriving as ten simultaneous requests.
DEFAULT_DELAY_SECONDS = 2.0


def db_path() -> Path:
    return Path(os.environ.get("VIDEO_SUMMARY_DB", str(DEFAULT_DB_PATH))).expanduser()


def transcript_dir() -> Path:
    return Path(
        os.environ.get("VIDEO_SUMMARY_TRANSCRIPTS", str(DEFAULT_TRANSCRIPT_DIR))
    ).expanduser()


def timezone() -> ZoneInfo:
    """Fallback timezone. ``feeds.json`` may override it with its own key."""
    return ZoneInfo(os.environ.get("VIDEO_SUMMARY_TZ", DEFAULT_TZ))


def config_path() -> Path:
    from .config import feeds as _feeds

    return Path(os.environ.get("VIDEO_SUMMARY_CONFIG", str(_feeds.CONFIG_FILE))).expanduser()


def http_timeout() -> float:
    return float(os.environ.get("VIDEO_SUMMARY_TIMEOUT", DEFAULT_TIMEOUT))


def http_retries() -> int:
    return int(os.environ.get("VIDEO_SUMMARY_RETRIES", DEFAULT_RETRIES))


def request_delay() -> float | None:
    """Per-request pacing override; ``None`` means "use the value in feeds.json"."""
    raw = os.environ.get("VIDEO_SUMMARY_DELAY")
    return float(raw) if raw is not None else None


def proxy_url() -> str | None:
    """Optional proxy for caption fetching only.

    YouTube blocks caption requests from most cloud provider address space, and
    from any address that has asked too often. The feed itself is unaffected --
    it is a public document served from a different path -- so this proxy is
    wired into the transcript client and nowhere else. Unset is the normal case
    on a home connection.
    """
    return os.environ.get("VIDEO_SUMMARY_PROXY") or None
