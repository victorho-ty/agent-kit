"""CLI surface: account resolution, exit codes, and the JSON contract."""

from __future__ import annotations

import json

import pytest

from coupon_tracker import accounts, cli
from coupon_tracker.errors import ExitCode

from .conftest import FROZEN, add


def run(capsys, *argv, config=None, expect=ExitCode.OK):
    """Invoke the CLI as the agent would, always in --json mode."""
    args = ["--json", "--now", FROZEN]
    if config is not None:
        args += ["--config", str(config)]
    args += [str(a) for a in argv]

    code = cli.main(args)
    captured = capsys.readouterr()
    assert code == int(expect), f"expected {expect.name}, got {code}\n{captured.out}{captured.err}"
    return json.loads(captured.out) if captured.out.strip() else {}


@pytest.fixture
def root(cfg):
    return cfg.source


def test_init_is_idempotent(tmp_path, capsys):
    target = tmp_path / "skill"
    first = run(capsys, "init", "--root", str(target))
    second = run(capsys, "init", "--root", str(target), config=target / "config.yaml")

    assert first["ok"] and second["ok"]
    assert first["migrations_applied"] == ["001_initial.sql"]
    assert second["migrations_applied"] == []


def test_global_flags_work_after_the_subcommand(root, scope, capsys):
    """`couponctl list --account X --json` must parse as well as the other order."""
    code = cli.main(["list", "--account", scope.account_id, "--config", str(root), "--json"])
    captured = capsys.readouterr()
    assert code == int(ExitCode.OK)
    assert json.loads(captured.out)["account_id"] == scope.account_id


def test_scoped_command_without_an_account_exits_12(root, capsys, monkeypatch):
    monkeypatch.delenv(cli.ACCOUNT_ENV, raising=False)
    payload = run(capsys, "list", config=root, expect=ExitCode.ERR_ACCOUNT)
    assert payload["code"] == "ERR_ACCOUNT"


def test_unknown_account_exits_12(root, capsys):
    payload = run(capsys, "list", "--account", "01JNOPE", config=root, expect=ExitCode.ERR_ACCOUNT)
    assert payload["code"] == "ERR_ACCOUNT"


def test_account_env_var_is_honoured(root, scope, capsys, monkeypatch):
    monkeypatch.setenv(cli.ACCOUNT_ENV, scope.account_id)
    payload = run(capsys, "list", config=root)
    assert payload["account_id"] == scope.account_id


def test_flag_beats_env_var(root, scope, other_scope, capsys, monkeypatch):
    monkeypatch.setenv(cli.ACCOUNT_ENV, other_scope.account_id)
    payload = run(capsys, "list", "--account", scope.account_id, config=root)
    assert payload["account_id"] == scope.account_id


def test_resolution_by_telegram_user(root, scope, capsys):
    payload = run(capsys, "list", "--telegram-user", "1001", config=root)
    assert payload["account_id"] == scope.account_id


def test_unbound_telegram_user_exits_12(root, scope, capsys):
    run(capsys, "list", "--telegram-user", "9999", config=root, expect=ExitCode.ERR_ACCOUNT)


def test_add_then_show(root, scope, capsys):
    added = run(
        capsys,
        "add",
        "--account", scope.account_id,
        "--merchant", "Cafe de Coral",
        "--title", "$20 off",
        "--expires-on", "2026-09-30",
        config=root,
    )
    coupon_id = added["coupon"]["id"]
    shown = run(capsys, "show", coupon_id, "--account", scope.account_id, config=root)
    assert shown["coupon"]["merchant"] == "Cafe de Coral"


def test_show_of_another_accounts_coupon_exits_30(root, scope, other_scope, now, capsys):
    theirs = add(other_scope, now)
    run(
        capsys,
        "show", theirs.id, "--account", scope.account_id,
        config=root,
        expect=ExitCode.ERR_NOT_FOUND,
    )


def test_use_twice_exits_zero_both_times(root, scope, now, capsys):
    coupon = add(scope, now)
    first = run(capsys, "use", coupon.id, "--account", scope.account_id, config=root)
    second = run(capsys, "use", coupon.id, "--account", scope.account_id, config=root)
    assert first["no_op"] is False
    assert second["no_op"] is True


def test_purge_dry_run_is_the_default(root, scope, now, capsys):
    coupon = add(scope, now)
    run(capsys, "use", coupon.id, "--account", scope.account_id, config=root)

    payload = run(capsys, "purge", "--account", scope.account_id, config=root)

    assert payload["dry_run"] is True
    assert payload["totals"]["coupons"] == 1
    from coupon_tracker import store
    assert store.find(scope, coupon.id) is not None


def test_purge_without_an_account_exits_12(root, scope, capsys, monkeypatch):
    monkeypatch.delenv(cli.ACCOUNT_ENV, raising=False)
    run(capsys, "purge", "--commit", config=root, expect=ExitCode.ERR_ACCOUNT)


def test_purge_with_an_orphan_file_exits_50(root, scope, now, capsys):
    scope.ensure_dirs()
    (scope.media_dir / "stray.jpg").write_bytes(b"untracked")
    run(
        capsys,
        "purge", "--commit", "--account", scope.account_id,
        config=root,
        expect=ExitCode.ERR_PURGE_UNSAFE,
    )


def test_account_add_and_list(root, capsys):
    created = run(capsys, "account", "add", "--name", "Carol", "--telegram-user", "3003", config=root)
    listed = run(capsys, "account", "list", config=root)
    assert created["id"] in [a["id"] for a in listed["accounts"]]


def test_duplicate_telegram_id_exits_12(root, scope, capsys):
    run(
        capsys,
        "account", "add", "--name", "Impostor", "--telegram-user", "1001",
        config=root,
        expect=ExitCode.ERR_ACCOUNT,
    )


def test_doctor_is_clean(root, scope, capsys):
    payload = run(capsys, "doctor", config=root)
    assert payload["clean"] is True


def test_doctor_reports_a_stray_media_directory(root, cfg, scope, capsys):
    (cfg.media_dir / "01JNOTANACCOUNT").mkdir(parents=True, exist_ok=True)
    payload = run(capsys, "doctor", config=root, expect=ExitCode.ERR_DB)
    assert any(p["kind"] == "stray_media_dir" for p in payload["problems"])


def test_account_delete_dry_run_then_commit(root, scope, now, capsys):
    add(scope, now)
    dry = run(capsys, "account", "delete", "--account", scope.account_id, config=root)
    assert dry["dry_run"] is True

    wet = run(capsys, "account", "delete", "--account", scope.account_id, "--commit", config=root)
    assert wet["dry_run"] is False
    assert accounts.get(scope.conn, scope.account_id) is None
