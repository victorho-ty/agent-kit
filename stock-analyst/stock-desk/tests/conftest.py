"""Synthetic bar builders.

Every fixture here is hand-specified: a list of ``(close, spread, volume)``
triples turned into bars on consecutive business days. Nothing is random and
nothing is downloaded, so a test that fails has failed because the maths
changed.

The scenario builders compose one shape the detector is supposed to recognise:

    200 quiet bars -> 15 volatile bars rising hard -> 3 bars pulling back deep
    -> a base of 16 bars whose swings shrink in three steps

The deep pullback is load-bearing. Without it the base search walks backwards
into the rally, and the "base" becomes sixty bars long -- which is a fine thing
for a real detector to find, but makes a test that means to check contraction
quietly check something else instead.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_desk.models import Bar

START = date(2024, 1, 1)


def business_days(count: int, start: date = START) -> list[date]:
    """``count`` consecutive weekdays. Holidays are irrelevant to the maths."""
    days: list[date] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def build(specs: list[tuple[float, float, float]], start: date = START) -> list[Bar]:
    """``(close, spread, volume)`` triples into bars.

    The bar is drawn symmetrically around its close, so ``high = close +
    spread/2``. That keeps every range exactly ``spread`` and makes the
    contraction arithmetic in the tests something a reader can verify by hand.
    """
    days = business_days(len(specs), start)
    bars: list[Bar] = []
    for index, (close, spread, volume) in enumerate(specs):
        previous_close = specs[index - 1][0] if index else close
        bars.append(
            Bar(
                day=days[index],
                open=previous_close,
                high=close + spread / 2,
                low=close - spread / 2,
                close=close,
                volume=volume,
            )
        )
    return bars


def quiet_run(count: int = 200, price: float = 105.0, volume: float = 2_000_000) -> list:
    """The background the squeeze is measured against.

    "Quiet" is relative, and two readings have to come out right at once:

    * **Band width** must be *wider* here than in the base, because band width is
      only ever read as a percentile of its own history. Against a dead-flat
      background every base ranks high and the squeeze veto rejects a genuine
      coil. That is what the +/-2.5 close oscillation buys.
    * **ATR%** must be *lower* here than in the rally that follows, or the
      prior-expansion test finds the background more volatile than the expansion
      and the whole premise inverts. That caps how wide the oscillation can go.

    Close dispersion drives the first and bar-to-bar true range drives the
    second, so they are separated by *pacing*: a four-bar triangular cycle
    travels the same distance as a two-bar one but in half-steps, which keeps
    band width wide while halving true range.
    """
    cycle = (-2.5, 0.0, 2.5, 0.0)
    return [(price + cycle[index % 4], 1.0, volume) for index in range(count)]


def expansion_run() -> list:
    """Fifteen bars rising 110 -> 133 on wide ranges: the 'high volatility' leg."""
    return [(110.0 + index * 1.55, 8.0, 4_000_000) for index in range(15)]


def deep_pullback() -> list:
    """Three bars diving to a low of 110, immediately before the base begins.

    The order matters: the *last* of these is the deepest, so the bar adjacent to
    the base is the one that breaks the depth limit. Put the deep bar earlier and
    the base search steps straight over it.
    """
    return [(120.0, 4.0, 5_000_000), (116.0, 4.0, 4_500_000), (112.0, 4.0, 3_500_000)]


def steep_rally(count: int = 15, start: float = 100.0, step: float = 4.3) -> list:
    """A move too fast for any window of seven bars to fit inside 15%.

    ``expansion_run`` is deliberately gentler, and a nine-bar window at the top of
    it really does qualify as a shallow base -- correct behaviour, but useless for
    testing the no-base branch.

    Ranges widen strictly, never repeating. A cycling pattern ties for narrowest
    every few bars, and NR7 counts a tie as narrowest -- right for real bars,
    where exact ties are vanishingly rare, but it makes a synthetic rally trip a
    tightness signal it has no business tripping.
    """
    return [
        (start + index * step, 10.0 + index * 0.4, 4_000_000) for index in range(count)
    ]


# Twenty-one bars in three sevens: wide, medium, tight. Highs peak at 133.0 on
# the second bar, which is the pivot every assertion below is written against.
#
# Twenty-one and not sixteen because Bollinger band width is a 20-bar window. A
# shorter base leaves that window straddling the pullback behind it, so the
# reading describes the dive rather than the base -- and the squeeze veto then
# rejects a coil the fixture means to be genuine.
CONTRACTING_BASE = [
    (128.0, 4.0, 3_000_000),
    (131.0, 4.0, 3_000_000),
    (127.5, 4.0, 3_000_000),
    (130.0, 4.0, 3_000_000),
    (128.0, 4.0, 3_000_000),
    (130.5, 4.0, 3_000_000),
    (128.5, 4.0, 3_000_000),
    (129.5, 2.5, 2_000_000),
    (131.0, 2.5, 2_000_000),
    (129.0, 2.5, 2_000_000),
    (130.5, 2.5, 2_000_000),
    (129.5, 2.5, 2_000_000),
    (130.5, 2.5, 2_000_000),
    (130.0, 2.5, 2_000_000),
    (130.5, 1.2, 1_200_000),
    (131.0, 1.2, 1_200_000),
    (130.75, 1.2, 1_200_000),
    (131.0, 1.2, 1_200_000),
    (130.8, 1.2, 1_200_000),
    (130.9, 1.2, 1_200_000),
    (130.8, 1.2, 1_200_000),
]

# Same depth and the same pivot, but no contraction whatsoever: price oscillates
# between two levels at constant amplitude for the whole window.
#
# Reusing CONTRACTING_BASE's closes with a fixed spread does NOT give this. Those
# closes converge on their own -- 128/131 early, 130.5/131 late -- so the thirds
# contract even with every spread identical, and the fixture ends up testing the
# opposite of what it claims.
FLAT_BASE = [
    (128.0 if index % 2 == 0 else 131.5, 3.0, 2_000_000)
    for index in range(len(CONTRACTING_BASE))
]

PIVOT = 133.0


@pytest.fixture
def coiled_bars() -> list[Bar]:
    """The full sequence, ending coiled just under the pivot."""
    return build(quiet_run() + expansion_run() + deep_pullback() + CONTRACTING_BASE)


@pytest.fixture
def basing_bars() -> list[Bar]:
    """Identical except the base never tightens."""
    return build(quiet_run() + expansion_run() + deep_pullback() + FLAT_BASE)


@pytest.fixture
def triggered_bars() -> list[Bar]:
    """The coil plus one bar closing clear of the pivot on heavy volume."""
    return build(
        quiet_run()
        + expansion_run()
        + deep_pullback()
        + CONTRACTING_BASE
        + [(136.0, 2.0, 5_000_000)]
    )


@pytest.fixture
def illiquid_bars() -> list[Bar]:
    """The same coil on a thousand shares a day."""
    specs = [
        (close, spread, 1_000)
        for close, spread, _ in quiet_run() + expansion_run() + deep_pullback() + CONTRACTING_BASE
    ]
    return build(specs)


@pytest.fixture
def short_history() -> list[Bar]:
    return build(quiet_run(count=50))
