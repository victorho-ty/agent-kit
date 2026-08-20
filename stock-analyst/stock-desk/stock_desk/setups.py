"""Turning measurements into a stage and a score.

One entry point, :func:`detect`, and it is deterministic: the same bars and the
same thresholds always give the same ``Setup``. Nothing here reads a clock, a
database or a network.

**The stages are a closed set**, and the report branches on them:

| stage | meaning | gets prose? |
|---|---|---|
| ``triggered`` | closed above a pivot it had been coiling under | yes |
| ``coiled`` | contracting, tight now, sitting just under a tested level | yes |
| ``failed`` | was triggered, has closed back under the pivot | yes |
| ``basing`` | in a shallow range, but not contracting or not tight yet | one line |
| ``expansion`` | volatile, no base to speak of yet | one line |
| ``none`` | nothing, or not enough history, or too illiquid to trade | one line |

The score is a 0-100 composite, and it is a *ranking* device for ordering a
morning's candidates against each other -- not a probability. Nothing here knows
which way price leaves the range, and a 90 that fails is not a bug.
"""

from __future__ import annotations

from datetime import date

from . import compression, indicators
from .models import Bar, Setup, Thresholds

STAGES = ("none", "expansion", "basing", "coiled", "triggered", "failed")

# Weights sum to 100. Contraction dominates because it is the one component that
# distinguishes a coil from a stock that is merely quiet.
WEIGHT_CONTRACTION = 25
WEIGHT_BBW = 20
WEIGHT_PROXIMITY = 15
WEIGHT_EXPANSION = 10
WEIGHT_TOUCHES = 10
WEIGHT_DRYUP = 10
WEIGHT_TIGHTNESS = 10


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _ordinal(value: float) -> str:
    """``71`` -> ``71st``. The teens are the exception every naive version gets
    wrong: 11, 12 and 13 take ``th`` despite ending in 1, 2 and 3."""
    number = int(round(value))
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _score(
    ratios: tuple[float, float, float] | None,
    bbw_percentile: float | None,
    distance_pct: float | None,
    proximity_limit: float,
    prior_expansion: bool,
    touches: int,
    dryup: float | None,
    signals: dict[str, bool],
) -> int:
    total = 0.0

    if ratios and ratios[0] > 0:
        # Full marks for halving the range across the base; nothing for widening.
        shrink = 1.0 - (ratios[2] / ratios[0])
        total += WEIGHT_CONTRACTION * _clamp(shrink / 0.5)

    if bbw_percentile is not None:
        total += WEIGHT_BBW * _clamp(1.0 - bbw_percentile / 100.0)

    if distance_pct is not None:
        if distance_pct <= 0:  # at or through the pivot
            total += WEIGHT_PROXIMITY
        elif proximity_limit > 0:
            total += WEIGHT_PROXIMITY * _clamp(1.0 - (distance_pct / 100.0) / proximity_limit)

    if prior_expansion:
        total += WEIGHT_EXPANSION

    total += WEIGHT_TOUCHES * _clamp(min(touches, 4) / 4.0)

    if dryup is not None:
        # 0.5x volume into the end of the base is full marks; flat or rising is none.
        total += WEIGHT_DRYUP * _clamp((1.0 - dryup) / 0.5)

    if signals:
        total += WEIGHT_TIGHTNESS * _clamp(sum(signals.values()) / len(signals))

    return int(round(total))


def _trend_block(bars: list[Bar]) -> dict:
    """The position metrics every ticker carries, whatever its stage.

    Computed even for ``none`` because the one-line status in the daily report is
    built from these, and a line that says only "no setup" tells the reader
    nothing about whether the stock is at highs or falling apart.
    """
    closes = [bar.close for bar in bars]
    close = closes[-1]
    sma20 = indicators.sma(closes, 20)[-1]
    sma50 = indicators.sma(closes, 50)[-1]
    sma200 = indicators.sma(closes, 200)[-1] if len(closes) >= 200 else None
    high52, low52 = indicators.week52(bars)

    position = None
    if high52 is not None and low52 is not None and high52 > low52:
        position = 100.0 * (close - low52) / (high52 - low52)

    return {
        "close": close,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "dist_sma20_pct": indicators.pct_change(close, sma20),
        "dist_sma50_pct": indicators.pct_change(close, sma50),
        "dist_sma200_pct": indicators.pct_change(close, sma200),
        "week52_high": high52,
        "week52_low": low52,
        "week52_position_pct": position,
    }


def detect(
    ticker: str,
    bars: list[Bar],
    thresholds: Thresholds | None = None,
    previous_stage: str | None = None,
    previous_pivot: float | None = None,
) -> Setup:
    """Classify the most recent bar's setup.

    ``previous_stage`` and ``previous_pivot`` come from ``setup_state`` and exist
    for exactly one purpose: recognising a breakout that has failed. Without
    yesterday's verdict, a stock back inside its range is indistinguishable from
    one that never left it -- and quietly forgetting a failed call is the habit
    the SOUL forbids.
    """
    thresholds = thresholds or Thresholds()

    if not bars:
        return Setup(ticker=ticker, as_of=date.min, stage="none", score=0, reason="no_bars")

    as_of = bars[-1].day
    if len(bars) < thresholds.min_bars:
        return Setup(
            ticker=ticker,
            as_of=as_of,
            stage="none",
            score=0,
            reason="insufficient_history",
            close=bars[-1].close,
        )

    trend = _trend_block(bars)
    close = trend["close"]
    last = len(bars) - 1

    closes = [bar.close for bar in bars]
    atr_pct_series = indicators.atr_percent(bars)
    bbw_series = indicators.bollinger_width(closes)
    donchian_series = indicators.donchian_width(bars)

    atr_pct = atr_pct_series[last]
    atr_pctile = (
        indicators.percentile_rank(
            indicators.trailing(atr_pct_series, last, thresholds.lookback_percentile), atr_pct
        )
        if atr_pct is not None
        else None
    )
    bbw = bbw_series[last]
    bbw_pctile = (
        indicators.percentile_rank(
            indicators.trailing(bbw_series, last, thresholds.lookback_percentile), bbw
        )
        if bbw is not None
        else None
    )
    donchian = donchian_series[last]
    donchian_pctile = (
        indicators.percentile_rank(
            indicators.trailing(donchian_series, last, thresholds.lookback_percentile), donchian
        )
        if donchian is not None
        else None
    )

    adv20 = indicators.average_dollar_volume(bars, 20)
    current_rvol = indicators.rvol(bars, 20)

    common = dict(
        atr_pct=atr_pct,
        atr_percentile=atr_pctile,
        bbw=bbw,
        bbw_percentile=bbw_pctile,
        donchian_percentile=donchian_pctile,
        nr7=indicators.is_nr7(bars, last),
        inside_day=indicators.is_inside_day(bars, last),
        rvol=current_rvol,
        avg_dollar_volume_20=adv20,
        **trend,
    )

    # Liquidity gate. Checked before anything else is interpreted, because a
    # perfect coil on 200k a day of turnover is not a trade, it is a trap.
    if adv20 is not None and adv20 < thresholds.min_avg_dollar_volume:
        return Setup(
            ticker=ticker, as_of=as_of, stage="none", score=0, reason="illiquid", **common
        )

    base = compression.find_base(bars, thresholds)

    if base is None:
        stage = (
            "expansion"
            if atr_pctile is not None and atr_pctile >= thresholds.expansion_percentile
            else "none"
        )
        return Setup(
            ticker=ticker,
            as_of=as_of,
            stage=stage,
            score=0,
            reason=None if stage == "expansion" else "no_base",
            **common,
        )

    pivot = compression.pivot_level(bars, base)
    if pivot is None:
        return Setup(
            ticker=ticker, as_of=as_of, stage="none", score=0, reason="no_pivot", **common
        )
    touches = compression.pivot_touches(bars, base, thresholds.pivot_tolerance)
    ratios = compression.contraction_ratios(bars, base)
    monotone = compression.is_monotone_contraction(ratios, thresholds.contraction_tolerance)
    prior_expansion = compression.had_prior_expansion(atr_pct_series, base.start_index, thresholds)
    dryup = compression.volume_dryup(bars, base)
    signals = compression.tightness_signals(bars, bbw_pctile, donchian_pctile, thresholds)

    distance_pct = indicators.pct_change(pivot, close)  # >0 means pivot is above price
    base_start_day = bars[base.start_index].day
    within_horizon = (as_of - base_start_day).days <= thresholds.technical_horizon_days

    score = _score(
        ratios=ratios,
        bbw_percentile=bbw_pctile,
        distance_pct=distance_pct,
        proximity_limit=thresholds.pivot_proximity,
        prior_expansion=prior_expansion,
        touches=touches,
        dryup=dryup,
        signals=signals,
    )

    # Stage, in precedence order. The failed check comes first because it depends
    # on yesterday's verdict rather than on today's geometry, and a stock that has
    # lost a pivot it broke will often still look like a tidy base underneath.
    volume_confirmed: bool | None = None
    if previous_stage == "triggered" and previous_pivot is not None:
        if close < previous_pivot:
            stage = "failed"
        else:
            stage = "triggered"
            volume_confirmed = (
                current_rvol >= thresholds.volume_confirm_rvol if current_rvol is not None else None
            )
    elif close > pivot:
        stage = "triggered"
        volume_confirmed = (
            current_rvol >= thresholds.volume_confirm_rvol if current_rvol is not None else None
        )
    elif (
        monotone
        and any(signals.values())
        # The squeeze gauge holds a veto. Without it a single NR7 bar can call a
        # coil while band width sits at the 94th percentile of its own year --
        # observed on real data, and flatly against the premise, which is that
        # volatility has *fallen*. Contracting swings inside a still-wide range
        # are a base, not a coil.
        and (bbw_pctile is None or bbw_pctile <= thresholds.max_bbw_percentile)
        and touches >= thresholds.min_pivot_touches
        and distance_pct is not None
        and 0 <= distance_pct / 100.0 <= thresholds.pivot_proximity
    ):
        stage = "coiled"
    else:
        stage = "basing"

    return Setup(
        ticker=ticker,
        as_of=as_of,
        stage=stage,
        score=score,
        within_horizon=within_horizon,
        pivot=pivot,
        pivot_touches=touches,
        distance_to_pivot_pct=distance_pct,
        base_start=base_start_day,
        base_length=base.length,
        base_depth_pct=100.0 * base.depth,
        contraction_ratios=ratios,
        contraction_monotone=monotone,
        prior_expansion=prior_expansion,
        volume_dryup=dryup,
        volume_confirmed=volume_confirmed,
        **common,
    )


def status_line(setup: Setup) -> str:
    """The one-line rendering for a ticker that did not earn a paragraph.

    Built here, in Python, and relayed verbatim by the agent. This function is
    the reason the daily report's cost does not scale with the size of the
    watchlist: forty tickers produce forty of these and not one model token.
    """
    parts = [f"{setup.ticker} — "]

    if setup.reason == "no_bars":
        return f"{setup.ticker} — no price data cached; run a sync"
    if setup.reason == "insufficient_history":
        return f"{setup.ticker} — not enough history yet"
    if setup.reason == "illiquid":
        adv = setup.avg_dollar_volume_20 or 0
        return f"{setup.ticker} — below the liquidity floor (20d avg ${adv/1e6:.1f}M)"

    if setup.stage == "expansion":
        parts.append("expanding, no base")
    elif setup.stage == "basing":
        depth = setup.base_depth_pct
        parts.append(f"basing {setup.base_length}d, {depth:.1f}% deep" if depth else "basing")
    elif setup.stage == "none":
        parts.append("no setup")
    else:
        parts.append(f"{setup.stage}, score {setup.score}")

    if setup.bbw_percentile is not None:
        parts.append(f", BBW {_ordinal(setup.bbw_percentile)} pct")
    if setup.dist_sma20_pct is not None:
        direction = "above" if setup.dist_sma20_pct >= 0 else "below"
        parts.append(f", {abs(setup.dist_sma20_pct):.1f}% {direction} SMA20")
    if setup.week52_position_pct is not None:
        parts.append(f", {setup.week52_position_pct:.0f}% of 52w range")

    return "".join(parts)
