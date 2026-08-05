from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from coupon_tracker import accounts, clock, config as config_mod, db, store

FROZEN = "2026-08-05T12:00:00+08:00"


@pytest.fixture
def now() -> datetime:
    return clock.parse_datetime(FROZEN)


@pytest.fixture
def cfg(tmp_path: Path):
    config_mod.write_default(tmp_path)
    loaded = config_mod.load(tmp_path / "config.yaml")
    loaded.ensure_dirs()
    return loaded


@pytest.fixture
def conn(cfg):
    connection = db.open_migrated(cfg.db_path)
    yield connection
    connection.close()


@pytest.fixture
def scope(conn, cfg, now):
    """A ready-to-use account: 'Alice'."""
    account = accounts.create(conn, cfg, "Alice", now, telegram_user_id="1001", chat_id="1001")
    return accounts.open_scope(conn, cfg, account.id)


@pytest.fixture
def other_scope(conn, cfg, now):
    """A second account, for every isolation assertion."""
    account = accounts.create(conn, cfg, "Bob", now, telegram_user_id="2002", chat_id="2002")
    return accounts.open_scope(conn, cfg, account.id)


@pytest.fixture
def image(tmp_path: Path) -> Path:
    path = tmp_path / "coupon.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0 pretend jpeg bytes")
    return path


def add(scope, now, **overrides):
    """Add a coupon with sane defaults, overriding what the test cares about."""
    defaults = {
        "merchant": "Cafe de Coral",
        "title": "$20 off",
        "expires_on": "2026-09-30",
    }
    return store.add_coupon(scope, now, **{**defaults, **overrides})


def write_candidates(scope, payload: dict, name: str = "item.candidates.json") -> Path:
    scope.ensure_dirs()
    path = scope.inbox_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
