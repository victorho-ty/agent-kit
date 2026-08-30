"""Runtime configuration, all overridable by environment variable.

Two storage locations, on purpose, and the split is the opposite way round from
`hk-estates-supply`:

* **The estate list lives in the bundle**, at ``config/estates.json``. It is
  hand-written intent -- which blocks, which layouts, which sizes -- so it is
  version-controlled and ships with the code.
* **The database lives under the profile state directory.** It is a growing
  archive of scraped transactions and a delivery ledger; keeping it out of the
  bundle means a redeploy never has to merge it, and never risks truncating it.
  The path is scoped to ``hermes-estates-analyst/`` rather than to this bundle,
  so it sits alongside the supply monitor's state and nothing belonging to
  another profile can read it.

The database is the only copy of transactions older than the newest hundred:
Centanet serves at most 100 records per estate and offers no way to page back
past them, so anything that scrolls off the end of that window survives here or
not at all. Nothing in this package ever deletes a row.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent / "config" / "estates.json"

PROFILE_STATE_DIR = Path.home() / ".local" / "share" / "hermes-estates-analyst"
DEFAULT_DB_FILE = PROFILE_STATE_DIR / "hk_transactions.db"
DEFAULT_IMAGE_DIR = PROFILE_STATE_DIR / "hk_tx_images"

DEFAULT_TZ = "Asia/Hong_Kong"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
DEFAULT_DELAY = 2.0

# Centanet's server-rendered list honours `size` and caps it at exactly 100:
# ask for 101 and the payload comes back with an empty transactionList rather
# than an error. No offset, page, skip or start parameter is honoured, so 100
# newest records per estate is the whole window and there is no way to page
# past it. See references/data-source.md.
MAX_FETCH_SIZE = 100
DEFAULT_FETCH_SIZE = MAX_FETCH_SIZE

# The trend compares the last N days against the N before them.
DEFAULT_TREND_WINDOW_DAYS = 90
# Below this many transactions in a window, a median is not a market level and
# a percentage between two of them is not a trend. Reported as insufficient.
DEFAULT_TREND_MIN_SAMPLES = 3
# Months of monthly medians behind a line chart, and the minimum number of
# months carrying data before one is worth drawing at all.
DEFAULT_CHART_MONTHS = 24
DEFAULT_CHART_MIN_POINTS = 3

# Rendered PNGs accumulate one set per report otherwise. Swept on each render.
DEFAULT_IMAGE_RETENTION_DAYS = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _number(name: str, default, cast, *, minimum=None, maximum=None):
    """An environment override parsed into a closed error rather than a traceback.

    ``HK_TX_TIMEOUT=thirty`` is a typo in a cron file, and the agent should read
    it as ERR_CONFIG on stdout with the variable named. A ValueError out of
    ``float()`` reaches it as an empty stdout and a stack trace on stderr, which
    looks like the command having died rather than having been misconfigured.
    """
    from .errors import ConfigError

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"{name} is not a number: {raw!r}",
            variable=name, value=raw, expected=cast.__name__,
        ) from exc
    if minimum is not None and value < minimum:
        raise ConfigError(
            f"{name} must be at least {minimum}, got {value}",
            variable=name, value=value, minimum=minimum,
        )
    if maximum is not None and value > maximum:
        raise ConfigError(
            f"{name} must be at most {maximum}, got {value}",
            variable=name, value=value, maximum=maximum,
        )
    return value


def db_path() -> Path:
    return Path(os.environ.get("HK_TX_DB", str(DEFAULT_DB_FILE))).expanduser()


def config_path() -> Path:
    return Path(os.environ.get("HK_TX_CONFIG", str(DEFAULT_CONFIG_FILE))).expanduser()


def image_dir() -> Path:
    return Path(os.environ.get("HK_TX_IMAGE_DIR", str(DEFAULT_IMAGE_DIR))).expanduser()


def http_timeout() -> float:
    return _number("HK_TX_TIMEOUT", DEFAULT_TIMEOUT, float, minimum=1.0)


def http_retries() -> int:
    return _number("HK_TX_RETRIES", DEFAULT_RETRIES, int, minimum=0)


def request_delay() -> float:
    """Seconds between estates. These are somebody else's servers."""
    return _number("HK_TX_DELAY", DEFAULT_DELAY, float, minimum=0.0)


def fetch_size() -> int:
    """Records asked for per estate. Clamped: 101 returns an empty list, silently."""
    return _number(
        "HK_TX_FETCH_SIZE", DEFAULT_FETCH_SIZE, int, minimum=1, maximum=MAX_FETCH_SIZE
    )


def image_retention_days() -> int:
    return _number("HK_TX_IMAGE_RETENTION", DEFAULT_IMAGE_RETENTION_DAYS, int, minimum=0)


def font_override() -> str | None:
    """A specific font family to draw Chinese labels with, when auto-detection fails."""
    name = os.environ.get("HK_TX_FONT", "").strip()
    return name or None


def timezone() -> ZoneInfo:
    """A missing zone is a closed error rather than a traceback.

    Windows and slim containers ship no system tz database, so this fails on
    exactly the machines where the cause is least obvious, and the remedy is one
    package.
    """
    from zoneinfo import ZoneInfoNotFoundError

    from .errors import ConfigError

    name = os.environ.get("HK_TX_TZ", DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"no timezone data for {name}",
            timezone=name,
            remedy="install the tzdata package (it is a declared dependency; run uv sync)",
        ) from exc
