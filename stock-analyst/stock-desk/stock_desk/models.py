"""The shapes that move between layers.

Frozen dataclasses, not dicts, for everything that crosses a module boundary.
The detector takes ``list[Bar]`` and returns a ``Setup``; only :mod:`cli` turns
those into the JSON the agent reads. That boundary is the whole reason the maths
is testable -- a fixture is a list of ``Bar``, not a database.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class Bar:
    """One daily OHLCV bar. ``adj_close`` may equal ``close`` for HK feeds."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: float | None = None

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def dollar_volume(self) -> float:
        return self.close * self.volume


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Every tunable the detector has, in one object.

    Defaults are the ones documented in ``references/setups.md``. They are
    deliberately not scattered as module constants: a threshold nobody can find
    is a threshold nobody revises after a run of false positives.
    """

    min_bars: int = 120
    lookback_percentile: int = 252
    min_base_len: int = 7
    max_base_len: int = 60
    max_base_depth: float = 0.15
    pivot_tolerance: float = 0.02
    min_pivot_touches: int = 2
    contraction_tolerance: float = 0.98
    expansion_percentile: float = 66.0
    # The run-up must be this many times the median ATR%, not merely rank above
    # it. Ranking alone passes on a hair's difference.
    expansion_margin: float = 1.15
    bbw_percentile: float = 20.0
    donchian_percentile: float = 25.0
    # A ceiling, not a target. Above this the range is demonstrably not tight,
    # and no single narrow bar is allowed to call it a squeeze.
    max_bbw_percentile: float = 60.0
    pivot_proximity: float = 0.03
    min_avg_dollar_volume: float = 5_000_000.0
    volume_confirm_rvol: float = 1.5
    technical_horizon_days: int = 30


@dataclass(frozen=True, slots=True)
class Setup:
    """What the detector concluded about one ticker, on one day.

    ``stage`` is the closed enum the report branches on. Everything else is
    measurement -- the agent relays these numbers, it does not recompute them.
    """

    ticker: str
    as_of: date
    stage: str  # none | expansion | basing | coiled | triggered | failed
    score: int
    reason: str | None = None
    within_horizon: bool = False

    close: float | None = None
    pivot: float | None = None
    pivot_touches: int = 0
    distance_to_pivot_pct: float | None = None

    base_start: date | None = None
    base_length: int = 0
    base_depth_pct: float | None = None
    contraction_ratios: tuple[float, float, float] | None = None
    contraction_monotone: bool = False

    atr_pct: float | None = None
    atr_percentile: float | None = None
    bbw: float | None = None
    bbw_percentile: float | None = None
    donchian_percentile: float | None = None
    nr7: bool = False
    inside_day: bool = False
    prior_expansion: bool = False

    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    dist_sma20_pct: float | None = None
    dist_sma50_pct: float | None = None
    dist_sma200_pct: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    week52_position_pct: float | None = None

    rvol: float | None = None
    avg_dollar_volume_20: float | None = None
    volume_dryup: float | None = None
    volume_confirmed: bool | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["as_of"] = self.as_of.isoformat()
        payload["base_start"] = self.base_start.isoformat() if self.base_start else None
        if self.contraction_ratios is not None:
            payload["contraction_ratios"] = list(self.contraction_ratios)
        return payload


@dataclass(frozen=True, slots=True)
class TickerConfig:
    ticker: str
    enabled: bool = True
    analysis_types: tuple[str, ...] = ("technical", "competitor")
    technical_horizon_days: int = 30
    competitors: tuple[str, ...] = ()
    sector_keywords: tuple[str, ...] = ()
    company_name: str | None = None
    min_avg_dollar_volume: float | None = None
    notes: str = ""

    def wants(self, analysis: str) -> bool:
        return self.enabled and analysis in self.analysis_types


@dataclass(frozen=True, slots=True)
class NewsItem:
    ticker: str
    title: str
    url: str
    source: str
    published_at: str | None
    published_text: str | None = None
    peer_of: str | None = None  # set when the story is about a competitor
    url_hash: str = ""
    item_id: int | None = None


@dataclass(frozen=True, slots=True)
class Story:
    """One event, however many outlets carried it."""

    title: str
    url: str
    items: tuple[NewsItem, ...]

    @property
    def sources(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for item in self.items:
            seen.setdefault(item.source, None)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class CorporateEvent:
    ticker: str
    kind: str  # earnings | ex_dividend
    event_date: date
    days_away: int
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Fundamentals:
    ticker: str
    as_of: date
    pe: float | None = None
    forward_pe: float | None = None
    market_cap: float | None = None
    beta: float | None = None
    sector: str | None = None
    industry: str | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class Trade:
    ticker: str
    trade_date: date
    side: str  # buy | sell
    quantity: float
    price: float
    fee: float = 0.0
    note: str = ""
    trade_id: int | None = None


@dataclass(frozen=True, slots=True)
class Holding:
    """A netted position, priced if a recent bar was available."""

    ticker: str
    quantity: float
    avg_cost: float
    cost_basis: float
    realized_pnl: float
    last_close: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pct: float | None = None
    currency: str | None = None
    lots: tuple[Trade, ...] = field(default_factory=tuple)
