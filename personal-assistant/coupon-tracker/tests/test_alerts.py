"""M5 — which coupons are due, grouped so a group cannot be misaddressed.

Nothing here sends anything: Hermes owns the channel. These tests pin the
contract the cron entry and the agent rely on.
"""

from __future__ import annotations

from coupon_tracker import accounts, alerts, clock, lifecycle, store

from .conftest import add

TODAY = "2026-08-05"       # the frozen `now`
TOMORROW = "2026-08-06"
NEXT_WEEK = "2026-08-12"
YESTERDAY = "2026-08-04"


def run(cfg, conn, now, **kwargs):
    return alerts.run(cfg, conn, now, **kwargs)


def test_coupon_expiring_tomorrow_is_due(scope, cfg, now):
    add(scope, now, expires_on=TOMORROW)
    result = run(cfg, scope.conn, now)

    assert result["totals"]["coupons"] == 1
    assert result["groups"][0]["coupons"][0]["alert_kind"] == "pre_expiry"
    assert result["groups"][0]["coupons"][0]["days_left"] == 1


def test_coupon_expiring_today_is_due_with_expiry_day_kind(scope, cfg, now):
    add(scope, now, expires_on=TODAY)
    result = run(cfg, scope.conn, now)

    assert result["groups"][0]["coupons"][0]["alert_kind"] == "expiry_day"
    assert result["groups"][0]["coupons"][0]["days_left"] == 0


def test_coupon_beyond_the_window_is_not_due(scope, cfg, now):
    add(scope, now, expires_on=NEXT_WEEK)
    assert run(cfg, scope.conn, now)["totals"]["coupons"] == 0


def test_alert_days_before_widens_the_window(scope, cfg, now):
    import dataclasses

    add(scope, now, expires_on=NEXT_WEEK)
    assert run(cfg, scope.conn, now)["totals"]["coupons"] == 0

    wider = dataclasses.replace(cfg, alert_days_before=10)
    wider_scope = dataclasses.replace(scope, config=wider)
    assert alerts.due_for_account(wider_scope, now) != []


def test_used_and_review_coupons_are_never_due(scope, cfg, now):
    used = add(scope, now, expires_on=TOMORROW)
    lifecycle.mark_used(scope, used.id, now)
    add(scope, now, expires_on=TOMORROW, status="needs_review", title="unconfirmed")

    assert run(cfg, scope.conn, now)["totals"]["coupons"] == 0


def test_expiry_is_swept_before_deciding(scope, cfg, now):
    """A coupon that lapsed overnight is expired, not announced as due today."""
    lapsed = add(scope, now, expires_on=YESTERDAY)

    result = run(cfg, scope.conn, now, commit=True)

    assert result["totals"]["coupons"] == 0
    assert store.get(scope, lapsed.id).status == "expired"


def test_dry_run_does_not_persist_the_sweep(scope, cfg, now):
    lapsed = add(scope, now, expires_on=YESTERDAY)
    run(cfg, scope.conn, now, commit=False)
    assert store.get(scope, lapsed.id).status == "active"


# -- the isolation guarantee the cron entry depends on ----------------------- #


def test_each_account_gets_its_own_group_and_its_own_chat_id(scope, other_scope, cfg, now):
    add(scope, now, expires_on=TOMORROW, merchant="Alice's cafe")
    add(other_scope, now, expires_on=TOMORROW, merchant="Bob's diner")

    result = run(cfg, scope.conn, now)

    assert len(result["groups"]) == 2
    by_account = {g["account_id"]: g for g in result["groups"]}

    mine = by_account[scope.account_id]
    theirs = by_account[other_scope.account_id]

    assert mine["chat_id"] == "1001"
    assert theirs["chat_id"] == "2002"
    assert [c["merchant"] for c in mine["coupons"]] == ["Alice's cafe"]
    assert [c["merchant"] for c in theirs["coupons"]] == ["Bob's diner"]


def test_payload_has_no_flat_coupon_list_to_misaddress(scope, other_scope, cfg, now):
    """Every coupon is reachable only through the group carrying its chat_id."""
    add(scope, now, expires_on=TOMORROW)
    add(other_scope, now, expires_on=TOMORROW)

    result = run(cfg, scope.conn, now)

    assert "coupons" not in result
    assert all("chat_id" in group and group["chat_id"] for group in result["groups"])


def test_account_restriction_reports_only_that_account(scope, other_scope, cfg, now):
    add(scope, now, expires_on=TOMORROW)
    add(other_scope, now, expires_on=TOMORROW)

    result = run(cfg, scope.conn, now, account_id=scope.account_id)

    assert [g["account_id"] for g in result["groups"]] == [scope.account_id]


def test_account_without_a_chat_id_is_skipped_not_failed(conn, cfg, now):
    account = accounts.create(conn, cfg, "Unreachable", now, telegram_user_id="7007")
    scope = accounts.open_scope(conn, cfg, account.id)
    add(scope, now, expires_on=TOMORROW)

    result = alerts.run(cfg, conn, now)

    assert result["groups"] == []
    assert result["skipped"][0]["account_id"] == account.id
    assert result["skipped"][0]["due"] == 1
    assert result["failures"] == []


def test_one_broken_account_does_not_silence_the_others(scope, other_scope, cfg, now, monkeypatch):
    add(scope, now, expires_on=TOMORROW)
    add(other_scope, now, expires_on=TOMORROW)

    real = alerts.due_for_account

    def explode(target_scope, when, **kwargs):
        if target_scope.account_id == scope.account_id:
            raise RuntimeError("simulated corruption")
        return real(target_scope, when, **kwargs)

    monkeypatch.setattr(alerts, "due_for_account", explode)
    result = alerts.run(cfg, scope.conn, now)

    assert [g["account_id"] for g in result["groups"]] == [other_scope.account_id]
    assert result["failures"][0]["account_id"] == scope.account_id
    assert "simulated corruption" in result["failures"][0]["error"]


# -- no ledger --------------------------------------------------------------- #


def test_running_twice_reports_the_same_thing(scope, cfg, now):
    """No sent-ledger: due-ness is recomputed, so a re-run is not suppressed."""
    add(scope, now, expires_on=TOMORROW)

    first = run(cfg, scope.conn, now)
    second = run(cfg, scope.conn, now)

    assert first["totals"]["coupons"] == second["totals"]["coupons"] == 1


def test_a_missed_day_needs_no_catch_up(scope, cfg, now):
    """Machine off for days: the next run just reports what is due then."""
    add(scope, now, expires_on="2026-08-20")
    assert run(cfg, scope.conn, now)["totals"]["coupons"] == 0

    day_before = clock.parse_datetime("2026-08-19T08:00")
    later = alerts.run(cfg, scope.conn, day_before)
    assert later["totals"]["coupons"] == 1


def test_extending_a_coupon_moves_its_alert_without_any_invalidation(scope, cfg, now):
    coupon = add(scope, now, expires_on=TOMORROW)
    assert run(cfg, scope.conn, now)["totals"]["coupons"] == 1

    lifecycle.extend(scope, coupon.id, NEXT_WEEK, now)
    assert run(cfg, scope.conn, now)["totals"]["coupons"] == 0


def test_format_group_names_the_merchant_and_urgency(scope, cfg, now):
    add(scope, now, expires_on=TODAY, merchant="Cafe de Coral", value_text="$20 off")
    group = run(cfg, scope.conn, now)["groups"][0]

    body = alerts.format_group(group)

    assert "Cafe de Coral" in body
    assert "$20 off" in body
    assert "expires TODAY" in body


def test_format_group_surfaces_conditions_as_caveats(scope, cfg, now):
    add(
        scope,
        now,
        expires_on=TODAY,
        conditions=[{"kind": "channel", "params": {"allow": ["dine_in"]}, "text": "堂食限定"}],
    )
    body = alerts.format_group(run(cfg, scope.conn, now)["groups"][0])
    assert "堂食限定" in body
