"""Indicator maths, checked against values a reader can verify by hand."""

from __future__ import annotations

from datetime import date

import pytest

from stock_desk import indicators
from stock_desk.models import Bar


def bar(close: float, spread: float = 2.0, volume: float = 1000.0, day: int = 1) -> Bar:
    return Bar(
        day=date(2024, 1, day),
        open=close,
        high=close + spread / 2,
        low=close - spread / 2,
        close=close,
        volume=volume,
    )


class TestSMA:
    def test_pads_until_the_window_is_full(self):
        assert indicators.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]

    def test_length_always_matches_the_input(self):
        values = [float(n) for n in range(10)]
        assert len(indicators.sma(values, 4)) == len(values)

    def test_period_longer_than_the_series_is_all_none(self):
        assert indicators.sma([1, 2], 5) == [None, None]

    def test_running_sum_does_not_drift(self):
        """The rolling implementation subtracts as it goes; check it against a
        recomputed mean rather than against itself."""
        values = [float(n % 7) * 1.37 for n in range(200)]
        rolled = indicators.sma(values, 20)
        for index in range(19, len(values)):
            expected = sum(values[index - 19 : index + 1]) / 20
            assert rolled[index] == pytest.approx(expected, abs=1e-9)


class TestTrueRangeAndATR:
    def test_first_bar_falls_back_to_high_minus_low(self):
        bars = [bar(100, spread=3)]
        assert indicators.true_range(bars) == [3.0]

    def test_gap_up_uses_the_previous_close(self):
        bars = [bar(100, spread=2), bar(110, spread=2)]
        # high 111, low 109, previous close 100 -> the 11-point gap dominates.
        assert indicators.true_range(bars)[1] == pytest.approx(11.0)

    def test_atr_is_seeded_with_a_simple_mean(self):
        bars = [bar(100, spread=2) for _ in range(14)]
        values = indicators.atr(bars, 14)
        assert values[:13] == [None] * 13
        assert values[13] == pytest.approx(2.0)

    def test_atr_smooths_rather_than_jumping(self):
        bars = [bar(100, spread=2) for _ in range(14)] + [bar(100, spread=30)]
        values = indicators.atr(bars, 14)
        # Wilder: (2 * 13 + 30) / 14 = 4.0, not 30.
        assert values[14] == pytest.approx(4.0)

    def test_atr_percent_normalises_by_close(self):
        bars = [bar(100, spread=2) for _ in range(14)]
        assert indicators.atr_percent(bars, 14)[13] == pytest.approx(0.02)


class TestBollingerAndDonchian:
    def test_width_is_zero_on_a_flat_series(self):
        assert indicators.bollinger_width([100.0] * 25, 20)[24] == pytest.approx(0.0)

    def test_width_grows_with_dispersion(self):
        steady = indicators.bollinger_width([100.0 + (n % 2) for n in range(40)], 20)[-1]
        wild = indicators.bollinger_width([100.0 + (n % 2) * 20 for n in range(40)], 20)[-1]
        assert wild > steady

    def test_donchian_spans_the_window_high_and_low(self):
        bars = [bar(100, spread=2) for _ in range(19)] + [bar(110, spread=2)]
        # High 111 across the window, low 99, close 110.
        assert indicators.donchian_width(bars, 20)[19] == pytest.approx(12.0 / 110.0)


class TestPercentileRank:
    def test_midpoint(self):
        assert indicators.percentile_rank([1, 2, 3, 4], 2) == 50.0

    def test_minimum_of_the_window(self):
        assert indicators.percentile_rank([5, 6, 7, 8], 5) == 25.0

    def test_above_everything(self):
        assert indicators.percentile_rank([1, 2, 3], 99) == 100.0

    def test_empty_history_is_neutral(self):
        assert indicators.percentile_rank([], 42) == 50.0


class TestBarPatterns:
    def test_nr7_needs_seven_bars(self):
        bars = [bar(100, spread=2) for _ in range(6)]
        assert indicators.is_nr7(bars, 5) is False

    def test_nr7_fires_on_the_narrowest_of_seven(self):
        bars = [bar(100, spread=5) for _ in range(6)] + [bar(100, spread=1)]
        assert indicators.is_nr7(bars, 6) is True

    def test_nr7_does_not_fire_when_a_narrower_bar_precedes_it(self):
        bars = [bar(100, spread=1)] + [bar(100, spread=5) for _ in range(5)] + [bar(100, spread=2)]
        assert indicators.is_nr7(bars, 6) is False

    def test_inside_day_requires_containment_on_both_sides(self):
        bars = [bar(100, spread=10), bar(100, spread=4)]
        assert indicators.is_inside_day(bars, 1) is True

    def test_higher_high_is_not_an_inside_day(self):
        bars = [bar(100, spread=4), bar(103, spread=4)]
        assert indicators.is_inside_day(bars, 1) is False


class TestVolume:
    def test_rvol_excludes_the_current_bar_from_its_own_baseline(self):
        bars = [bar(100, volume=1000) for _ in range(20)] + [bar(100, volume=3000)]
        # Baseline is the prior 20 bars at 1000, so 3000/1000 = 3.0. Including
        # the spike would give 3000/1095 and understate every surge.
        assert indicators.rvol(bars, 20) == pytest.approx(3.0)

    def test_rvol_is_none_without_enough_history(self):
        assert indicators.rvol([bar(100) for _ in range(5)], 20) is None

    def test_average_dollar_volume_multiplies_by_close(self):
        bars = [bar(50, volume=1000) for _ in range(20)]
        assert indicators.average_dollar_volume(bars, 20) == pytest.approx(50_000)


class TestRangeHelpers:
    def test_week52_caps_at_252_sessions(self):
        bars = [bar(500, spread=2, day=1)] + [bar(100, spread=2) for _ in range(260)]
        high, low = indicators.week52(bars)
        # The 500 is older than 252 sessions, so it must not be the high.
        assert high == pytest.approx(101.0)
        assert low == pytest.approx(99.0)

    def test_pct_change_sign_follows_the_first_argument(self):
        assert indicators.pct_change(110, 100) == pytest.approx(10.0)
        assert indicators.pct_change(90, 100) == pytest.approx(-10.0)

    def test_pct_change_guards_a_zero_reference(self):
        assert indicators.pct_change(10, 0) is None
