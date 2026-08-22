"""Sector arithmetic over hand-written bars.

Every fixture here is a straight line from A to B, because the question is
whether the comparison is right, not whether the price path is realistic.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_desk import sector
from stock_desk.models import Bar, SectorConfig

START = date(2026, 7, 1)


def ramp(start: float, end: float, count: int = 31) -> list[Bar]:
    """``count`` daily bars walking linearly from ``start`` to ``end``."""
    step = 0.0 if count < 2 else (end - start) / (count - 1)
    bars = []
    for i in range(count):
        close = start + step * i
        bars.append(
            Bar(
                day=START + timedelta(days=i),
                open=close,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=1_000_000,
            )
        )
    return bars


def group(name="AI infrastructure", members=("NVDA", "AMD", "AVGO")):
    return SectorConfig(name=name, members=members)


class TestHorizonReturn:
    def test_measures_close_to_close(self):
        assert sector.horizon_return(ramp(100.0, 110.0), 30) == pytest.approx(10.0)

    def test_a_shorter_window_reaches_less_far_back(self):
        bars = ramp(100.0, 130.0, count=31)  # +1 per session
        assert sector.horizon_return(bars, 10) == pytest.approx(
            100.0 * (bars[-1].close - bars[-11].close) / bars[-11].close
        )

    def test_too_little_history_is_none_not_zero(self):
        # A member silently counted as flat drags the group median toward nothing.
        assert sector.horizon_return([], 30) is None
        assert sector.horizon_return(ramp(100.0, 110.0, count=1), 30) is None

    def test_a_short_series_uses_what_it_has(self):
        # A holiday shortens the window rather than reaching further back.
        assert sector.horizon_return(ramp(100.0, 105.0, count=5), 30) == pytest.approx(5.0)

    def test_a_zero_horizon_is_rejected(self):
        assert sector.horizon_return(ramp(100.0, 110.0), 0) is None


class TestCohesion:
    def test_a_group_moving_together_is_a_bloc(self):
        bars = {
            "NVDA": ramp(100.0, 110.0),
            "AMD": ramp(100.0, 111.0),
            "AVGO": ramp(100.0, 109.0),
        }
        view = sector.analyse(group(), bars, 30)
        assert view.cohesion == "bloc"
        assert view.breadth_up == 3

    def test_a_group_pulling_apart_is_scattered(self):
        bars = {
            "NVDA": ramp(100.0, 140.0),
            "AMD": ramp(100.0, 100.5),
            "AVGO": ramp(100.0, 80.0),
        }
        view = sector.analyse(group(), bars, 30)
        assert view.cohesion == "scattered"

    def test_breadth_counts_direction_not_size(self):
        # One name up 30% and two barely down is not a sector that is working,
        # however good the median looks.
        bars = {
            "NVDA": ramp(100.0, 130.0),
            "AMD": ramp(100.0, 99.0),
            "AVGO": ramp(100.0, 98.0),
        }
        view = sector.analyse(group(), bars, 30)
        assert view.breadth_up == 1
        assert view.breadth_total == 3


class TestMissingMembers:
    def test_a_member_with_no_history_is_named_not_dropped(self):
        bars = {"NVDA": ramp(100.0, 110.0), "AMD": ramp(100.0, 108.0), "AVGO": []}
        view = sector.analyse(group(), bars, 30)
        assert view.missing == ("AVGO",)
        assert view.breadth_total == 2

    def test_one_priced_member_is_not_a_sector(self):
        bars = {"NVDA": ramp(100.0, 110.0), "AMD": [], "AVGO": []}
        view = sector.analyse(group(), bars, 30)
        assert not view.usable
        assert view.median_return is None
        assert "not enough history" in sector.line(view)

    def test_the_line_says_how_many_were_priced(self):
        bars = {"NVDA": ramp(100.0, 110.0), "AMD": [], "AVGO": []}
        assert "1 of 3" in sector.line(sector.analyse(group(), bars, 30))


class TestStanding:
    def test_a_leader_is_named_as_one(self):
        bars = {
            "NVDA": ramp(100.0, 125.0),
            "AMD": ramp(100.0, 102.0),
            "AVGO": ramp(100.0, 101.0),
        }
        view = sector.analyse(group(), bars, 30)
        assert sector.standing(view, "NVDA")["position"] == "leading"
        assert "NVDA" in view.leaders

    def test_a_laggard_is_named_as_one(self):
        bars = {
            "NVDA": ramp(100.0, 90.0),
            "AMD": ramp(100.0, 112.0),
            "AVGO": ramp(100.0, 113.0),
        }
        view = sector.analyse(group(), bars, 30)
        assert sector.standing(view, "NVDA")["position"] == "lagging"

    def test_a_small_gap_is_in_line(self):
        bars = {
            "NVDA": ramp(100.0, 111.0),
            "AMD": ramp(100.0, 110.0),
            "AVGO": ramp(100.0, 109.0),
        }
        assert sector.standing(sector.analyse(group(), bars, 30), "NVDA")["position"] == "in_line"

    def test_moving_in_line_with_a_bloc_means_carried(self):
        """The reading the whole module exists for.

        A breakout that is really the sector's move deserves less conviction
        than the chart alone suggests.
        """
        bars = {
            "NVDA": ramp(100.0, 110.0),
            "AMD": ramp(100.0, 111.0),
            "AVGO": ramp(100.0, 109.0),
        }
        assert sector.standing(sector.analyse(group(), bars, 30), "NVDA")["carried"] is True

    def test_leading_a_scattered_group_is_not_carried(self):
        bars = {
            "NVDA": ramp(100.0, 140.0),
            "AMD": ramp(100.0, 100.5),
            "AVGO": ramp(100.0, 80.0),
        }
        assert sector.standing(sector.analyse(group(), bars, 30), "NVDA")["carried"] is False

    def test_an_unpriced_member_has_no_standing(self):
        bars = {"NVDA": ramp(100.0, 110.0), "AMD": ramp(100.0, 108.0), "AVGO": []}
        assert sector.standing(sector.analyse(group(), bars, 30), "AVGO") is None

    def test_a_ticker_outside_the_sector_has_no_standing(self):
        bars = {"NVDA": ramp(100.0, 110.0), "AMD": ramp(100.0, 108.0)}
        assert sector.standing(sector.analyse(group(), bars, 30), "TSLA") is None


class TestSerialisation:
    def test_to_dict_labels_the_reading_as_derived(self):
        bars = {"NVDA": ramp(100.0, 110.0), "AMD": ramp(100.0, 108.0)}
        payload = sector.analyse(group(), bars, 30).to_dict()
        assert payload["basis"].startswith("derived")
        assert payload["breadth"] == "2/2"

    def test_the_line_is_a_finished_sentence(self):
        bars = {
            "NVDA": ramp(100.0, 125.0),
            "AMD": ramp(100.0, 102.0),
            "AVGO": ramp(100.0, 101.0),
        }
        rendered = sector.line(sector.analyse(group(), bars, 30))
        assert rendered.startswith("AI infrastructure:")
        assert rendered.endswith(".")
        assert "leading NVDA" in rendered


class TestBreadthWording:
    def test_breadth_always_counts_what_is_up(self):
        """"0/2 down" read as "neither is down". Breadth counts up, always."""
        bars = {"NVDA": ramp(100.0, 92.0), "AMD": ramp(100.0, 91.0), "AVGO": ramp(100.0, 93.0)}
        rendered = sector.line(sector.analyse(group(), bars, 30))
        assert "0/3 up" in rendered
        assert "down" not in rendered

    def test_a_rising_group_reads_the_same_way(self):
        bars = {"NVDA": ramp(100.0, 110.0), "AMD": ramp(100.0, 111.0), "AVGO": ramp(100.0, 109.0)}
        assert "3/3 up" in sector.line(sector.analyse(group(), bars, 30))
