"""Shared fixtures. No network, and no wall clock: every function that needs
the time is handed it, so a drill scheduled a month out can be tested in a
millisecond.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quotes_drill import db as db_module, store  # noqa: E402

TZ = ZoneInfo("Asia/Hong_Kong")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 30, 9, 0, tzinfo=TZ)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "quotes_drill.db"


@pytest.fixture
def conn(db_path):
    connection = db_module.connect(db_path)
    yield connection
    connection.close()


def add(conn, now, text: str, category: str = "Food", **overrides):
    entry, _ = store.add_entry(conn, now, text=text, category=category, **overrides)
    return entry
