"""The FOMC calendar, and the contract symbols it selects."""

from __future__ import annotations

import json
from datetime import date

import pytest

from fed_watch import contracts
from fed_watch.config import fomc
from fed_watch.errors import ConfigError


def test_shipped_calendar_is_sorted_unique_and_parseable():
    meetings = fomc.load(fomc.CALENDAR_FILE)
    assert meetings == sorted(set(meetings))
    assert all(isinstance(meeting, date) for meeting in meetings)


def test_upcoming_includes_a_meeting_on_its_own_decision_day(calendar):
    """The announcement lands in the afternoon; until then it is still priced."""
    assert fomc.upcoming(calendar, date(2026, 9, 16), 1) == [date(2026, 9, 16)]


def test_upcoming_returns_the_next_two_in_ascending_order(calendar):
    assert fomc.upcoming(calendar, date(2026, 9, 12), 2) == [
        date(2026, 9, 16),
        date(2026, 10, 28),
    ]


def test_running_out_of_calendar_is_an_error_with_a_remedy(calendar):
    with pytest.raises(ConfigError) as caught:
        fomc.upcoming(calendar, date(2027, 12, 1), 2)
    assert "remedy" in caught.value.detail


def test_unsorted_calendar_is_rejected(tmp_path):
    path = tmp_path / "fomc.json"
    path.write_text(json.dumps({"meetings": ["2026-10-28", "2026-09-16"]}), encoding="utf-8")
    with pytest.raises(ConfigError):
        fomc.load(path)


def test_november_2026_holds_no_meeting(calendar):
    assert fomc.month_has_meeting(calendar, 2026, 10)
    assert not fomc.month_has_meeting(calendar, 2026, 11)


@pytest.mark.parametrize(
    "year, month, expected",
    [(2026, 9, "ZQU26.CBT"), (2026, 10, "ZQV26.CBT"), (2026, 11, "ZQX26.CBT"),
     (2026, 12, "ZQZ26.CBT"), (2027, 1, "ZQF27.CBT")],
)
def test_symbol_uses_the_dotted_cbot_form(year, month, expected):
    """`ZQU26=F` and bare `ZQU26` both 404 on Yahoo; only this form resolves."""
    assert contracts.symbol(year, month) == expected


def test_next_month_rolls_the_year():
    assert contracts.next_month(2026, 12) == (2027, 1)
    assert contracts.next_month(2026, 9) == (2026, 10)
