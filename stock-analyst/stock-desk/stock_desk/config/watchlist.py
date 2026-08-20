"""Loading, validating and editing ``watchlist.json``.

*What* is watched is configuration, never code. Adding a ticker, changing a
horizon or correcting a peer set is an edit to this file -- which is also why
the CRUD helpers here rewrite it in place, preserving key order, rather than
making the agent hand-edit JSON and risk truncating somebody's peer list.

**Competitors are declared, not derived.** The peer set for a ticker is written
down once, when the ticker is added, and reused on every run afterwards. Deriving
it from a sector classification each morning would cost a model call per ticker
per day to answer a question whose answer changes about once a year.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from ..errors import ConfigError
from ..models import TickerConfig

CONFIG_FILE = Path(__file__).with_name("watchlist.json")

VALID_ANALYSES = frozenset({"technical", "competitor"})


@dataclass(frozen=True, slots=True)
class ReportConfig:
    frequency: str = "daily"
    minutes_before_open: int = 30
    cluster_threshold: float = 0.6
    news_lookback_days: int = 3
    event_horizon_days: int = 10
    # A ceiling on stories per message. Anything held back is stated in the
    # payload and stays pending, so a busy day defers rather than disappears.
    max_stories: int = 12


@dataclass(frozen=True, slots=True)
class Defaults:
    analysis_types: tuple[str, ...] = ("technical", "competitor")
    technical_horizon_days: int = 30
    min_avg_dollar_volume: float = 5_000_000.0


@dataclass(frozen=True, slots=True)
class WatchlistConfig:
    timezone: str
    report: ReportConfig
    defaults: Defaults
    tickers: tuple[TickerConfig, ...]
    path: Path

    def enabled(self) -> tuple[TickerConfig, ...]:
        return tuple(t for t in self.tickers if t.enabled)

    def find(self, ticker: str) -> TickerConfig | None:
        wanted = ticker.strip().upper()
        for entry in self.tickers:
            if entry.ticker.upper() == wanted:
                return entry
        return None

    def require(self, ticker: str) -> TickerConfig:
        found = self.find(ticker)
        if found is None:
            raise ConfigError(
                f"{ticker} is not on the watchlist",
                ticker=ticker,
                known=[t.ticker for t in self.tickers],
            )
        return found

    def peers_of(self, ticker: str) -> tuple[str, ...]:
        entry = self.find(ticker)
        return entry.competitors if entry else ()


def _read(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"watchlist config not found at {path}", path=str(path)) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"watchlist config is not valid JSON: {exc.msg} at line {exc.lineno}",
            path=str(path),
            line=exc.lineno,
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError("watchlist config must be a JSON object", path=str(path))
    return raw


def _ticker_from_raw(raw: dict, defaults: Defaults, index: int, path: Path) -> TickerConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"tickers[{index}] must be an object", path=str(path))
    symbol = str(raw.get("ticker", "")).strip().upper()
    if not symbol:
        raise ConfigError(f"tickers[{index}] has no ticker", path=str(path), index=index)

    analyses = tuple(raw.get("analysis_types", defaults.analysis_types))
    unknown = set(analyses) - VALID_ANALYSES
    if unknown:
        raise ConfigError(
            f"{symbol} requests unknown analysis types: {sorted(unknown)}",
            ticker=symbol,
            valid=sorted(VALID_ANALYSES),
        )

    horizon = int(raw.get("technical_horizon_days", defaults.technical_horizon_days))
    if horizon < 1:
        raise ConfigError(
            f"{symbol} has technical_horizon_days={horizon}; must be at least 1",
            ticker=symbol,
        )

    floor = raw.get("min_avg_dollar_volume")
    return TickerConfig(
        ticker=symbol,
        enabled=bool(raw.get("enabled", True)),
        analysis_types=analyses,
        technical_horizon_days=horizon,
        competitors=tuple(str(c).strip().upper() for c in raw.get("competitors", []) if str(c).strip()),
        sector_keywords=tuple(raw.get("sector_keywords", [])),
        company_name=raw.get("company_name") or None,
        min_avg_dollar_volume=float(floor) if floor is not None else None,
        notes=str(raw.get("notes", "")),
    )


def load(path: Path | None = None) -> WatchlistConfig:
    """Read and validate the config. Raises :class:`ConfigError`, never returns a partial."""
    from .. import settings

    resolved = Path(path) if path else settings.config_path()
    raw = _read(resolved)

    defaults_raw = raw.get("defaults", {}) or {}
    defaults = Defaults(
        analysis_types=tuple(defaults_raw.get("analysis_types", Defaults.analysis_types)),
        technical_horizon_days=int(
            defaults_raw.get("technical_horizon_days", Defaults.technical_horizon_days)
        ),
        min_avg_dollar_volume=float(
            defaults_raw.get("min_avg_dollar_volume", Defaults.min_avg_dollar_volume)
        ),
    )

    report_raw = raw.get("report", {}) or {}
    report = ReportConfig(
        frequency=str(report_raw.get("frequency", ReportConfig.frequency)),
        minutes_before_open=int(
            report_raw.get("minutes_before_open", ReportConfig.minutes_before_open)
        ),
        cluster_threshold=float(
            report_raw.get("cluster_threshold", ReportConfig.cluster_threshold)
        ),
        news_lookback_days=int(
            report_raw.get("news_lookback_days", ReportConfig.news_lookback_days)
        ),
        event_horizon_days=int(
            report_raw.get("event_horizon_days", ReportConfig.event_horizon_days)
        ),
        max_stories=int(report_raw.get("max_stories", ReportConfig.max_stories)),
    )

    entries = raw.get("tickers", [])
    if not isinstance(entries, list):
        raise ConfigError("`tickers` must be a list", path=str(resolved))

    tickers = tuple(
        _ticker_from_raw(entry, defaults, index, resolved) for index, entry in enumerate(entries)
    )

    seen: set[str] = set()
    for entry in tickers:
        if entry.ticker in seen:
            raise ConfigError(f"{entry.ticker} appears twice on the watchlist", ticker=entry.ticker)
        seen.add(entry.ticker)

    return WatchlistConfig(
        timezone=str(raw.get("timezone", "Asia/Hong_Kong")),
        report=report,
        defaults=defaults,
        tickers=tickers,
        path=resolved,
    )


def _to_raw(entry: TickerConfig) -> dict:
    payload: dict = {
        "ticker": entry.ticker,
        "company_name": entry.company_name,
        "enabled": entry.enabled,
        "analysis_types": list(entry.analysis_types),
        "technical_horizon_days": entry.technical_horizon_days,
        "competitors": list(entry.competitors),
        "sector_keywords": list(entry.sector_keywords),
        "notes": entry.notes,
    }
    if entry.min_avg_dollar_volume is not None:
        payload["min_avg_dollar_volume"] = entry.min_avg_dollar_volume
    return payload


def save(config: WatchlistConfig) -> Path:
    """Write the config back, atomically. The temp file is in the same directory
    so the replace is a rename on one filesystem, not a copy that can half-finish."""
    body = {
        "timezone": config.timezone,
        "report": {
            "frequency": config.report.frequency,
            "minutes_before_open": config.report.minutes_before_open,
            "cluster_threshold": config.report.cluster_threshold,
            "news_lookback_days": config.report.news_lookback_days,
            "event_horizon_days": config.report.event_horizon_days,
            "max_stories": config.report.max_stories,
        },
        "defaults": {
            "analysis_types": list(config.defaults.analysis_types),
            "technical_horizon_days": config.defaults.technical_horizon_days,
            "min_avg_dollar_volume": config.defaults.min_avg_dollar_volume,
        },
        "tickers": [_to_raw(entry) for entry in config.tickers],
    }
    temp = config.path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(config.path)
    return config.path


def add_ticker(config: WatchlistConfig, entry: TickerConfig) -> WatchlistConfig:
    if config.find(entry.ticker) is not None:
        raise ConfigError(f"{entry.ticker} is already on the watchlist", ticker=entry.ticker)
    updated = replace(config, tickers=config.tickers + (entry,))
    save(updated)
    return updated


def remove_ticker(config: WatchlistConfig, ticker: str) -> WatchlistConfig:
    existing = config.require(ticker)
    kept = tuple(t for t in config.tickers if t.ticker != existing.ticker)
    updated = replace(config, tickers=kept)
    save(updated)
    return updated


def update_ticker(config: WatchlistConfig, ticker: str, **changes) -> WatchlistConfig:
    """Patch one entry. Unknown fields are an error, not a silent no-op --
    a typo in a field name must not look like a successful edit."""
    existing = config.require(ticker)
    allowed = {f for f in TickerConfig.__dataclass_fields__ if f != "ticker"}
    unknown = set(changes) - allowed
    if unknown:
        raise ConfigError(
            f"unknown field(s) for a watchlist entry: {sorted(unknown)}",
            ticker=existing.ticker,
            valid=sorted(allowed),
        )
    if "analysis_types" in changes:
        bad = set(changes["analysis_types"]) - VALID_ANALYSES
        if bad:
            raise ConfigError(
                f"unknown analysis types: {sorted(bad)}", valid=sorted(VALID_ANALYSES)
            )
        changes["analysis_types"] = tuple(changes["analysis_types"])
    if "competitors" in changes:
        changes["competitors"] = tuple(str(c).strip().upper() for c in changes["competitors"])
    if "sector_keywords" in changes:
        changes["sector_keywords"] = tuple(changes["sector_keywords"])

    patched = replace(existing, **changes)
    tickers = tuple(patched if t.ticker == existing.ticker else t for t in config.tickers)
    updated = replace(config, tickers=tickers)
    save(updated)
    return updated
