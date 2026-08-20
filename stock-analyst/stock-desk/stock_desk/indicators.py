"""Indicator maths, as pure functions over lists of floats.

No pandas, no numpy, no clock, no network. Everything these functions need is an
argument, which is what makes the setup detector testable on twenty hand-written
bars instead of a market data subscription.

**Every series returned is the same length as its input**, left-padded with
``None`` for the bars where the indicator is not yet defined. Aligning by index
rather than by trimming is the thing that stops off-by-one errors from silently
comparing today's price to last week's average.
"""

from __future__ import annotations

from statistics import fmean, pstdev

from .models import Bar

Series = list[float | None]


def _window(values: list[float], end: int, period: int) -> list[float] | None:
    """The ``period`` values ending at and including index ``end``."""
    start = end - period + 1
    if start < 0:
        return None
    return values[start : end + 1]


def sma(values: list[float], period: int) -> Series:
    """Simple moving average. ``None`` until ``period`` values exist."""
    out: Series = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            out[index] = running / period
    return out


def rolling_stdev(values: list[float], period: int) -> Series:
    """Population standard deviation over a rolling window.

    Population, not sample: the window *is* the whole set being described, and
    Bollinger Bands are conventionally computed this way. Using the sample
    deviation would widen every band by a factor of sqrt(n/(n-1)) and shift the
    band-width percentiles the detector thresholds on.
    """
    out: Series = [None] * len(values)
    for index in range(len(values)):
        window = _window(values, index, period)
        if window is not None:
            out[index] = pstdev(window)
    return out


def true_range(bars: list[Bar]) -> list[float]:
    """Wilder's true range. The first bar has no previous close, so it is the
    plain high-low -- an assumption that washes out well before ATR(14) is used."""
    out: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            out.append(bar.high - bar.low)
            continue
        previous_close = bars[index - 1].close
        out.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return out


def atr(bars: list[Bar], period: int = 14) -> Series:
    """Average true range, Wilder-smoothed.

    Seeded with a simple mean of the first ``period`` true ranges, then smoothed.
    That is Wilder's own definition and it is what every charting package shows;
    an EMA-seeded variant would disagree with the user's screen by a few percent
    forever.
    """
    out: Series = [None] * len(bars)
    if len(bars) < period or period <= 0:
        return out
    ranges = true_range(bars)
    seed = fmean(ranges[:period])
    out[period - 1] = seed
    current = seed
    for index in range(period, len(bars)):
        current = (current * (period - 1) + ranges[index]) / period
        out[index] = current
    return out


def atr_percent(bars: list[Bar], period: int = 14) -> Series:
    """ATR as a fraction of close -- the comparable form.

    A $4 ATR means something different on a $30 stock and a $900 one, and the
    compression test compares a ticker against its own history *and* against a
    threshold shared with every other ticker.
    """
    values = atr(bars, period)
    return [
        (value / bars[index].close) if value is not None and bars[index].close else None
        for index, value in enumerate(values)
    ]


def bollinger_width(closes: list[float], period: int = 20, multiple: float = 2.0) -> Series:
    """Band width as a fraction of the middle band: ``(upper - lower) / mid``.

    This is the squeeze gauge. Its *absolute* value says little -- a quiet utility
    and a biotech live in different ranges -- so the detector always uses its
    percentile against the ticker's own trailing year.
    """
    mids = sma(closes, period)
    deviations = rolling_stdev(closes, period)
    out: Series = [None] * len(closes)
    for index, (mid, deviation) in enumerate(zip(mids, deviations)):
        if mid is None or deviation is None or mid == 0:
            continue
        out[index] = (2 * multiple * deviation) / mid
    return out


def donchian_width(bars: list[Bar], period: int = 20) -> Series:
    """Channel height as a fraction of close: ``(highest high - lowest low) / close``."""
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    out: Series = [None] * len(bars)
    for index in range(len(bars)):
        high_window = _window(highs, index, period)
        low_window = _window(lows, index, period)
        if high_window is None or low_window is None or not bars[index].close:
            continue
        out[index] = (max(high_window) - min(low_window)) / bars[index].close
    return out


def percentile_rank(history: list[float], value: float) -> float:
    """Where ``value`` sits in ``history``, as 0-100.

    The fraction of observations at or below it. 0 means nothing in the trailing
    window was ever this low, which is exactly the reading a maximal squeeze
    produces.
    """
    if not history:
        return 50.0
    at_or_below = sum(1 for item in history if item <= value)
    return 100.0 * at_or_below / len(history)


def trailing(series: Series, end: int, lookback: int) -> list[float]:
    """The defined values of ``series`` in the ``lookback`` bars ending at ``end``."""
    start = max(0, end - lookback + 1)
    return [value for value in series[start : end + 1] if value is not None]


def is_nr7(bars: list[Bar], index: int) -> bool:
    """Narrowest range of the last seven bars -- the classic pre-breakout bar."""
    if index < 6:
        return False
    window = bars[index - 6 : index + 1]
    return bars[index].range <= min(bar.range for bar in window)


def is_inside_day(bars: list[Bar], index: int) -> bool:
    """Entirely inside the previous bar's range: no new information, both ways."""
    if index < 1:
        return False
    current, previous = bars[index], bars[index - 1]
    return current.high <= previous.high and current.low >= previous.low


def average_volume(bars: list[Bar], period: int = 20, end: int | None = None) -> float | None:
    end = len(bars) - 1 if end is None else end
    window = _window([bar.volume for bar in bars], end, period)
    return fmean(window) if window else None


def average_dollar_volume(bars: list[Bar], period: int = 20, end: int | None = None) -> float | None:
    """The liquidity gate. A perfect setup on something that trades $200k a day
    is not tradeable, and reporting it wastes the reader's morning."""
    end = len(bars) - 1 if end is None else end
    window = _window([bar.dollar_volume for bar in bars], end, period)
    return fmean(window) if window else None


def rvol(bars: list[Bar], period: int = 20, end: int | None = None) -> float | None:
    """Today's volume against its own recent average.

    Computed on the ``period`` bars *before* the one being measured, so a huge
    day does not dilute the baseline it is being compared against.
    """
    end = len(bars) - 1 if end is None else end
    if end < period:
        return None
    baseline = fmean([bar.volume for bar in bars[end - period : end]])
    if not baseline:
        return None
    return bars[end].volume / baseline


def week52(bars: list[Bar]) -> tuple[float | None, float | None]:
    """High and low of the trailing 252 sessions, or of everything if shorter."""
    window = bars[-252:] if len(bars) > 252 else bars
    if not window:
        return None, None
    return max(bar.high for bar in window), min(bar.low for bar in window)


def pct_change(value: float | None, reference: float | None) -> float | None:
    """``value`` relative to ``reference``, as a percentage. Order matters:
    a positive result means ``value`` is above ``reference``."""
    if value is None or reference is None or reference == 0:
        return None
    return 100.0 * (value - reference) / reference
