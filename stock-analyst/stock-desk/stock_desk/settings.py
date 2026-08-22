"""Runtime configuration, all overridable by environment variable.

*What* is watched is not here -- that lives in ``config/watchlist.json`` so a new
ticker or a changed horizon never means a code change. This module only holds
where things are stored and how the clients behave.

**Paths are scoped to the profile, not to the bundle.** State lives under
``hermes-stock-analyst/`` rather than ``hermes-stock-desk/``, so a second bundle
added to this profile later shares one state directory, and nothing belonging to
another profile can read it.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

PROFILE_STATE_DIR = Path.home() / ".local" / "share" / "hermes-stock-analyst"
DEFAULT_DB_PATH = PROFILE_STATE_DIR / "stock_desk.db"
DEFAULT_CHART_DIR = PROFILE_STATE_DIR / "charts"
DEFAULT_TZ = "Asia/Hong_Kong"
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
# Seconds between requests within one sync. Providers rate-limit, and a burst of
# twenty tickers arriving simultaneously is the reliable way to get throttled.
DEFAULT_DELAY_SECONDS = 1.5
# Charts accumulate one PNG per request forever otherwise. Swept on each brief.
DEFAULT_CHART_RETENTION_DAYS = 7
DEFAULT_CHART_ORIENTATION = "portrait"
# Cold-starting an MCP server through `uvx` resolves an environment before it
# runs anything, which on a cold cache is tens of seconds. The HTTP timeout is
# far too short to reuse here, and a spawn killed halfway looks like an outage.
DEFAULT_MCP_TIMEOUT = 120.0


def db_path() -> Path:
    return Path(os.environ.get("STOCK_DESK_DB", str(DEFAULT_DB_PATH))).expanduser()


def chart_dir() -> Path:
    return Path(os.environ.get("STOCK_DESK_CHART_DIR", str(DEFAULT_CHART_DIR))).expanduser()


def chart_orientation() -> str:
    """`portrait` or `landscape`. Portrait by default, and that is not cosmetic.

    The report is delivered to Telegram and read on a phone held upright. A
    landscape chart arrives there as a letterboxed strip a centimetre tall, in
    which a candle is roughly one pixel wide -- unreadable precisely when a
    breakout chart is worth looking at.
    """
    value = os.environ.get("STOCK_DESK_CHART_ORIENTATION", DEFAULT_CHART_ORIENTATION)
    return value if value in ("portrait", "landscape") else DEFAULT_CHART_ORIENTATION


def chart_retention_days() -> int:
    return int(os.environ.get("STOCK_DESK_CHART_RETENTION", DEFAULT_CHART_RETENTION_DAYS))


def timezone() -> ZoneInfo:
    """Fallback timezone. ``watchlist.json`` may override it with its own key.

    A missing zone is a closed error rather than a traceback. Windows and slim
    containers ship no system tz database, so this fails on exactly the machines
    where the cause is least obvious -- and the remedy is one package.
    """
    from zoneinfo import ZoneInfoNotFoundError

    from .errors import ConfigError

    name = os.environ.get("STOCK_DESK_TZ", DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"no timezone data for {name}",
            timezone=name,
            remedy="install the tzdata package (it is a declared dependency; run uv sync)",
        ) from exc


def config_path() -> Path:
    from .config import watchlist as _watchlist

    return Path(os.environ.get("STOCK_DESK_CONFIG", str(_watchlist.CONFIG_FILE))).expanduser()


def mcp_config_path() -> Path:
    """Where the MCP server definitions live.

    Defaults to the *profile* root -- the directory holding this bundle -- so the
    bundle and the interactive agent read the same `.mcp.json`, and a pin applied
    for one applies to both. That file is gitignored because it holds literal API
    keys; `.mcp.json.example` beside it carries the server definitions without
    them.
    """
    from .providers.mcp_client import BUNDLE_ROOT

    default = BUNDLE_ROOT.parent / ".mcp.json"
    return Path(os.environ.get("STOCK_DESK_MCP_CONFIG", str(default))).expanduser()


def mcp_timeout() -> float:
    return float(os.environ.get("STOCK_DESK_MCP_TIMEOUT", DEFAULT_MCP_TIMEOUT))


def http_timeout() -> float:
    return float(os.environ.get("STOCK_DESK_TIMEOUT", DEFAULT_TIMEOUT))


def http_retries() -> int:
    return int(os.environ.get("STOCK_DESK_RETRIES", DEFAULT_RETRIES))


def request_delay() -> float:
    return float(os.environ.get("STOCK_DESK_DELAY", DEFAULT_DELAY_SECONDS))
