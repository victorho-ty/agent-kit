"""Base detection, pivots, and the progressive-contraction test."""

from __future__ import annotations

import pytest

from stock_desk import compression, indicators
from stock_desk.models import Thresholds

from .conftest import CONTRACTING_BASE, PIVOT, build, expansion_run, quiet_run, steep_rally

THRESHOLDS = Thresholds()


class TestFindBase:
    def test_finds_the_contracting_window(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        assert base is not None
        assert base.length == len(CONTRACTING_BASE)

    def test_stops_at_the_deep_pullback(self, coiled_bars):
        """The bar before the base drops to 112, which breaks the depth limit --
        this is what keeps the base from swallowing the rally behind it."""
        base = compression.find_base(coiled_bars, THRESHOLDS)
        assert base.start_index == len(coiled_bars) - len(CONTRACTING_BASE)

    def test_depth_stays_inside_the_limit(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        assert base.depth <= THRESHOLDS.max_base_depth

    def test_takes_the_longest_qualifying_window_not_the_shortest(self, coiled_bars):
        """Every sideways stretch contains a tight three-day window. Returning
        that would find a base on literally everything."""
        base = compression.find_base(coiled_bars, THRESHOLDS)
        assert base.length > THRESHOLDS.min_base_len

    def test_no_base_during_a_hard_rally(self):
        bars = build(quiet_run() + steep_rally())
        assert compression.find_base(bars, THRESHOLDS) is None

    def test_a_shallow_pullback_inside_a_gentle_rally_is_a_base(self):
        """Not a bug. A nine-bar window near the top of a 20% advance genuinely
        fits inside the depth limit; what disqualifies it is the contraction
        test, further down, not the base search."""
        bars = build(quiet_run() + expansion_run())
        base = compression.find_base(bars, THRESHOLDS)
        assert base is not None
        assert not compression.is_monotone_contraction(
            compression.contraction_ratios(bars, base), THRESHOLDS.contraction_tolerance
        )

    def test_no_base_when_history_is_shorter_than_the_minimum(self):
        bars = build(quiet_run(count=4))
        assert compression.find_base(bars, THRESHOLDS) is None

    def test_respects_max_base_len(self):
        """A year of dead-flat trading is capped, not returned whole."""
        bars = build(quiet_run(count=200, price=100.0))
        base = compression.find_base(bars, THRESHOLDS)
        assert base is not None
        assert base.length == THRESHOLDS.max_base_len


class TestPivot:
    def test_pivot_excludes_the_final_bar(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        assert compression.pivot_level(coiled_bars, base) == pytest.approx(PIVOT)

    def test_pivot_does_not_rise_with_a_breakout_bar(self, triggered_bars):
        """The whole point: on the day price clears the range, the level it
        cleared must still be the old one."""
        base = compression.find_base(triggered_bars, THRESHOLDS)
        assert compression.pivot_level(triggered_bars, base) == pytest.approx(PIVOT)
        assert triggered_bars[-1].close > PIVOT

    def test_counts_repeated_tests_of_the_level(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        touches = compression.pivot_touches(coiled_bars, base, THRESHOLDS.pivot_tolerance)
        assert touches >= THRESHOLDS.min_pivot_touches

    def test_a_tight_tolerance_counts_fewer_touches(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        loose = compression.pivot_touches(coiled_bars, base, 0.05)
        tight = compression.pivot_touches(coiled_bars, base, 0.001)
        assert tight < loose


class TestContraction:
    def test_ratios_shrink_across_the_base(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        ratios = compression.contraction_ratios(coiled_bars, base)
        assert ratios is not None
        first, second, third = ratios
        assert first > second > third

    def test_monotone_accepts_the_contracting_base(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        ratios = compression.contraction_ratios(coiled_bars, base)
        assert compression.is_monotone_contraction(ratios, THRESHOLDS.contraction_tolerance)

    def test_monotone_rejects_a_flat_base(self, basing_bars):
        base = compression.find_base(basing_bars, THRESHOLDS)
        ratios = compression.contraction_ratios(basing_bars, base)
        assert not compression.is_monotone_contraction(ratios, THRESHOLDS.contraction_tolerance)

    def test_tolerance_rejects_a_negligible_improvement(self):
        """0.5% tighter is noise, not contraction."""
        assert not compression.is_monotone_contraction((1.0, 0.995, 0.99), 0.98)

    def test_tolerance_accepts_a_real_step_down(self):
        assert compression.is_monotone_contraction((1.0, 0.8, 0.6), 0.98)

    def test_widening_is_never_contraction(self):
        assert not compression.is_monotone_contraction((0.6, 0.8, 1.0), 0.98)

    def test_ratios_none_when_the_base_is_too_short_to_split(self):
        bars = build(quiet_run(count=200))
        base = compression.find_base(bars, THRESHOLDS)
        short = compression.Base(
            start_index=base.end_index, end_index=base.end_index, high=1.0, low=1.0, depth=0.0
        )
        assert compression.contraction_ratios(bars, short) is None


class TestPriorExpansion:
    def test_true_after_a_volatile_leg(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        atr_pct = indicators.atr_percent(coiled_bars)
        assert compression.had_prior_expansion(atr_pct, base.start_index, THRESHOLDS)

    def test_false_for_a_stock_that_has_only_ever_been_quiet(self):
        """Also pins the tie case. This series is perfectly periodic, so ATR% is
        constant and the run-up equals every historical value -- which a
        percentile alone scores at 100 and calls an expansion."""
        bars = build(quiet_run(count=200))
        base = compression.find_base(bars, THRESHOLDS)
        atr_pct = indicators.atr_percent(bars)
        assert not compression.had_prior_expansion(atr_pct, base.start_index, THRESHOLDS)

    def test_false_without_twenty_bars_of_run_up(self):
        bars = build(quiet_run(count=200))
        atr_pct = indicators.atr_percent(bars)
        assert not compression.had_prior_expansion(atr_pct, 5, THRESHOLDS)


class TestVolumeDryup:
    def test_below_one_when_participation_drains(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        dryup = compression.volume_dryup(coiled_bars, base)
        # 3.0M in the first third, 1.2M in the last.
        assert dryup == pytest.approx(0.4)

    def test_around_one_on_steady_volume(self, basing_bars):
        base = compression.find_base(basing_bars, THRESHOLDS)
        assert compression.volume_dryup(basing_bars, base) == pytest.approx(1.0)


class TestTightnessSignals:
    def test_the_coil_trips_at_least_one(self, coiled_bars):
        base = compression.find_base(coiled_bars, THRESHOLDS)
        closes = [b.close for b in coiled_bars]
        bbw = indicators.bollinger_width(closes)
        donchian = indicators.donchian_width(coiled_bars)
        last = len(coiled_bars) - 1
        signals = compression.tightness_signals(
            coiled_bars,
            indicators.percentile_rank(indicators.trailing(bbw, last, 252), bbw[last]),
            indicators.percentile_rank(indicators.trailing(donchian, last, 252), donchian[last]),
            THRESHOLDS,
        )
        assert any(signals.values())
        assert set(signals) == {"nr7", "inside_day", "bbw_squeeze", "donchian_squeeze"}

    def test_none_trip_midway_through_a_rally(self):
        bars = build(quiet_run() + steep_rally())
        signals = compression.tightness_signals(bars, 95.0, 95.0, THRESHOLDS)
        assert not any(signals.values())
