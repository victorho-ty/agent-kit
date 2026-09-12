"""Storage: the time series, and the reported-baseline contract."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fed_watch import db, probabilities
from fed_watch.errors import DatabaseError


def _stamp(hour: int) -> datetime:
    return datetime(2026, 9, 12, hour, 0, tzinfo=timezone.utc)


def _store(conn, policy, quotes, month_has_meeting, hour: int):
    priced = probabilities.solve(
        policy, [date(2026, 9, 16), date(2026, 10, 28)], quotes, month_has_meeting
    )
    return db.store_snapshot(
        conn, _stamp(hour), policy, priced, "test", "ok", {}
    )


def test_snapshot_round_trips_with_its_meetings_and_outcomes(
    db_path, policy, quotes, month_has_meeting
):
    with db.connect() as conn:
        snapshot_id = _store(conn, policy, quotes, month_has_meeting, 9)
        stored = db.load_snapshot(conn, snapshot_id)

    assert stored["effr"] == policy.effr
    assert [m["meeting_date"] for m in stored["meetings"]] == ["2026-09-16", "2026-10-28"]
    assert stored["meetings"][1]["method"] == "clean_next_month"
    assert sum(o["probability"] for o in stored["meetings"][0]["outcomes"]) == pytest.approx(1.0)


def test_outcomes_come_back_ordered_by_step(db_path, policy, quotes, month_has_meeting):
    with db.connect() as conn:
        stored = db.load_snapshot(conn, _store(conn, policy, quotes, month_has_meeting, 9))
    steps = [o["step"] for o in stored["meetings"][1]["outcomes"]]
    assert steps == sorted(steps)


def test_baseline_is_the_last_reported_snapshot_not_the_last_poll(
    db_path, policy, quotes, month_has_meeting
):
    """The core of the drift design: quiet polls must not move the baseline."""
    with db.connect() as conn:
        first = _store(conn, policy, quotes, month_has_meeting, 9)
        db.mark_reported(conn, first, _stamp(9))
        _store(conn, policy, quotes, month_has_meeting, 10)  # a quiet poll
        _store(conn, policy, quotes, month_has_meeting, 11)  # another

        assert db.latest_reported(conn)["id"] == first
        assert db.count_snapshots(conn) == 3


def test_reported_history_is_oldest_first_and_capped(
    db_path, policy, quotes, month_has_meeting
):
    with db.connect() as conn:
        for hour in (9, 10, 11):
            db.mark_reported(conn, _store(conn, policy, quotes, month_has_meeting, hour), _stamp(hour))
        history = db.reported_history(conn, 2)

    assert [h["taken_at"] for h in history] == [_stamp(10).isoformat(), _stamp(11).isoformat()]


def test_a_future_schema_is_refused_rather_than_written_to(db_path, policy):
    with db.connect() as conn:
        conn.execute("UPDATE schema_version SET version = ?", (db.SCHEMA_VERSION + 1,))
    with pytest.raises(DatabaseError):
        with db.connect():
            pass
