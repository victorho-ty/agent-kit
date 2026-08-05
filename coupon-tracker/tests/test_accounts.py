"""M0 + M0.5 — scaffold, accounts, and the isolation those depend on."""

from __future__ import annotations

import pytest

from coupon_tracker import accounts, config as config_mod, db
from coupon_tracker.errors import AccountError, ExitCode

from .conftest import add


def test_init_is_idempotent(tmp_path, cfg):
    first = db.open_migrated(cfg.db_path)
    applied_first = db.applied_migrations(first)
    second = db.open_migrated(cfg.db_path)
    assert db.applied_migrations(second) == applied_first
    assert db.migrate(second) == []


def test_foreign_keys_are_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_creating_an_account_provisions_its_directories(conn, cfg, now):
    account = accounts.create(conn, cfg, "Alice", now, telegram_user_id="1001")
    assert cfg.account_media_dir(account.id).is_dir()
    assert cfg.account_inbox_dir(account.id).is_dir()


def test_telegram_id_cannot_belong_to_two_accounts(conn, cfg, now):
    accounts.create(conn, cfg, "Alice", now, telegram_user_id="1001")
    with pytest.raises(AccountError) as exc:
        accounts.create(conn, cfg, "Impostor", now, telegram_user_id="1001")
    assert exc.value.exit_code is ExitCode.ERR_ACCOUNT


def test_rebinding_a_telegram_id_to_its_own_account_is_allowed(conn, cfg, now):
    account = accounts.create(conn, cfg, "Alice", now, telegram_user_id="1001")
    updated = accounts.update(conn, account.id, now, telegram_user_id="1001", display_name="Alicia")
    assert updated.display_name == "Alicia"


def test_unknown_account_id_creates_nothing(conn, cfg):
    with pytest.raises(AccountError):
        accounts.open_scope(conn, cfg, "01JNOTANACCOUNT")
    assert accounts.list_accounts(conn) == []


def test_resolve_telegram_returns_none_for_a_stranger(conn, cfg, now):
    accounts.create(conn, cfg, "Alice", now, telegram_user_id="1001")
    assert accounts.resolve_telegram(conn, "9999") is None


def test_scope_refuses_a_path_outside_its_directories(scope, tmp_path):
    with pytest.raises(AccountError):
        scope.assert_owns_path(tmp_path / "elsewhere.json")


def test_scope_refuses_a_traversal_relative_path(scope):
    with pytest.raises(AccountError):
        scope.media_path("../../escape.jpg")


def test_delete_dry_run_touches_nothing(scope, cfg, now, image):
    from coupon_tracker import store

    media = store.register_media(scope, image, now)
    add(scope, now, media_id=media.id)

    manifest = accounts.delete(scope.conn, cfg, scope.account_id, now, commit=False)

    assert manifest["dry_run"] is True
    assert manifest["coupons"]["total"] == 1
    assert accounts.get(scope.conn, scope.account_id) is not None
    assert (scope.media_dir / media.path).is_file()


def test_delete_commit_removes_rows_and_directories(scope, cfg, now, image):
    from coupon_tracker import store

    media = store.register_media(scope, image, now)
    add(scope, now, media_id=media.id)
    account_id = scope.account_id

    manifest = accounts.delete(scope.conn, cfg, account_id, now, commit=True)

    assert manifest["totals"]["files"] == 1
    assert accounts.get(scope.conn, account_id) is None
    assert scope.conn.execute(
        "SELECT COUNT(*) FROM coupon WHERE account_id = ?", (account_id,)
    ).fetchone()[0] == 0
    assert scope.conn.execute(
        "SELECT COUNT(*) FROM media WHERE account_id = ?", (account_id,)
    ).fetchone()[0] == 0
    assert not cfg.account_media_dir(account_id).exists()
    assert not cfg.account_inbox_dir(account_id).exists()


def test_deleting_one_account_leaves_the_other_intact(scope, other_scope, cfg, now, image):
    from coupon_tracker import store

    store.register_media(scope, image, now)
    kept_media = store.register_media(other_scope, image, now)
    add(scope, now)
    kept = add(other_scope, now, media_id=kept_media.id)

    accounts.delete(scope.conn, cfg, scope.account_id, now, commit=True)

    assert accounts.get(other_scope.conn, other_scope.account_id) is not None
    assert store.find(other_scope, kept.id) is not None
    assert (other_scope.media_dir / kept_media.path).is_file()


def test_config_rejects_unknown_keys(tmp_path):
    (tmp_path / "config.yaml").write_text("nonsense_key: 1\n", encoding="utf-8")
    with pytest.raises(Exception):
        config_mod.load(tmp_path / "config.yaml")


def test_allowlist_permits_only_listed_ids(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "accounts:\n  allowlist:\n    - 1001\n    - '2002'\n", encoding="utf-8"
    )
    loaded = config_mod.load(tmp_path / "config.yaml")
    assert loaded.accounts.permits("1001")
    assert loaded.accounts.permits(2002)
    assert not loaded.accounts.permits("9999")
