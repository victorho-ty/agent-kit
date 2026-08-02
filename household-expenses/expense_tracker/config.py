"""Runtime configuration, all overridable by environment variable."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "hermes-expenses" / "expenses.db"
DEFAULT_TZ = "Asia/Hong_Kong"
DEFAULT_CURRENCY = "HKD"


def db_path() -> Path:
    return Path(os.environ.get("HOUSEHOLD_EXPENSES_DB", str(DEFAULT_DB_PATH))).expanduser()


def timezone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("HOUSEHOLD_EXPENSES_TZ", DEFAULT_TZ))


def currency() -> str:
    return os.environ.get("HOUSEHOLD_EXPENSES_CURRENCY", DEFAULT_CURRENCY)


def report_dir() -> Path:
    return Path(os.environ.get("HOUSEHOLD_EXPENSES_REPORT_DIR", str(db_path().parent / "reports"))).expanduser()
