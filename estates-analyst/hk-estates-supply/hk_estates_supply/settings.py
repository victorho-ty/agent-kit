"""Runtime configuration, all overridable by environment variable.

Two storage locations, on purpose:

* **The history CSV lives in the bundle**, at ``data/hk_units_supply_history.csv``.
  It is the durable artefact -- eighteen quarters of figures that exist nowhere
  else once the Housing Bureau moves a PDF into its archive -- so it ships with
  the bundle, is under version control, and is the one file worth backing up.
* **Everything else lives under the profile state directory.** The delivery
  ledger, the run log and the rendered PNGs are all reconstructible or
  disposable, and keeping them out of the bundle means a redeploy never has to
  merge them. Paths are scoped to ``hermes-estates-analyst/`` rather than to this
  bundle, so a second bundle added to the profile later shares one state
  directory and nothing belonging to another profile can read it.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY_FILE = BUNDLE_ROOT / "data" / "hk_units_supply_history.csv"

PROFILE_STATE_DIR = Path.home() / ".local" / "share" / "hermes-estates-analyst"
DEFAULT_STATE_FILE = PROFILE_STATE_DIR / "hk_supply_state.json"
DEFAULT_RUNS_FILE = PROFILE_STATE_DIR / "hk_supply_runs.jsonl"
DEFAULT_IMAGE_DIR = PROFILE_STATE_DIR / "hk_supply_images"

INDEX_URL = "https://www.hb.gov.hk/tc/publications/housing/private/pshpm/index.html"
# The PDF href on the index page is relative, so it resolves against this.
PDF_BASE_URL = "https://www.hb.gov.hk/tc/publications/housing/private/pshpm/"

DEFAULT_TZ = "Asia/Hong_Kong"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
# Quarters shown in the report table. Twelve is three years -- enough to see a
# cycle, few enough to stay legible on a phone.
DEFAULT_TABLE_QUARTERS = 12
# Rendered PNGs accumulate one set per report otherwise. Swept on each render.
DEFAULT_IMAGE_RETENTION_DAYS = 30
# Kept lines in the run log. A daily job writes 365 rows a year and only the
# recent ones answer "is this thing still running".
RUN_LOG_LINES = 200


def _number(name: str, default, cast, *, minimum=None):
    """An environment override parsed into a closed error rather than a traceback.

    ``HK_SUPPLY_QUARTERS=twelve`` is a typo in a cron file, and the agent should
    read it as ERR_CONFIG on stdout with the variable named. A ValueError out of
    ``int()`` reaches it as an empty stdout and a stack trace on stderr, which
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
    return value


def history_file() -> Path:
    return Path(os.environ.get("HK_SUPPLY_HISTORY", str(DEFAULT_HISTORY_FILE))).expanduser()


def state_file() -> Path:
    return Path(os.environ.get("HK_SUPPLY_STATE", str(DEFAULT_STATE_FILE))).expanduser()


def runs_file() -> Path:
    return Path(os.environ.get("HK_SUPPLY_RUNS", str(DEFAULT_RUNS_FILE))).expanduser()


def image_dir() -> Path:
    return Path(os.environ.get("HK_SUPPLY_IMAGE_DIR", str(DEFAULT_IMAGE_DIR))).expanduser()


def index_url() -> str:
    return os.environ.get("HK_SUPPLY_INDEX_URL", INDEX_URL)


def pdf_base_url() -> str:
    return os.environ.get("HK_SUPPLY_PDF_BASE_URL", PDF_BASE_URL)


def http_timeout() -> float:
    return _number("HK_SUPPLY_TIMEOUT", DEFAULT_TIMEOUT, float, minimum=1.0)


def http_retries() -> int:
    return _number("HK_SUPPLY_RETRIES", DEFAULT_RETRIES, int, minimum=0)


def table_quarters() -> int:
    return _number("HK_SUPPLY_QUARTERS", DEFAULT_TABLE_QUARTERS, int, minimum=1)


def image_retention_days() -> int:
    return _number("HK_SUPPLY_IMAGE_RETENTION", DEFAULT_IMAGE_RETENTION_DAYS, int, minimum=0)


def font_override() -> str | None:
    """A specific font family to draw Chinese labels with, when auto-detection fails."""
    name = os.environ.get("HK_SUPPLY_FONT", "").strip()
    return name or None


def timezone() -> ZoneInfo:
    """A missing zone is a closed error rather than a traceback.

    Windows and slim containers ship no system tz database, so this fails on
    exactly the machines where the cause is least obvious, and the remedy is one
    package.
    """
    from zoneinfo import ZoneInfoNotFoundError

    from .errors import ConfigError

    name = os.environ.get("HK_SUPPLY_TZ", DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"no timezone data for {name}",
            timezone=name,
            remedy="install the tzdata package (it is a declared dependency; run uv sync)",
        ) from exc
