"""The calculation, pinned against CME's own published output."""

from __future__ import annotations

from datetime import date

import pytest

from fed_watch import probabilities
from fed_watch.models import PolicyRate

from .conftest import (
    CME_MID_ZQU26,
    CME_PUBLISHED_HIKE_25,
    CME_PUBLISHED_NO_CHANGE,
    TARGET_HIGH,
    TARGET_LOW,
)


def _pct(outcomes, step):
    return round(next(o.probability for o in outcomes if o.step == step) * 100, 1)


def test_reproduces_cme_published_probabilities(policy, month_has_meeting):
    """The whole justification for computing rather than scraping.

    Given CME's own mid price, the pipeline must land on CME's own published
    figures for 16 September 2026 -- 13.3% no change, 86.7% a 25bp hike.
    """
    from .conftest import quote

    priced = probabilities.solve(
        policy,
        [date(2026, 9, 16)],
        {"ZQU26.CBT": quote("ZQU26.CBT", CME_MID_ZQU26)},
        month_has_meeting,
    )

    assert len(priced) == 1
    assert _pct(priced[0].outcomes, 0) == CME_PUBLISHED_NO_CHANGE
    assert _pct(priced[0].outcomes, 1) == CME_PUBLISHED_HIKE_25


def test_target_midpoint_instead_of_effr_is_four_points_wrong(month_has_meeting):
    """Why `rates.py` fetches the effective rate rather than assuming it."""
    from .conftest import quote

    midpoint = PolicyRate(
        effr=(TARGET_LOW + TARGET_HIGH) / 2,
        as_of=date(2026, 9, 10),
        target_low=TARGET_LOW,
        target_high=TARGET_HIGH,
    )
    priced = probabilities.solve(
        midpoint,
        [date(2026, 9, 16)],
        {"ZQU26.CBT": quote("ZQU26.CBT", CME_MID_ZQU26)},
        month_has_meeting,
    )
    assert _pct(priced[0].outcomes, 1) == pytest.approx(91.0, abs=0.1)


def test_september_uses_blend_inversion_october_uses_the_clear_month(month_has_meeting):
    """October is followed by a meeting-free November, so no inversion is needed."""
    assert probabilities.plan(date(2026, 9, 16), month_has_meeting) == (
        "blend_inversion",
        "ZQU26.CBT",
    )
    assert probabilities.plan(date(2026, 10, 28), month_has_meeting) == (
        "clean_next_month",
        "ZQX26.CBT",
    )


def test_late_month_inversion_would_amplify_ten_fold():
    """The reason `clean_next_month` is preferred where the calendar allows it."""
    assert probabilities.amplification(date(2026, 10, 28)) == pytest.approx(31 / 3, abs=0.01)
    assert probabilities.amplification(date(2026, 9, 16)) == pytest.approx(30 / 14, abs=0.01)


def test_second_meeting_is_conditioned_on_the_first(policy, quotes, month_has_meeting):
    priced = probabilities.solve(
        policy, [date(2026, 9, 16), date(2026, 10, 28)], quotes, month_has_meeting
    )

    assert [p.ordinal for p in priced] == [1, 2]
    assert priced[1].method == "clean_next_month"
    assert priced[1].contract == "ZQX26.CBT"
    # Two independent 25bp steps can compound, so the October table spans three
    # bands where September's spans two.
    assert {o.step for o in priced[1].outcomes} == {0, 1, 2}
    for meeting in priced:
        assert sum(o.probability for o in meeting.outcomes) == pytest.approx(1.0)


def test_chain_stops_at_the_first_missing_contract(policy, month_has_meeting):
    """A later meeting measured from a rate we could not read is worse than silence."""
    from .conftest import YAHOO_ZQU26, quote

    priced = probabilities.solve(
        policy,
        [date(2026, 9, 16), date(2026, 10, 28)],
        {"ZQU26.CBT": quote("ZQU26.CBT", YAHOO_ZQU26)},
        month_has_meeting,
    )
    assert [p.ordinal for p in priced] == [1]


def test_distribute_handles_cuts():
    # -15bp of expected move is 60% of a 25bp cut: 0.6 * -0.25 == -0.15.
    assert probabilities.distribute(-0.15) == pytest.approx({-1: 0.6, 0: 0.4})
    assert probabilities.distribute(0.25) == {1: 1.0}
    assert probabilities.distribute(0.0) == {0: 1.0}


def test_distribute_spans_more_than_one_step():
    assert probabilities.distribute(0.40) == pytest.approx({1: 0.4, 2: 0.6})


def test_outcome_table_is_contiguous_and_always_holds_no_change(policy, month_has_meeting):
    """A band the market has ruled out is a finding; a missing row looks like a bug."""
    from .conftest import quote

    priced = probabilities.solve(
        policy,
        [date(2026, 9, 16)],
        # Priced far enough for a 50bp hike that no-change carries nothing.
        {"ZQU26.CBT": quote("ZQU26.CBT", 96.05)},
        month_has_meeting,
    )
    steps = [o.step for o in priced[0].outcomes]
    assert steps == list(range(min(steps), max(steps) + 1))
    assert 0 in steps


def test_bands_track_the_current_target_range(policy, quotes, month_has_meeting):
    priced = probabilities.solve(policy, [date(2026, 9, 16)], quotes, month_has_meeting)
    hike = next(o for o in priced[0].outcomes if o.step == 1)
    assert (hike.band_low, hike.band_high) == (3.75, 4.00)
    assert hike.label == "25bp hike"


def test_a_decision_on_the_last_day_of_a_month_is_refused():
    with pytest.raises(ValueError):
        probabilities.post_meeting_rate(3.7, 3.63, date(2026, 9, 30))
