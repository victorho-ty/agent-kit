"""Runtime configuration, all overridable by environment variable.

State is scoped to the *profile*, not to this bundle: every tool the
`english-coach` agent owns keeps its data under one directory, so a second
bundle in the same profile does not need a second backup rule.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_STATE_DIR = Path.home() / ".local" / "share" / "hermes-english-coach"
DEFAULT_DB_PATH = DEFAULT_STATE_DIR / "quotes_drill.db"
DEFAULT_TZ = "Asia/Hong_Kong"
# An entry drilled inside this window is held back even when it is otherwise
# next in the queue. Without it, a store of three entries hands back the same
# one twice in a morning.
DEFAULT_COOLDOWN_HOURS = 12.0


def db_path() -> Path:
    return Path(os.environ.get("QUOTES_DRILL_DB", str(DEFAULT_DB_PATH))).expanduser()


def styles_path() -> Path:
    from .config import styles as styles_config

    return Path(
        os.environ.get("QUOTES_DRILL_STYLES", str(styles_config.CONFIG_FILE))
    ).expanduser()


def timezone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("QUOTES_DRILL_TZ", DEFAULT_TZ))


def cooldown_hours() -> float:
    return float(os.environ.get("QUOTES_DRILL_COOLDOWN_HOURS", DEFAULT_COOLDOWN_HOURS))
