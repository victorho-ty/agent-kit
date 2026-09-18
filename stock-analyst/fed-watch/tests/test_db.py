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


# ------------------------------------------------- decided meetings leave the store


def _store_dates(conn, policy, quotes, month_has_meeting, hour: int, meetings: list[date]):
    priced = probabilities.solve(policy, meetings, quotes, month_has_meeting)
    return db.store_snapshot(conn, _stamp(hour), policy, priced, "test", "ok", {})


def _raw_counts(conn) -> tuple[int, int]:
    return (
        conn.execute("SELECT COUNT(*) AS n FROM meeting_snapshots").fetchone()["n"],
        conn.execute("SELECT COUNT(*) AS n FROM outcomes").fetchone()["n"],
    )


def test_a_decided_meeting_is_filtered_out_of_a_stored_snapshot(
    db_path, policy, quotes, month_has_meeting, monkeypatch
):
    """The bug this exists for: a snapshot replayed after the announcement."""
    with db.connect() as conn:
        snapshot_id = _store(conn, policy, quotes, month_has_meeting, 9)

    monkeypatch.setenv("FED_WATCH_NOW", "2026-09-18T09:00:00+08:00")
    with db.connect() as conn:
        stored = db.load_snapshot(conn, snapshot_id)

    assert [m["meeting_date"] for m in stored["meetings"]] == ["2026-10-28"]


def test_a_meeting_survives_its_own_decision_day(
    db_path, policy, quotes, month_has_meeting, monkeypatch
):
    """Announced in the afternoon; until then the futures are still pricing it."""
    with db.connect() as conn:
        snapshot_id = _store(conn, policy, quotes, month_has_meeting, 9)

    monkeypatch.setenv("FED_WATCH_NOW", "2026-09-16T09:00:00+08:00")
    with db.connect() as conn:
        stored = db.load_snapshot(conn, snapshot_id)

    assert [m["meeting_date"] for m in stored["meetings"]] == ["2026-09-16", "2026-10-28"]


def test_ordinals_are_renumbered_so_the_first_meeting_is_the_next_decision(
    db_path, policy, quotes, month_has_meeting, monkeypatch
):
    with db.connect() as conn:
        snapshot_id = _store(conn, policy, quotes, month_has_meeting, 9)

    monkeypatch.setenv("FED_WATCH_NOW", "2026-09-18T09:00:00+08:00")
    with db.connect() as conn:
        stored = db.load_snapshot(conn, snapshot_id)

    # Stored as ordinal 2 behind 2026-09-16; it is the next decision now.
    assert [m["ordinal"] for m in stored["meetings"]] == [1]


def test_purge_removes_the_rows_and_not_only_the_answer(
    db_path, policy, quotes, month_has_meeting
):
    with db.connect() as conn:
        _store(conn, policy, quotes, month_has_meeting, 9)
        _store(conn, policy, quotes, month_has_meeting, 10)
        meetings_before, outcomes_before = _raw_counts(conn)

        report = db.purge_past_meetings(conn, date(2026, 9, 18))
        meetings_after, outcomes_after = _raw_counts(conn)

    assert meetings_before == 4  # two snapshots, two meetings each
    assert report["meetings"] == 2
    assert report["cutoff"] == "2026-09-18"
    assert meetings_after == 2
    assert outcomes_after < outcomes_before
    assert report["outcomes"] == outcomes_before - outcomes_after


def test_purge_keeps_a_meeting_through_its_own_decision_day(
    db_path, policy, quotes, month_has_meeting
):
    with db.connect() as conn:
        _store(conn, policy, quotes, month_has_meeting, 9)
        report = db.purge_past_meetings(conn, date(2026, 9, 16))
        assert report["meetings"] == 0
        assert _raw_counts(conn)[0] == 2


def test_purge_leaves_the_snapshot_rows_and_their_reported_marks_alone(
    db_path, policy, quotes, month_has_meeting
):
    """Only meeting rows go. The reported baseline is not a casualty of tidying."""
    with db.connect() as conn:
        first = _store(conn, policy, quotes, month_has_meeting, 9)
        db.mark_reported(conn, first, _stamp(9))
        db.purge_past_meetings(conn, date(2026, 9, 18))

        assert db.count_snapshots(conn) == 1
        assert db.latest_reported(conn)["id"] == first


def test_a_decided_meeting_is_not_charted_from_stale_history(
    db_path, policy, quotes, month_has_meeting, monkeypatch
):
    """The reported window spans the announcement; the old meeting must not plot."""
    from fed_watch import changes

    with db.connect() as conn:
        for hour in (9, 10):
            db.mark_reported(
                conn, _store(conn, policy, quotes, month_has_meeting, hour), _stamp(hour)
            )

    monkeypatch.setenv("FED_WATCH_NOW", "2026-09-18T09:00:00+08:00")
    with db.connect() as conn:
        shaped = changes.series(db.reported_history(conn, 10))

    assert sorted(shaped) == ["2026-10-28"]
