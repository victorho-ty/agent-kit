"""Finding the coil: high volatility, then a base, then swings that shrink.

This module answers one question and does it with no side effects: *given these
bars, is price squeezing toward a level, and how tightly?* It knows nothing about
tickers, databases, reports or time of day. :mod:`setups` assembles its answers
into a stage and a score.

The sequence being detected is the operator's own definition, in order:

1. **High volatility** -- there was a real move before this, not a stock that has
   drifted sideways for a year. Measured as ATR% in the 20 sessions before the
   base, ranked against the ticker's own trailing year.
2. **Consolidation** -- a recent window whose whole range fits inside a shallow
   band. Its high is the pivot.
3. **Progressively smaller swings** -- the load-bearing test. Split the base in
   three and require each third to be tighter than the one before. A range that
   is merely *narrow* is a dull stock; a range that is narrow *and still
   narrowing* is a coil.
4. **Squeezed toward a key level** -- price sitting just under a high it has
   already tested more than once, not floating in the middle of the band.

A base that passes 2 but fails 3 is `basing`, which is a fine thing to be and
not worth writing a paragraph about. That distinction is most of why the daily
report stays short.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, median

from . import indicators
from .models import Bar, Thresholds


@dataclass(frozen=True, slots=True)
class Base:
    """A consolidation window ending at the most recent bar."""

    start_index: int
    end_index: int
    high: float
    low: float
    depth: float

    @property
    def length(self) -> int:
        return self.end_index - self.start_index + 1


def find_base(bars: list[Bar], thresholds: Thresholds) -> Base | None:
    """The longest recent window whose depth stays inside ``max_base_depth``.

    Depth is monotone in window length -- extending backwards can only raise the
    high or lower the low -- so the longest qualifying window is found by walking
    outwards until the band breaks, with no search needed.

    Longest, not shortest, on purpose: every sideways stretch contains a tight
    three-day window, and taking the shortest would report a base on everything.
    """
    if len(bars) < thresholds.min_base_len:
        return None

    end = len(bars) - 1
    best: Base | None = None
    highest = float("-inf")
    lowest = float("inf")

    for length in range(1, min(thresholds.max_base_len, len(bars)) + 1):
        bar = bars[end - length + 1]
        highest = max(highest, bar.high)
        lowest = min(lowest, bar.low)
        if highest <= 0:
            break
        depth = (highest - lowest) / highest
        if depth > thresholds.max_base_depth:
            break
        if length >= thresholds.min_base_len:
            best = Base(
                start_index=end - length + 1,
                end_index=end,
                high=highest,
                low=lowest,
                depth=depth,
            )
    return best


def pivot_level(bars: list[Bar], base: Base) -> float | None:
    """The resistance the base has been testing -- its high, *excluding today*.

    Excluding the current bar is not a detail. A pivot is a level established by
    prior action; if today's own high defines it, then on the day price finally
    clears the range the pivot rises with it and the breakout can never be
    detected. Depth still uses the whole window, because how deep the range is
    genuinely includes today.
    """
    if base.length < 2:
        return None
    return max(bar.high for bar in bars[base.start_index : base.end_index])


def pivot_touches(bars: list[Bar], base: Base, tolerance: float) -> int:
    """How many prior bars in the base reached within ``tolerance`` of the pivot.

    A level tested once is an accident of a single day's high. Tested twice or
    more, it is a place sellers have actually shown up -- which is what makes a
    breakout through it mean something. Counts the same bars the pivot is drawn
    from, so today's action never inflates the count of its own level.
    """
    pivot = pivot_level(bars, base)
    if pivot is None:
        return 0
    threshold = pivot * (1.0 - tolerance)
    return sum(1 for bar in bars[base.start_index : base.end_index] if bar.high >= threshold)


def contraction_ratios(bars: list[Bar], base: Base) -> tuple[float, float, float] | None:
    """Range of each third of the base, normalised by that third's mean close.

    Normalising matters: a base that drifts 10% higher across its length would
    otherwise show a shrinking range purely because the denominator moved.
    """
    if base.length < 3:
        return None
    window = bars[base.start_index : base.end_index + 1]
    size = len(window) // 3
    if size < 1:
        return None
    # Any remainder goes to the middle third, so the first and last -- the two
    # being compared for the headline contraction -- always cover equal spans.
    thirds = [window[:size], window[size : len(window) - size], window[len(window) - size :]]

    ratios = []
    for part in thirds:
        mean_close = fmean(bar.close for bar in part)
        if not mean_close:
            return None
        ratios.append((max(b.high for b in part) - min(b.low for b in part)) / mean_close)
    return ratios[0], ratios[1], ratios[2]


def is_monotone_contraction(
    ratios: tuple[float, float, float] | None, tolerance: float
) -> bool:
    """Each third strictly tighter than the last, by at least ``1 - tolerance``.

    The tolerance stops a 0.1% difference from counting as contraction; with the
    default 0.98 each third must be at most 98% of the one before it.
    """
    if ratios is None:
        return False
    first, second, third = ratios
    if first <= 0 or second <= 0:
        return False
    return second <= first * tolerance and third <= second * tolerance


def had_prior_expansion(
    atr_pct: indicators.Series, base_start: int, thresholds: Thresholds
) -> bool:
    """Was volatility elevated in the 20 sessions before the base began?

    This is what separates "coiled after a move" from "asleep for a year". A
    stock that never expanded has no energy to release, and its tight range is
    just its normal condition.
    """
    if base_start < 20:
        return False
    pre = [value for value in atr_pct[base_start - 20 : base_start] if value is not None]
    if not pre:
        return False
    history = indicators.trailing(atr_pct, base_start - 1, thresholds.lookback_percentile)
    if len(history) < 20:
        return False

    pre_mean = fmean(pre)
    # The percentile alone is not enough. `percentile_rank` counts ties with
    # `<=`, so on a stock whose volatility barely varies the run-up ranks near
    # the 100th percentile while being materially no different -- reporting a
    # prior expansion for a stock that has never expanded in its life. A bare
    # "greater than the median" does not fix it either: ATR% is ATR over close,
    # so an oscillating close alone lifts the mean above the median by a
    # fraction of a basis point. The run-up has to clear the median by a real
    # margin, which a genuine expansion does several times over.
    midpoint = median(history)
    if midpoint <= 0 or pre_mean < midpoint * thresholds.expansion_margin:
        return False
    return indicators.percentile_rank(history, pre_mean) >= thresholds.expansion_percentile


def volume_dryup(bars: list[Bar], base: Base) -> float | None:
    """Volume in the last third of the base against the first third.

    Below 1.0 means participation is draining out of the range, which is the
    condition that precedes a real expansion. Above 1.0 during a tightening range
    usually means distribution, and the breakout tends not to hold.
    """
    window = bars[base.start_index : base.end_index + 1]
    size = len(window) // 3
    if size < 1:
        return None
    early = fmean(bar.volume for bar in window[:size])
    late = fmean(bar.volume for bar in window[len(window) - size :])
    if not early:
        return None
    return late / early


def tightness_signals(
    bars: list[Bar],
    bbw_percentile: float | None,
    donchian_pct: float | None,
    thresholds: Thresholds,
) -> dict[str, bool]:
    """The corroborating evidence that the range is genuinely tight *now*.

    Progressive contraction describes the shape of the whole base; these describe
    the last few bars. At least one is required, because a base can contract
    steadily and then loosen again in its final week.
    """
    last = len(bars) - 1
    return {
        "nr7": any(indicators.is_nr7(bars, index) for index in range(max(0, last - 2), last + 1)),
        "inside_day": any(
            indicators.is_inside_day(bars, index) for index in range(max(0, last - 1), last + 1)
        ),
        "bbw_squeeze": bbw_percentile is not None and bbw_percentile <= thresholds.bbw_percentile,
        "donchian_squeeze": (
            donchian_pct is not None and donchian_pct <= thresholds.donchian_percentile
        ),
    }
