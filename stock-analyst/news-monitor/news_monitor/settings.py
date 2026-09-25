"""Runtime configuration, all overridable by environment variable.

*What* is watched is not here, and it is not in a file either -- it is the
``feed`` table, because ``add`` writes sources at run time and a config file the
tools also wrote would be a second truth that drifts. ``config/seeds.json`` only
bootstraps an empty database.

**Paths are scoped to the profile, not to the bundle.** State lives under
``hermes-stock-analyst/`` alongside the stock-desk and fed-watch databases, so
everything this profile knows sits in one directory and nothing belonging to
another profile can read it.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

PROFILE_STATE_DIR = Path.home() / ".local" / "share" / "hermes-stock-analyst"
DEFAULT_DB_PATH = PROFILE_STATE_DIR / "news_monitor.db"
DEFAULT_TZ = "Asia/Hong_Kong"
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3

# Seconds between requests inside one check. Conditional GET already makes an
# unchanged feed nearly free; this is what stops thirty feeds arriving at their
# publishers as thirty simultaneous requests.
DEFAULT_DELAY_SECONDS = 1.0
# Entries read from the top of each feed. A wire feed carries a few dozen; going
# deeper only re-reads items the ledger has already seen.
DEFAULT_MAX_ITEMS = 40
# Items one `check` hands over at once. The rest stay pending -- the ledger
# loses nothing, and a backlog drains over the following runs rather than
# arriving as one unreadable wall.
DEFAULT_MAX_PER_CHECK = 40
# Characters of the feed's own summary paragraph carried into the payload. This
# is the brief header the agent writes its one or two lines from; it is not an
# article, and the whole point of the link is that the article is elsewhere.
DEFAULT_SUMMARY_CHAR_CAP = 600

# The gate a feed offered to `add` must clear to be enabled without review.
# See gate.py for what each one actually rejects.
DEFAULT_GATE_MIN_ITEMS = 3
DEFAULT_GATE_MAX_AGE_DAYS = 30
DEFAULT_GATE_MIN_FINANCE_HITS = 3


def db_path() -> Path:
    return Path(os.environ.get("NEWS_MONITOR_DB", str(DEFAULT_DB_PATH))).expanduser()


def timezone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("NEWS_MONITOR_TZ", DEFAULT_TZ))


def seeds_path() -> Path:
    from .config import seeds as _seeds

    return Path(os.environ.get("NEWS_MONITOR_SEEDS", str(_seeds.SEEDS_FILE))).expanduser()


def taxonomy_path() -> Path:
    from .config import taxonomy as _taxonomy

    return Path(os.environ.get("NEWS_MONITOR_TAXONOMY", str(_taxonomy.TAXONOMY_FILE))).expanduser()


def http_timeout() -> float:
    return float(os.environ.get("NEWS_MONITOR_TIMEOUT", DEFAULT_TIMEOUT))


def http_retries() -> int:
    return int(os.environ.get("NEWS_MONITOR_RETRIES", DEFAULT_RETRIES))


def request_delay() -> float:
    return float(os.environ.get("NEWS_MONITOR_DELAY", DEFAULT_DELAY_SECONDS))


def contact() -> str | None:
    """A contact address to put in the User-Agent, or ``None``.

    Optional, and nothing seeded needs it. It exists because the operator can
    ``add`` a source at any time: US government data hosts publish an access policy
    requiring an identifiable requester, and **bls.gov answers 403 to any
    User-Agent with no email address in it** -- including a browser's -- while
    serving the feed immediately with one. Measured, not assumed.

    So a ``.gov`` feed added later fails with an opaque 403 and no remedy unless
    this knob exists. federalreserve.gov currently does not check, and may.

    Unset by default and read from the environment rather than shipped: the
    address belongs to whoever is running this, and a placeholder baked into the
    repo would be someone else's address on a request they did not make.
    """
    return os.environ.get("NEWS_MONITOR_CONTACT") or None


def max_items() -> int:
    return int(os.environ.get("NEWS_MONITOR_MAX_ITEMS", DEFAULT_MAX_ITEMS))


def max_per_check() -> int:
    return int(os.environ.get("NEWS_MONITOR_MAX_PER_CHECK", DEFAULT_MAX_PER_CHECK))


def summary_char_cap() -> int:
    return int(os.environ.get("NEWS_MONITOR_SUMMARY_CAP", DEFAULT_SUMMARY_CHAR_CAP))


def gate() -> dict:
    return {
        "min_items": int(os.environ.get("NEWS_MONITOR_GATE_MIN_ITEMS", DEFAULT_GATE_MIN_ITEMS)),
        "max_age_days": int(
            os.environ.get("NEWS_MONITOR_GATE_MAX_AGE_DAYS", DEFAULT_GATE_MAX_AGE_DAYS)
        ),
        "min_finance_hits": int(
            os.environ.get("NEWS_MONITOR_GATE_MIN_HITS", DEFAULT_GATE_MIN_FINANCE_HITS)
        ),
    }
