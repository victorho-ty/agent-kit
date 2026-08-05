"""M2 + M3 — the ingest contract and the lifecycle state machine."""

from __future__ import annotations

import pytest

from coupon_tracker import clock, lifecycle, query, store
from coupon_tracker.errors import (
    AccountError,
    ExitCode,
    IllegalTransitionError,
    NotFoundError,
    PredicateSchemaError,
)

from .conftest import add, write_candidates


def candidates_payload(scope, count=1, **overrides):
    candidate = {
        "merchant": "Cafe de Coral",
        "title": "$20 off",
        "expires_on": "2026-09-30",
        "expiry_precision": "exact",
        "expiry_assumed": False,
        "uses_total": 1,
        "conditions": [],
        "confidence": 0.95,
        **overrides,
    }
    return {
        "account_id": scope.account_id,
        "source": {"kind": "telegram_photo", "media_sha256": None, "raw_text": None},
        "coupon_count_stated": count,
        "candidates": [candidate],
    }


# -- ingest ------------------------------------------------------------------ #


def test_confident_candidate_becomes_active(scope, now):
    path = write_candidates(scope, candidates_payload(scope))
    result = store.commit_candidates(scope, path, now)

    assert result["totals"]["committed"] == 1
    assert result["committed"][0]["status"] == "active"


def test_low_confidence_routes_to_review(scope, now):
    path = write_candidates(scope, candidates_payload(scope, confidence=0.4))
    result = store.commit_candidates(scope, path, now)
    assert result["committed"][0]["status"] == "needs_review"
    assert any("confidence" in r for r in result["committed"][0]["review_reasons"])


def test_assumed_expiry_routes_to_review(scope, now):
    path = write_candidates(scope, candidates_payload(scope, expiry_assumed=True))
    result = store.commit_candidates(scope, path, now)
    assert result["committed"][0]["status"] == "needs_review"


def test_count_mismatch_routes_everything_to_review(scope, now):
    path = write_candidates(scope, candidates_payload(scope, count=3))
    result = store.commit_candidates(scope, path, now)

    assert result["count_mismatch"] is True
    assert all(c["status"] == "needs_review" for c in result["committed"])


def test_malformed_predicate_rejects_the_whole_file(scope, now):
    payload = candidates_payload(scope)
    payload["candidates"][0]["conditions"] = [{"kind": "invented", "params": None}]
    path = write_candidates(scope, payload)

    with pytest.raises(PredicateSchemaError):
        store.commit_candidates(scope, path, now)

    assert query.list_coupons(scope, query.FilterSpec(), now) == []


def test_dedupe_collision_within_an_account_routes_to_review(scope, now):
    add(scope, now)
    path = write_candidates(scope, candidates_payload(scope))
    result = store.commit_candidates(scope, path, now)

    assert result["committed"][0]["status"] == "needs_review"
    assert any("duplicate" in r for r in result["committed"][0]["review_reasons"])


def test_identical_coupon_in_another_account_is_not_a_collision(scope, other_scope, now):
    add(other_scope, now)
    path = write_candidates(scope, candidates_payload(scope))
    result = store.commit_candidates(scope, path, now)

    assert result["committed"][0]["status"] == "active"


def test_candidates_file_for_another_account_is_refused(scope, other_scope, now):
    payload = candidates_payload(scope)
    payload["account_id"] = other_scope.account_id
    path = write_candidates(scope, payload)

    with pytest.raises(AccountError) as exc:
        store.commit_candidates(scope, path, now)

    assert exc.value.exit_code is ExitCode.ERR_ACCOUNT
    assert query.list_coupons(scope, query.FilterSpec(), now) == []


def test_candidates_file_outside_the_account_inbox_is_refused(scope, other_scope, now, tmp_path):
    payload = candidates_payload(scope)
    stray = other_scope.inbox_dir / "sneaky.candidates.json"
    stray.write_text(__import__("json").dumps(payload), encoding="utf-8")

    with pytest.raises(AccountError):
        store.commit_candidates(scope, stray, now)

    assert query.list_coupons(scope, query.FilterSpec(), now) == []


def test_auto_confirm_overrides_review_routing(scope, now):
    path = write_candidates(scope, candidates_payload(scope, confidence=0.1))
    result = store.commit_candidates(scope, path, now, auto_confirm=True)
    assert result["committed"][0]["status"] == "active"


# -- lifecycle --------------------------------------------------------------- #


def test_use_twice_is_a_no_op_not_an_error(scope, now):
    coupon = add(scope, now)
    first = lifecycle.mark_used(scope, coupon.id, now)
    second = lifecycle.mark_used(scope, coupon.id, now)

    assert first.new_status == "used" and first.no_op is False
    assert second.no_op is True
    assert second.new_status == "used"


def test_multi_use_coupon_stays_active_until_consumed(scope, now):
    coupon = add(scope, now, uses_total=3, uses_remaining=3)

    assert lifecycle.mark_used(scope, coupon.id, now).new_status == "active"
    assert lifecycle.mark_used(scope, coupon.id, now).new_status == "active"
    third = lifecycle.mark_used(scope, coupon.id, now)

    assert third.new_status == "used"
    assert third.uses_remaining == 0


def test_unuse_restores_the_coupon(scope, now):
    coupon = add(scope, now, uses_total=2, uses_remaining=2)
    lifecycle.mark_used(scope, coupon.id, now)
    result = lifecycle.mark_unused(scope, coupon.id, now)

    assert result.new_status == "active"
    assert store.get(scope, coupon.id).uses_remaining == 2


def test_unuse_beyond_the_window_needs_force(scope, now):
    coupon = add(scope, now)
    lifecycle.mark_used(scope, coupon.id, now)
    much_later = clock.parse_datetime("2026-08-10T12:00")

    with pytest.raises(IllegalTransitionError):
        lifecycle.mark_unused(scope, coupon.id, much_later)

    assert lifecycle.mark_unused(scope, coupon.id, much_later, force=True).new_status == "active"


def test_extend_revives_an_expired_coupon_and_clears_alerts(scope, now):
    coupon = add(scope, now, expires_on="2026-08-01")
    lifecycle.sweep_expiry(scope, now)
    scope.conn.execute(
        "INSERT INTO alerts_sent (account_id, coupon_id, alert_kind, sent_at)"
        " VALUES (?, ?, 'pre_expiry', ?)",
        (scope.account_id, coupon.id, clock.iso(now)),
    )

    result = lifecycle.extend(scope, coupon.id, "2026-12-31", now)

    assert result.new_status == "active"
    assert store.get(scope, coupon.id).expires_on == "2026-12-31"
    assert scope.conn.execute(
        "SELECT COUNT(*) FROM alerts_sent WHERE coupon_id = ?", (coupon.id,)
    ).fetchone()[0] == 0


def test_illegal_transition_exit_code(scope, now):
    coupon = add(scope, now)
    lifecycle.mark_void(scope, coupon.id, "mis-scan", now)

    with pytest.raises(IllegalTransitionError) as exc:
        lifecycle.mark_used(scope, coupon.id, now)
    assert exc.value.exit_code is ExitCode.ERR_ILLEGAL_TRANSITION


def test_sweep_expiry_is_idempotent(scope, now):
    add(scope, now, expires_on="2026-08-01")
    first = lifecycle.sweep_expiry(scope, now)
    second = lifecycle.sweep_expiry(scope, now)

    assert len(first) == 1
    assert second == []


def test_sweep_leaves_the_other_account_untouched(scope, other_scope, now):
    add(scope, now, expires_on="2026-08-01")
    theirs = add(other_scope, now, expires_on="2026-08-01")

    lifecycle.sweep_expiry(scope, now)

    assert store.get(other_scope, theirs.id).status == "active"


def test_use_on_another_accounts_coupon_changes_nothing(scope, other_scope, now):
    theirs = add(other_scope, now)

    with pytest.raises(NotFoundError):
        lifecycle.mark_used(scope, theirs.id, now)

    assert store.get(other_scope, theirs.id).status == "active"
