"""The shapes that cross module boundaries."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime


@dataclasses.dataclass(frozen=True)
class PolicyRate:
    """Where policy is right now, straight from the New York Fed.

    ``effr`` is the published effective rate, not the midpoint of the target
    range, and the distinction is worth 4 percentage points of probability. See
    ``probabilities.py``.
    """

    effr: float
    as_of: date
    target_low: float
    target_high: float


@dataclasses.dataclass(frozen=True)
class Quote:
    """One dated 30-Day Fed Funds futures contract.

    ``source`` records which Yahoo series the price came from, per contract. It
    is carried rather than assumed because a fallback from the intraday series
    to the daily bar changes the number by about a percentage point of
    probability.
    """

    symbol: str
    price: float
    as_of: datetime | None
    source: str

    @property
    def implied_rate(self) -> float:
        """The average EFFR the contract's delivery month is priced for.

        ZQ settles to the arithmetic mean of the daily effective rate across the
        whole delivery month, so this is a month average -- never a rate on any
        particular day, and never a probability.
        """
        return 100.0 - self.price


@dataclasses.dataclass(frozen=True)
class Outcome:
    """One target-range cell of one meeting's probability distribution."""

    step: int          # 25bp increments from the current range; 0 is no change
    band_low: float
    band_high: float
    probability: float  # 0..1

    @property
    def basis_points(self) -> int:
        return self.step * 25

    @property
    def label(self) -> str:
        if self.step == 0:
            return "no change"
        direction = "hike" if self.step > 0 else "cut"
        return f"{abs(self.basis_points)}bp {direction}"


@dataclasses.dataclass(frozen=True)
class MeetingProbabilities:
    """What the market is pricing for one FOMC decision."""

    meeting_date: date
    ordinal: int              # 1 is the next meeting, 2 the one after it
    contract: str
    price: float
    implied_rate: float
    expected_rate: float      # the probability-weighted post-meeting rate
    method: str               # which inversion produced it; see probabilities.py
    outcomes: tuple[Outcome, ...]
