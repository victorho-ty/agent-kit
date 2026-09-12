"""The FedWatch calculation. Pure stdlib over floats -- no network, no clock.

This is the whole reason the bundle does not scrape. CME publishes the
methodology, and it is arithmetic over a public futures price, so the numbers
can be recomputed here for the cost of two HTTP calls.

**What a ZQ price means.** The contract settles to the arithmetic mean of the
daily effective federal funds rate across its whole delivery month, so
``100 - price`` is a *month average*, never a rate on any particular day. Every
step below exists to get from that average to the rate the market expects
*after* a specific decision.

**Two ways to recover the post-meeting rate**, and the choice matters:

``blend_inversion``
    A month containing a decision on day ``d`` of ``n`` runs ``d`` days at the
    old rate and ``n - d`` at the new one, because a new target range is
    effective the day after the announcement, so::

        avg = (d/n) * r_start + ((n-d)/n) * r_end

    which inverts for ``r_end``. The inversion divides by ``(n-d)/n``, so it
    amplifies any error in the price by ``n/(n-d)``. For a meeting on the 28th
    of a 31-day month that is a factor of ten, and the answer is worthless.

``clean_next_month``
    When the *following* month holds no decision, its contract runs at one
    constant rate for its whole length, so ``100 - price`` **is** the
    post-meeting rate -- no inversion, no amplification.

Preferring ``clean_next_month`` wherever the calendar allows it is what keeps a
late-month meeting -- 28 October, say -- as trustworthy as a mid-month one.

**Why the effective rate and not the midpoint of the target range.** Validated
against CME's own published table for the 16 September 2026 meeting, using
CME's mid price of 96.2688:

    r_start                     no change   +25bp
    3.625% (range midpoint)          9.0%   91.0%
    3.63%  (published EFFR)         13.3%   86.7%
    CME FedWatch                    13.3%   86.7%

Half a basis point of ``r_start`` is four percentage points of probability. The
effective rate is published daily; assuming the midpoint is a four-point error
for no reason.

**The one modelling simplification.** For the second meeting the increment the
market prices is applied uniformly across the first meeting's branches rather
than solved conditionally per branch. Over a two-meeting horizon with a single
25bp step in play the difference is immaterial, but it is a simplification and
``method`` in the payload carries it.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from .models import MeetingProbabilities, Outcome, PolicyRate, Quote

# The FOMC has not moved by anything other than a multiple of this in decades,
# and FedWatch's whole presentation assumes it.
STEP = 0.25

# Above this, `blend_inversion` multiplies price error enough that the result
# should not travel without saying so.
AMPLIFICATION_WARNING = 3.0


def post_meeting_rate(month_average: float, r_start: float, meeting: date) -> float:
    """Invert the month-average blend for the month holding the decision."""
    days = monthrange(meeting.year, meeting.month)[1]
    elapsed = meeting.day
    remaining = days - elapsed
    if remaining <= 0:
        raise ValueError(
            f"a decision on the last day of {meeting:%B %Y} leaves no days at the new rate"
        )
    return (month_average - (elapsed / days) * r_start) / (remaining / days)


def amplification(meeting: date) -> float:
    """How much :func:`post_meeting_rate` multiplies an error in the price."""
    days = monthrange(meeting.year, meeting.month)[1]
    remaining = days - meeting.day
    return float("inf") if remaining <= 0 else days / remaining


def distribute(move: float, step: float = STEP) -> dict[int, float]:
    """Spread a rate move across the two adjacent 25bp increments bracketing it.

    ``move`` is in percentage points and may be negative. A move of +0.217
    becomes ``{0: 0.132, 1: 0.868}`` -- a 13.2% chance of no change and an 86.8%
    chance of a 25bp hike. Floor division is deliberate and correct for cuts:
    -0.15 floors to -1, leaving ``{-1: 0.6, 0: 0.4}``.
    """
    steps = move / step
    lower = int(steps // 1)
    fraction = steps - lower
    if fraction == 0.0:
        return {lower: 1.0}
    return {lower: 1.0 - fraction, lower + 1: fraction}


def combine(tree: dict[int, float], increment: dict[int, float]) -> dict[int, float]:
    """Convolve a further move onto an existing distribution of outcomes."""
    combined: dict[int, float] = {}
    for base, base_probability in tree.items():
        for move, move_probability in increment.items():
            combined[base + move] = (
                combined.get(base + move, 0.0) + base_probability * move_probability
            )
    return combined


def _outcomes(tree: dict[int, float], policy: PolicyRate) -> tuple[Outcome, ...]:
    """The distribution as a contiguous table of target ranges.

    Every step between the extremes is emitted even at zero probability, and
    "no change" always appears. A band the market has ruled out is a finding;
    an absent row just looks like a gap in the data.
    """
    steps = range(min(min(tree), 0), max(max(tree), 0) + 1)
    return tuple(
        Outcome(
            step=step,
            band_low=round(policy.target_low + step * STEP, 4),
            band_high=round(policy.target_high + step * STEP, 4),
            probability=tree.get(step, 0.0),
        )
        for step in steps
    )


def _expected_rate(tree: dict[int, float], r_start: float) -> float:
    return r_start + sum(probability * step * STEP for step, probability in tree.items())


def plan(meeting: date, month_has_meeting) -> tuple[str, str]:
    """Which method and which contract symbol this meeting needs.

    ``month_has_meeting(year, month)`` answers whether a month holds a decision.
    It is a callable so this module never imports the calendar loader and stays
    free of I/O.
    """
    from . import contracts

    year, month = contracts.next_month(meeting.year, meeting.month)
    if not month_has_meeting(year, month):
        return "clean_next_month", contracts.symbol(year, month)
    return "blend_inversion", contracts.symbol_for_meeting(meeting)


def solve(
    policy: PolicyRate,
    meetings: list[date],
    quotes: dict[str, Quote],
    month_has_meeting,
) -> list[MeetingProbabilities]:
    """Probabilities for each meeting in order, each conditioned on the last.

    ``meetings`` must be ascending. A meeting whose contract is missing from
    ``quotes`` ends the chain: every later meeting is measured from this one's
    expected rate, so continuing from a rate we could not read would produce
    confident numbers built on a guess.
    """
    resolved: list[MeetingProbabilities] = []
    r_start = policy.effr
    tree: dict[int, float] = {0: 1.0}

    for ordinal, meeting in enumerate(meetings, start=1):
        method, wanted = plan(meeting, month_has_meeting)
        quote = quotes.get(wanted)
        if quote is None:
            break

        if method == "clean_next_month":
            r_end = quote.implied_rate
        else:
            r_end = post_meeting_rate(quote.implied_rate, r_start, meeting)

        tree = combine(tree, distribute(r_end - r_start))
        resolved.append(
            MeetingProbabilities(
                meeting_date=meeting,
                ordinal=ordinal,
                contract=quote.symbol,
                price=quote.price,
                implied_rate=round(quote.implied_rate, 6),
                expected_rate=round(_expected_rate(tree, policy.effr), 6),
                method=method,
                outcomes=_outcomes(tree, policy),
            )
        )
        r_start = r_end

    return resolved
