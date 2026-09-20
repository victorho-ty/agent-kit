"""M1 — store, predicates, query, and their isolation guarantees."""

from __future__ import annotations

import pytest

from coupon_tracker import clock, predicates, query, store
from coupon_tracker.errors import ExitCode, NotFoundError, PredicateSchemaError
from coupon_tracker.models import Predicate
from coupon_tracker.predicates import EvalContext, Verdict

from .conftest import add


# -- predicates -------------------------------------------------------------- #


def test_unknown_predicate_kind_is_rejected():
    with pytest.raises(PredicateSchemaError) as exc:
        predicates.validate([{"kind": "phase_of_moon", "params": None}])
    assert exc.value.exit_code is ExitCode.ERR_PREDICATE_SCHEMA


def test_malformed_params_for_a_known_kind_are_rejected():
    with pytest.raises(PredicateSchemaError):
        predicates.validate([{"kind": "channel", "params": {"allow": ["teleport"]}}])
    with pytest.raises(PredicateSchemaError):
        predicates.validate([{"kind": "time_window", "params": {"from": "25 o'clock"}}])


def test_valid_conditions_round_trip():
    validated = predicates.validate(
        [{"kind": "channel", "params": {"allow": ["dine_in"]}, "text": "堂食限定"}]
    )
    assert validated[0].kind == "channel"
    assert validated[0].text == "堂食限定"


def test_unknown_verdict_never_excludes(now):
    only_advisory = [Predicate("min_spend", {"amount": 200, "ccy": "HKD"}, "滿$200")]
    usable, caveats = predicates.evaluate_all(only_advisory, EvalContext(at=now))
    assert usable is True
    assert len(caveats) == 1


def test_missing_context_is_unknown_not_fail(now):
    channel_only = [Predicate("channel", {"allow": ["dine_in"]}, None)]
    usable, caveats = predicates.evaluate_all(channel_only, EvalContext(at=now))
    assert usable is True and len(caveats) == 1


def test_wrong_channel_fails(now):
    p = Predicate("channel", {"allow": ["dine_in"]}, None)
    assert predicates.evaluate(p, EvalContext(at=now, channel="delivery")) is Verdict.FAIL
    assert predicates.evaluate(p, EvalContext(at=now, channel="dine_in")) is Verdict.PASS


def test_time_window_boundary():
    lunch = Predicate("time_window", {"days": [0, 1, 2, 3, 4], "from": "11:30", "to": "14:30"}, None)
    inside = clock.parse_datetime("2026-08-05T14:30")   # Wednesday
    outside = clock.parse_datetime("2026-08-05T14:31")
    weekend = clock.parse_datetime("2026-08-08T12:00")  # Saturday

    assert predicates.evaluate(lunch, EvalContext(at=inside)) is Verdict.PASS
    assert predicates.evaluate(lunch, EvalContext(at=outside)) is Verdict.FAIL
    assert predicates.evaluate(lunch, EvalContext(at=weekend)) is Verdict.FAIL


def test_time_window_wrapping_past_midnight():
    late = Predicate("time_window", {"from": "22:00", "to": "02:00"}, None)
    assert predicates.evaluate(late, EvalContext(at=clock.parse_datetime("2026-08-05T23:30"))) is Verdict.PASS
    assert predicates.evaluate(late, EvalContext(at=clock.parse_datetime("2026-08-05T01:30"))) is Verdict.PASS
    assert predicates.evaluate(late, EvalContext(at=clock.parse_datetime("2026-08-05T12:00"))) is Verdict.FAIL


# -- store ------------------------------------------------------------------- #


def test_dedupe_key_normalizes_merchant_and_title():
    assert store.dedupe_key("Cafe  DE  Coral", "$20 OFF", "2026-09-30") == store.dedupe_key(
        "cafe de coral", "$20 off", "2026-09-30"
    )


def test_same_image_registered_twice_makes_one_media_row(scope, now, image):
    first = store.register_media(scope, image, now)
    second = store.register_media(scope, image, now)
    assert first.id == second.id
    assert scope.conn.execute(
        "SELECT COUNT(*) FROM media WHERE account_id = ?", (scope.account_id,)
    ).fetchone()[0] == 1


def test_same_image_in_two_accounts_makes_two_rows_and_two_files(scope, other_scope, now, image):
    mine = store.register_media(scope, image, now)
    theirs = store.register_media(other_scope, image, now)

    assert mine.id != theirs.id
    assert mine.sha256 == theirs.sha256
    assert (scope.media_dir / mine.path).is_file()
    assert (other_scope.media_dir / theirs.path).is_file()
    assert (scope.media_dir / mine.path) != (other_scope.media_dir / theirs.path)


def test_media_lands_inside_the_owning_account_directory(scope, now, image):
    media = store.register_media(scope, image, now)
    resolved = (scope.media_dir / media.path).resolve()
    assert resolved.is_relative_to(scope.media_dir.resolve())
    assert scope.account_id in resolved.parts


# -- isolation --------------------------------------------------------------- #


def test_get_refuses_another_accounts_coupon(scope, other_scope, now):
    theirs = add(other_scope, now)
    with pytest.raises(NotFoundError) as exc:
        store.get(scope, theirs.id)
    assert exc.value.exit_code is ExitCode.ERR_NOT_FOUND


def test_foreign_id_and_nonexistent_id_are_indistinguishable(scope, other_scope, now):
    theirs = add(other_scope, now)
    with pytest.raises(NotFoundError) as foreign:
        store.get(scope, theirs.id)
    with pytest.raises(NotFoundError) as absent:
        store.get(scope, "01JDOESNOTEXIST0000000000")

    assert foreign.value.payload()["code"] == absent.value.payload()["code"]
    assert foreign.value.payload()["message"].replace(theirs.id, "X") == absent.value.payload()[
        "message"
    ].replace("01JDOESNOTEXIST0000000000", "X")


def test_list_returns_only_the_scoped_account(scope, other_scope, now):
    mine = add(scope, now, merchant="Mine")
    add(other_scope, now, merchant="Theirs")

    rows = query.list_coupons(scope, query.FilterSpec(), now)
    assert [c.id for c in rows] == [mine.id]


def test_usable_now_returns_only_the_scoped_account(scope, other_scope, now):
    add(scope, now, merchant="Mine")
    add(other_scope, now, merchant="Theirs")

    results = query.usable_now(scope, now)
    assert [r.coupon.merchant for r in results] == ["Mine"]


def test_identical_coupons_in_two_accounts_are_not_duplicates(scope, other_scope, now):
    mine = add(scope, now)
    theirs = add(other_scope, now)

    assert mine.dedupe_key == theirs.dedupe_key
    assert store.find_duplicate(scope, theirs.dedupe_key).id == mine.id
    assert store.find_duplicate(other_scope, mine.dedupe_key).id == theirs.id


def test_coupon_cannot_reference_another_accounts_media(scope, other_scope, now, image):
    theirs = store.register_media(other_scope, image, now)
    with pytest.raises(Exception) as exc:
        add(scope, now, media_id=theirs.id)
    assert exc.value.exit_code is ExitCode.ERR_ACCOUNT


# -- query ------------------------------------------------------------------- #


def test_advisory_only_coupon_is_always_returned_with_a_caveat(scope, now):
    """The caveat keeps the original source text — zh-Hant stays zh-Hant."""
    add(scope, now, conditions=[{"kind": "min_spend", "params": {"amount": 200}, "text": "滿$200"}])
    results = query.usable_now(scope, now)
    assert len(results) == 1
    assert results[0].to_dict()["caveats"] == ["滿$200"]


def test_caveat_falls_back_to_a_generated_description(scope, now):
    """With no source text, describe() synthesises one rather than showing the kind."""
    add(scope, now, conditions=[{"kind": "min_spend", "params": {"amount": 200, "ccy": "HKD"}}])
    results = query.usable_now(scope, now)
    assert results[0].to_dict()["caveats"] == ["min spend HKD 200"]


def test_usable_now_excludes_a_failing_time_window(scope, now):
    add(
        scope,
        now,
        conditions=[{"kind": "time_window", "params": {"from": "11:30", "to": "14:30"}, "text": "午市"}],
    )
    at_lunch = clock.parse_datetime("2026-08-05T12:00")
    at_dinner = clock.parse_datetime("2026-08-05T19:30")

    assert len(query.usable_now(scope, now, at=at_lunch)) == 1
    assert query.usable_now(scope, now, at=at_dinner) == []


def test_usable_now_excludes_expired_coupons(scope, now):
    add(scope, now, expires_on="2026-08-04")
    assert query.usable_now(scope, now) == []


def test_expiring_within_filter(scope, now):
    soon = add(scope, now, expires_on="2026-08-07", title="soon")
    add(scope, now, expires_on="2026-12-31", title="later")

    rows = query.list_coupons(scope, query.FilterSpec(expiring_within_days=7), now)
    assert [c.id for c in rows] == [soon.id]
