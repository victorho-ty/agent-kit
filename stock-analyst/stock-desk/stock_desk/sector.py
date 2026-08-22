"""Is this one name, or the whole group?

The question a breakout trader actually needs answered about a sector, and the
reason a list of theme keywords was never enough to answer it. Keywords find
articles. This finds out whether the peers moved too, which needs prices -- so a
sector here is a list of member tickers.

Three readings come out of it, and they are different questions:

**Standing** -- where this name sits against its own group over the horizon.
Leading, in line, or lagging, with the gap in percentage points.

**Cohesion** -- whether the group is moving as a bloc or scattering. A bloc is a
theme or a macro input acting on all of them; scatter means whatever moved this
name was about this name.

**Breadth** -- how many members are up. A sector where one name is up 30% and
five are flat is not a sector that is working, however good its median looks.

The combination is what carries information. A name leading a cohesive, broad
sector is being carried; the same name leading a scattered one did something.
Per the profile's own doctrine a name breaking out alone is a different trade
from a name breaking out with its group -- this module is what tells them apart.

Everything here is a pure function over ``list[Bar]``. No network, no clock, no
database: a fixture is a handful of bars, which is what makes the arithmetic
testable without a market being open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from statistics import median, pstdev

from .indicators import pct_change
from .models import Bar, SectorConfig

# Percentage points. A member within this of the group median is "in line" --
# the band exists because a comparative reading accurate to a tenth of a point
# invites a confidence nobody should have in it.
IN_LINE_BAND = 3.0

# Dispersion (population stdev of member returns, in percentage points) below
# which the group is moving together. Above the wide threshold, calling it a
# "sector move" is not supportable.
BLOC_DISPERSION = 4.0
SCATTER_DISPERSION = 10.0

# Fraction of members pointing the same way for breadth to count as one-sided.
ONE_SIDED = 0.7

MIN_MEMBERS = 2


@dataclass(frozen=True, slots=True)
class SectorView:
    """What the group did over one horizon. Measurement, not interpretation."""

    name: str
    as_of: date | None
    horizon_days: int
    returns: dict[str, float] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    median_return: float | None = None
    dispersion: float | None = None
    cohesion: str = "unknown"  # bloc | mixed | scattered | unknown
    breadth_up: int = 0
    breadth_total: int = 0
    leaders: tuple[str, ...] = ()
    laggards: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return len(self.returns) >= MIN_MEMBERS

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "horizon_days": self.horizon_days,
            "returns": {k: round(v, 2) for k, v in self.returns.items()},
            "missing": list(self.missing),
            "median_return": None if self.median_return is None else round(self.median_return, 2),
            "dispersion": None if self.dispersion is None else round(self.dispersion, 2),
            "cohesion": self.cohesion,
            "breadth": f"{self.breadth_up}/{self.breadth_total}",
            "leaders": list(self.leaders),
            "laggards": list(self.laggards),
            "basis": "derived: close-to-close over the horizon, from cached bars",
        }


def horizon_return(bars: list[Bar], days: int) -> float | None:
    """Close-to-close percentage change over the last ``days`` *sessions*.

    Sessions, not calendar days: the bars are what exist, so a holiday shortens
    the window rather than silently reaching further back. Too little history
    returns None, which the caller reports as missing rather than as zero -- a
    member quietly counted as flat drags the median toward nothing.
    """
    if days < 1 or len(bars) < 2:
        return None
    window = bars[-(days + 1) :] if len(bars) > days else bars
    return pct_change(window[-1].close, window[0].close)


def _cohesion(dispersion: float | None) -> str:
    if dispersion is None:
        return "unknown"
    if dispersion <= BLOC_DISPERSION:
        return "bloc"
    if dispersion >= SCATTER_DISPERSION:
        return "scattered"
    return "mixed"


def analyse(
    sector: SectorConfig,
    bars_by_ticker: dict[str, list[Bar]],
    horizon_days: int,
) -> SectorView:
    """Measure one sector over one horizon.

    Members with no usable history are named in ``missing`` rather than dropped
    silently: a three-member sector reporting on one member is a different claim
    from a three-member sector reporting on three, and the reader cannot tell
    which they are looking at unless it is stated.
    """
    returns: dict[str, float] = {}
    missing: list[str] = []
    as_of: date | None = None

    for ticker in sector.members:
        bars = bars_by_ticker.get(ticker) or []
        value = horizon_return(bars, horizon_days)
        if value is None:
            missing.append(ticker)
            continue
        returns[ticker] = value
        if bars and (as_of is None or bars[-1].day > as_of):
            as_of = bars[-1].day

    if len(returns) < MIN_MEMBERS:
        return SectorView(
            name=sector.name,
            as_of=as_of,
            horizon_days=horizon_days,
            returns=returns,
            missing=tuple(missing),
        )

    values = list(returns.values())
    mid = median(values)
    spread = pstdev(values)
    ordered = sorted(returns.items(), key=lambda kv: -kv[1])

    return SectorView(
        name=sector.name,
        as_of=as_of,
        horizon_days=horizon_days,
        returns=returns,
        missing=tuple(missing),
        median_return=mid,
        dispersion=spread,
        cohesion=_cohesion(spread),
        breadth_up=sum(1 for v in values if v > 0),
        breadth_total=len(values),
        leaders=tuple(t for t, v in ordered if v > mid + IN_LINE_BAND),
        laggards=tuple(t for t, v in ordered if v < mid - IN_LINE_BAND),
    )


def standing(view: SectorView, ticker: str) -> dict | None:
    """Where one name sits against its group, and what that implies.

    ``carried`` is the field worth reading. True means the group moved as a bloc
    and this name went with it -- the move is the sector's, not the company's,
    and a breakout on it deserves less conviction than the chart alone suggests.
    """
    if not view.usable or ticker not in view.returns:
        return None
    own = view.returns[ticker]
    gap = own - (view.median_return or 0.0)
    if gap > IN_LINE_BAND:
        position = "leading"
    elif gap < -IN_LINE_BAND:
        position = "lagging"
    else:
        position = "in_line"
    return {
        "sector": view.name,
        "position": position,
        "own_return": round(own, 2),
        "sector_median": round(view.median_return or 0.0, 2),
        "gap_pct_points": round(gap, 2),
        "cohesion": view.cohesion,
        "breadth": f"{view.breadth_up}/{view.breadth_total}",
        # Moving with a group that is moving together, and not out in front of
        # it: the move belongs to the sector.
        "carried": view.cohesion == "bloc" and position == "in_line",
        "basis": "derived: close-to-close over the horizon, from cached bars",
    }


def line(view: SectorView) -> str:
    """One finished sentence for a sector, relayed verbatim."""
    if not view.usable:
        have = len(view.returns)
        want = have + len(view.missing)
        return f"{view.name}: not enough history ({have} of {want} members priced)."

    shape = {
        "bloc": "moving together",
        "mixed": "mixed",
        "scattered": "scattered",
        "unknown": "unclear",
    }[view.cohesion]
    parts = [
        f"{view.name}: median {view.median_return:+.1f}% over {view.horizon_days}d, "
        f"{view.breadth_up}/{view.breadth_total} up, {shape}"
    ]
    if view.leaders:
        parts.append(f"leading {', '.join(view.leaders)}")
    if view.laggards:
        parts.append(f"lagging {', '.join(view.laggards)}")
    if view.missing:
        parts.append(f"no data for {', '.join(view.missing)}")
    return "; ".join(parts) + "."


def news_by_sector(
    stories, sectors: tuple[SectorConfig, ...]
) -> dict[str, list]:
    """Group already-filtered stories by the sector their ticker belongs to.

    A ticker in two sectors puts its story in both. That is duplication on
    purpose: the two sections answer different questions, and dropping it from
    one to avoid repeating it would silently make that sector look quiet.
    """
    grouped: dict[str, list] = {sector.name: [] for sector in sectors}
    for story in stories:
        ticker = story.items[0].ticker if story.items else None
        if not ticker:
            continue
        for sector in sectors:
            if ticker in sector.members:
                grouped[sector.name].append(story)
    return {name: items for name, items in grouped.items() if items}
