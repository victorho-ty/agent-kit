"""Change detection, and the baseline behaviour that makes it useful."""

from __future__ import annotations

import pytest

from fed_watch import changes, settings


def snapshot(taken_at: str, hike_pct: float, no_change_pct: float, effr: float = 3.63) -> dict:
    return {
        "taken_at": taken_at,
        "effr": effr,
        "target_low": 3.50,
        "target_high": 3.75,
        "meetings": [
            {
                "meeting_date": "2026-09-16",
                "contract": "ZQU26.CBT",
                "implied_rate": 3.73,
                "expected_rate": 3.85,
                "outcomes": [
                    {
                        "step": 0,
                        "band_low": 3.50,
                        "band_high": 3.75,
                        "probability": no_change_pct / 100,
                    },
                    {
                        "step": 1,
                        "band_low": 3.75,
                        "band_high": 4.00,
                        "probability": hike_pct / 100,
                    },
                ],
            }
        ],
    }


def test_first_run_reports_rather_than_claiming_nothing_moved():
    report = changes.diff(None, snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3), 1.0)
    assert report["changed"] is True
    assert report["baseline_taken_at"] is None


def test_movement_under_the_threshold_is_silent():
    before = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    after = snapshot("2026-09-12T10:00:00+08:00", 87.2, 12.8)
    assert changes.diff(before, after, 1.0)["changed"] is False


def test_movement_over_the_threshold_is_reported_per_band():
    before = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    after = snapshot("2026-09-12T15:00:00+08:00", 91.4, 8.6)
    report = changes.diff(before, after, 1.0)

    assert report["changed"] is True
    assert report["largest_move_pct"] == pytest.approx(4.7, abs=0.01)
    rows = {row["label"]: row for row in report["meetings"][0]["outcomes"]}
    assert rows["25bp hike"]["from_pct"] == 86.7
    assert rows["25bp hike"]["to_pct"] == 91.4
    assert rows["25bp hike"]["delta_pct"] == pytest.approx(4.7, abs=0.01)
    assert rows["no change"]["delta_pct"] == pytest.approx(-4.7, abs=0.01)


def test_a_policy_move_is_a_change_even_when_probabilities_have_not_shifted():
    before = snapshot("2026-09-16T09:00:00+08:00", 86.7, 13.3, effr=3.63)
    after = snapshot("2026-09-17T09:00:00+08:00", 86.7, 13.3, effr=3.88)
    report = changes.diff(before, after, 1.0)
    assert report["changed"] is True
    assert report["policy_changed"] is True


def test_a_meeting_that_has_rolled_off_is_named_not_silently_dropped():
    before = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    before["meetings"].append({**before["meetings"][0], "meeting_date": "2026-10-28"})
    after = snapshot("2026-09-17T09:00:00+08:00", 86.7, 13.3)

    statuses = {m["meeting_date"]: m["status"] for m in changes.diff(before, after, 1.0)["meetings"]}
    assert statuses["2026-10-28"] == "dropped"


def test_series_groups_by_meeting_and_preserves_order():
    history = [
        snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3),
        snapshot("2026-09-12T15:00:00+08:00", 91.4, 8.6),
    ]
    shaped = changes.series(history)
    assert set(shaped) == {"2026-09-16"}
    assert shaped["2026-09-16"]["series"]["25bp hike"] == [86.7, 91.4]
    assert shaped["2026-09-16"]["timestamps"] == [
        "2026-09-12T09:00:00+08:00",
        "2026-09-12T15:00:00+08:00",
    ]


def test_series_backfills_a_band_that_was_not_priced_earlier():
    """A band absent from an earlier snapshot was priced at nothing, not unknown."""
    first = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    second = snapshot("2026-09-12T15:00:00+08:00", 60.0, 5.0)
    second["meetings"][0]["outcomes"].append(
        {"step": 2, "band_low": 4.00, "band_high": 4.25, "probability": 0.35}
    )
    shaped = changes.series([first, second])
    assert shaped["2026-09-16"]["series"]["50bp hike"] == [0.0, 35.0]


def test_a_move_under_four_points_no_longer_alerts():
    """86.7 -> 89.9 is 3.2 points: real, but below the gate."""
    before = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    after = snapshot("2026-09-12T15:00:00+08:00", 89.9, 10.1)
    assert changes.diff(before, after, settings.change_threshold())["changed"] is False


def test_a_move_over_four_points_alerts():
    before = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    after = snapshot("2026-09-12T15:00:00+08:00", 91.4, 8.6)
    assert changes.diff(before, after, settings.change_threshold())["changed"] is True
