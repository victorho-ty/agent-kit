"""Stage classification and scoring -- the closed enum the report branches on."""

from __future__ import annotations

import pytest

from stock_desk import setups
from stock_desk.models import Thresholds

from .conftest import PIVOT, build, quiet_run

THRESHOLDS = Thresholds()


class TestGuards:
    def test_no_bars_at_all(self):
        result = setups.detect("NADA", [], THRESHOLDS)
        assert result.stage == "none"
        assert result.reason == "no_bars"

    def test_short_history_is_reported_as_such_not_as_no_setup(self, short_history):
        """A newly listed ticker and a ticker with nothing going on are different
        answers, and the difference decides whether waiting helps."""
        result = setups.detect("NEW", short_history, THRESHOLDS)
        assert result.stage == "none"
        assert result.reason == "insufficient_history"

    def test_illiquid_is_rejected_before_the_pattern_is_interpreted(self, illiquid_bars):
        result = setups.detect("THIN", illiquid_bars, THRESHOLDS)
        assert result.stage == "none"
        assert result.reason == "illiquid"
        assert result.score == 0

    def test_illiquid_still_carries_position_metrics_for_its_status_line(self, illiquid_bars):
        result = setups.detect("THIN", illiquid_bars, THRESHOLDS)
        assert result.close is not None
        assert result.week52_high is not None


class TestCoiled:
    def test_the_coil_is_recognised(self, coiled_bars):
        result = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert result.stage == "coiled"

    def test_it_reports_the_pivot_it_is_coiling_under(self, coiled_bars):
        result = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert result.pivot == pytest.approx(PIVOT)
        assert result.close < result.pivot

    def test_it_is_within_the_proximity_limit(self, coiled_bars):
        result = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert 0 <= result.distance_to_pivot_pct / 100 <= THRESHOLDS.pivot_proximity

    def test_all_four_conditions_are_recorded(self, coiled_bars):
        result = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert result.prior_expansion is True
        assert result.contraction_monotone is True
        assert result.pivot_touches >= THRESHOLDS.min_pivot_touches
        assert result.volume_dryup < 1.0

    def test_it_scores_well(self, coiled_bars):
        result = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert result.score >= 60

    def test_a_recent_base_is_within_the_horizon(self, coiled_bars):
        result = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert result.within_horizon is True

    def test_a_one_day_horizon_puts_the_same_base_out_of_scope(self, coiled_bars):
        """The horizon governs reporting, not detection: the setup is still found
        and still scored, it is just no longer fresh."""
        result = setups.detect("COIL", coiled_bars, Thresholds(technical_horizon_days=1))
        assert result.stage == "coiled"
        assert result.within_horizon is False


class TestSqueezeVeto:
    def test_a_wide_band_blocks_the_coil_call(self, coiled_bars):
        """Observed on live NVDA data: contraction was monotone and an NR7 bar
        tripped a tightness signal, but band width sat at the 94th percentile of
        its own year. Contracting swings inside a still-wide range are a base."""
        vetoed = setups.detect("COIL", coiled_bars, Thresholds(max_bbw_percentile=0.0))
        assert vetoed.stage == "basing"

    def test_the_veto_does_not_fire_on_a_genuine_squeeze(self, coiled_bars):
        result = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert result.stage == "coiled"
        assert result.bbw_percentile <= THRESHOLDS.max_bbw_percentile


class TestBasing:
    def test_a_range_that_never_tightens_is_only_basing(self, basing_bars):
        result = setups.detect("FLAT", basing_bars, THRESHOLDS)
        assert result.stage == "basing"
        assert result.contraction_monotone is False

    def test_basing_still_scores_below_a_coil(self, basing_bars, coiled_bars):
        flat = setups.detect("FLAT", basing_bars, THRESHOLDS)
        coil = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert flat.score < coil.score


class TestExpansion:
    def test_a_rally_with_no_base_is_expansion(self):
        """``expansion`` is reachable only when no base exists at all. A rally
        that has already paused long enough to form one is `basing`, however
        volatile it still looks."""
        from .conftest import steep_rally

        result = setups.detect("RIP", build(quiet_run() + steep_rally()), THRESHOLDS)
        assert result.stage == "expansion"

    def test_a_permanently_quiet_stock_is_not_expansion(self):
        result = setups.detect("DULL", build(quiet_run(count=200)), THRESHOLDS)
        assert result.stage in {"basing", "none"}


class TestTriggered:
    def test_closing_above_the_pivot_triggers(self, triggered_bars):
        result = setups.detect("POP", triggered_bars, THRESHOLDS)
        assert result.stage == "triggered"

    def test_heavy_volume_confirms_the_break(self, triggered_bars):
        result = setups.detect("POP", triggered_bars, THRESHOLDS)
        assert result.volume_confirmed is True

    def test_a_quiet_break_is_flagged_unconfirmed(self):
        """Same geometry, a fifth of the volume. The distinction is the whole
        reason volume is scored at all."""
        from .conftest import CONTRACTING_BASE, deep_pullback, expansion_run

        quiet_break = build(
            quiet_run()
            + expansion_run()
            + deep_pullback()
            + CONTRACTING_BASE
            + [(136.0, 2.0, 300_000)]
        )
        result = setups.detect("MEH", quiet_break, THRESHOLDS)
        assert result.stage == "triggered"
        assert result.volume_confirmed is False


class TestFailed:
    def test_back_under_a_pivot_it_broke_is_a_failure(self, coiled_bars):
        result = setups.detect(
            "FAIL", coiled_bars, THRESHOLDS, previous_stage="triggered", previous_pivot=PIVOT
        )
        assert result.stage == "failed"

    def test_holding_above_the_pivot_stays_triggered(self, triggered_bars):
        result = setups.detect(
            "HOLD", triggered_bars, THRESHOLDS, previous_stage="triggered", previous_pivot=PIVOT
        )
        assert result.stage == "triggered"

    def test_without_yesterdays_verdict_a_failure_is_invisible(self, coiled_bars):
        """This is exactly why setup_state exists -- the same bars read as a tidy
        coil if nobody remembers the breakout that preceded them."""
        assert setups.detect("FAIL", coiled_bars, THRESHOLDS).stage == "coiled"


class TestDeterminism:
    def test_the_same_bars_always_give_the_same_answer(self, coiled_bars):
        first = setups.detect("COIL", coiled_bars, THRESHOLDS)
        second = setups.detect("COIL", coiled_bars, THRESHOLDS)
        assert first.to_dict() == second.to_dict()

    def test_to_dict_serialises_dates_and_ratios(self, coiled_bars):
        payload = setups.detect("COIL", coiled_bars, THRESHOLDS).to_dict()
        assert isinstance(payload["as_of"], str)
        assert isinstance(payload["base_start"], str)
        assert isinstance(payload["contraction_ratios"], list)


class TestStatusLine:
    def test_every_stage_renders_without_a_model(self, coiled_bars, basing_bars, illiquid_bars):
        for bars, ticker in ((coiled_bars, "COIL"), (basing_bars, "FLAT"), (illiquid_bars, "THIN")):
            line = setups.status_line(setups.detect(ticker, bars, THRESHOLDS))
            assert line.startswith(ticker)
            assert len(line) < 200

    def test_illiquid_says_why(self, illiquid_bars):
        line = setups.status_line(setups.detect("THIN", illiquid_bars, THRESHOLDS))
        assert "liquidity floor" in line

    def test_short_history_says_why(self, short_history):
        line = setups.status_line(setups.detect("NEW", short_history, THRESHOLDS))
        assert "not enough history" in line
