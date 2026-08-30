from __future__ import annotations

from datetime import timedelta

import pytest

from quotes_drill import store
from quotes_drill.errors import NotFoundError, UsageError

from .conftest import add


def test_the_same_line_punctuated_differently_is_one_entry(conn, now):
    first, created = store.add_entry(
        conn, now, text="Don't cry over spilt milk.", category="Inspiration"
    )
    second, created_again = store.add_entry(
        conn, now, text="dont cry over spilt milk", category="Joke"
    )

    assert created is True
    assert created_again is False
    assert second.id == first.id
    # The first save wins: a re-add is a no-op, not a silent recategorisation.
    assert second.category == "Inspiration"


def test_an_entry_needs_a_category_and_a_known_kind(conn, now):
    with pytest.raises(UsageError):
        store.add_entry(conn, now, text="a fair few", category="  ")
    with pytest.raises(UsageError):
        store.add_entry(conn, now, text="a fair few", category="Food", kind="idiom")


def test_recording_an_attempt_advances_the_entry_and_keeps_the_answer(conn, now):
    entry = add(conn, now, "the flavour is unbelievable")

    updated, interval = store.record_attempt(
        conn,
        now,
        entry_id=entry.id,
        score=5,
        transcript="the flavour of this noodle soup is unbelievable",
        feedback="right register, wrong preposition",
        error_kind="grammar",
        style="Mark Wiens",
    )

    assert updated.times_tested == 1
    assert updated.last_tested_at == now.isoformat(timespec="seconds")
    assert updated.last_score == 5
    assert updated.streak == 1
    assert interval == 2
    assert updated.next_due_at == (now + timedelta(days=2)).isoformat(timespec="seconds")

    attempt = store.attempts_for(conn, entry.id)[0]
    assert attempt.transcript.startswith("the flavour of this noodle soup")
    assert attempt.error_kind == "grammar"
    assert attempt.style == "Mark Wiens"


def test_a_score_outside_the_band_is_refused(conn, now):
    entry = add(conn, now, "tuck in")
    with pytest.raises(UsageError):
        store.record_attempt(conn, now, entry_id=entry.id, score=7)
    with pytest.raises(NotFoundError):
        store.record_attempt(conn, now, entry_id=999, score=3)


def test_editing_text_onto_an_existing_entry_is_refused(conn, now):
    first = add(conn, now, "tuck in")
    second = add(conn, now, "wolf it down")

    with pytest.raises(UsageError):
        store.edit_entry(conn, now, second.id, text="Tuck in!")

    assert store.get_entry(conn, second.id).text == "wolf it down"
    assert store.get_entry(conn, first.id).text == "tuck in"


def test_retiring_takes_an_entry_out_of_the_active_list(conn, now):
    entry = add(conn, now, "tuck in")
    add(conn, now, "wolf it down")

    store.edit_entry(conn, now, entry.id, status="retired")

    assert [e.text for e in store.list_entries(conn)] == ["wolf it down"]
    assert len(store.list_entries(conn, status=None)) == 2
