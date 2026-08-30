from __future__ import annotations

from datetime import timedelta

import pytest

from quotes_drill import schedule, store
from quotes_drill.errors import NoEntriesError

from .conftest import add


def test_the_ladder_climbs_on_good_answers_and_falls_all_the_way_on_a_miss():
    assert schedule.next_state(0, 5) == (1, 2)
    assert schedule.next_state(1, 4) == (2, 4)
    assert schedule.next_state(4, 5) == (5, 32)
    assert schedule.next_state(5, 5) == (6, 32)  # the top rung is a ceiling
    # A 3 holds its rung: understood, not owned.
    assert schedule.next_state(3, 3) == (3, 8)
    assert schedule.next_state(4, 2) == (0, 1)


def test_never_drilled_entries_come_first_then_the_least_drilled(conn, now):
    fresh = add(conn, now, "a fair few")
    once = add(conn, now, "tuck in")
    twice = add(conn, now, "wolf it down")

    store.record_attempt(conn, now - timedelta(days=40), entry_id=once.id, score=5)
    store.record_attempt(conn, now - timedelta(days=41), entry_id=twice.id, score=5)
    store.record_attempt(conn, now - timedelta(days=39), entry_id=twice.id, score=5)

    picks, pool = schedule.pick(conn, now, count=3)

    assert [p.entry.id for p in picks] == [fresh.id, once.id, twice.id]
    assert picks[0].reason == "never_tested"
    assert picks[1].reason == "due"
    assert pool == {"active": 3, "due_now": 3, "never_tested": 1, "cooling": 0}


def test_two_entries_drilled_the_same_number_of_times_go_oldest_first(conn, now):
    recent = add(conn, now, "tuck in")
    stale = add(conn, now, "wolf it down")

    store.record_attempt(conn, now - timedelta(days=10), entry_id=recent.id, score=1)
    store.record_attempt(conn, now - timedelta(days=30), entry_id=stale.id, score=1)

    picks, _ = schedule.pick(conn, now, count=2)

    assert [p.entry.id for p in picks] == [stale.id, recent.id]


def test_an_entry_drilled_this_morning_is_held_back_until_the_cooldown_passes(conn, now):
    just_done = add(conn, now, "tuck in")
    waiting = add(conn, now, "a fair few")
    store.record_attempt(conn, now - timedelta(hours=2), entry_id=just_done.id, score=0)

    picks, pool = schedule.pick(conn, now, count=2, cooldown_hours=12)

    assert [p.entry.id for p in picks] == [waiting.id]
    assert pool["cooling"] == 1


def test_nothing_due_still_returns_an_entry_flagged_not_due(conn, now):
    entry = add(conn, now, "tuck in")
    store.record_attempt(conn, now - timedelta(days=1), entry_id=entry.id, score=5)

    picks, pool = schedule.pick(conn, now, count=1)

    assert picks[0].entry.id == entry.id
    assert picks[0].reason == "not_due"
    assert picks[0].due is False
    assert pool["due_now"] == 0


def test_a_category_filter_narrows_the_queue_and_an_empty_one_is_an_error(conn, now):
    add(conn, now, "tuck in", category="Food")
    add(conn, now, "ship it", category="Computer")

    picks, _ = schedule.pick(conn, now, count=5, category="food")
    assert [p.entry.text for p in picks] == ["tuck in"]

    with pytest.raises(NoEntriesError):
        schedule.pick(conn, now, category="Sports")
