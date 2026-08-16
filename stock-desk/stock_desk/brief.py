"""The on-demand per-ticker brief: two charts and the numbers beside them.

One call renders both images and returns every metric in one payload, so
answering "show me NVDA over 90 days" is a single command rather than four.

The metrics divide into three groups, and the payload keeps them separate
because they carry different weight:

* ``key_metrics`` -- the ones asked for by name: 52-week high and low, average
  volume over the requested window, trailing and forward P/E.
* ``technical`` -- the compression and position readings, the same numbers the
  daily scan uses, so the brief and the report can never disagree.
* ``position`` -- present only when the ticker is actually held.

Vendor ratios are labelled as such. P/E arrives already normalised by the data
vendor; it is neither the company's as-reported figure nor comparable across
vendors, and the SOUL requires that be said rather than assumed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import bars as bars_module
from . import charts, db, indicators, news, portfolio, setups
from .config.watchlist import WatchlistConfig
from .errors import InsufficientDataError, NotFoundError
from .models import Thresholds

DEFAULT_LOOKBACK_DAYS = 90


def build(
    conn: sqlite3.Connection,
    ticker: str,
    now: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    config: WatchlistConfig | None = None,
    with_charts: bool = True,
    news_days: int = 7,
) -> dict:
    """Everything about one ticker, on demand."""
    ticker = ticker.strip().upper()
    history = bars_module.history(conn, ticker)
    if not history:
        raise NotFoundError(
            f"no cached bars for {ticker}; run a sync first",
            ticker=ticker,
        )

    window = bars_module.window(history, lookback_days)
    if not window:
        raise InsufficientDataError(
            f"no bars for {ticker} inside a {lookback_days} day window",
            ticker=ticker,
            lookback_days=lookback_days,
        )

    thresholds = Thresholds()
    if config is not None:
        from .report import thresholds_for

        thresholds = thresholds_for(config, ticker)

    previous = db.load_setup_state(conn, ticker)
    setup = setups.detect(
        ticker,
        history,
        thresholds,
        previous_stage=previous["stage"] if previous else None,
        previous_pivot=previous["pivot"] if previous else None,
    )

    fundamentals = db.load_fundamentals(conn, ticker)
    sessions = len(window)
    average_volume = indicators.average_volume(history, sessions)

    payload: dict = {
        "ok": True,
        "ticker": ticker,
        "generated_at": now.isoformat(),
        "as_of": history[-1].day.isoformat(),
        "lookback_days": lookback_days,
        "sessions_in_window": sessions,
        "key_metrics": {
            "last_close": history[-1].close,
            "week52_high": setup.week52_high,
            "week52_low": setup.week52_low,
            "week52_position_pct": setup.week52_position_pct,
            "average_volume": average_volume,
            "average_volume_window_sessions": sessions,
            "average_dollar_volume_20d": setup.avg_dollar_volume_20,
            "pe": fundamentals.pe if fundamentals else None,
            "forward_pe": fundamentals.forward_pe if fundamentals else None,
            "market_cap": fundamentals.market_cap if fundamentals else None,
            "beta": fundamentals.beta if fundamentals else None,
            "currency": fundamentals.currency if fundamentals else None,
            "sector": fundamentals.sector if fundamentals else None,
            "ratios_as_of": fundamentals.as_of.isoformat() if fundamentals else None,
            "ratios_note": (
                "P/E and forward P/E are the data vendor's own normalisation, "
                "not the company's as-reported figure"
            ),
        },
        "technical": setup.to_dict(),
        "status_line": setups.status_line(setup),
        "charts": [],
        "news": [],
        "position": None,
    }

    if with_charts:
        charts.sweep()
        payload["charts"] = [
            charts.candles(ticker, window, lookback_days),
            # Averages come from the full history, then get windowed for display,
            # so SMA50 is drawn from the first visible day rather than starting
            # fifty days into a ninety-day chart.
            charts.lines(ticker, history, lookback_days),
        ]

    since = (now - timedelta(days=news_days)).isoformat()
    threshold = config.report.cluster_threshold if config else 0.6
    payload["news"] = [
        {
            "title": story.title,
            "url": story.url,
            "sources": list(story.sources),
            "published_text": story.items[0].published_text,
            "about_competitor": next(
                (item.peer_of for item in story.items if item.peer_of), None
            ),
        }
        for story in news.recent(conn, ticker, since, threshold)
    ]

    trades = db.load_trades(conn, ticker)
    if trades:
        held = portfolio.net(
            ticker,
            trades,
            last_close=history[-1].close,
            currency=fundamentals.currency if fundamentals else None,
        )
        payload["position"] = {
            "quantity": held.quantity,
            "avg_cost": held.avg_cost,
            "cost_basis": held.cost_basis,
            "market_value": held.market_value,
            "unrealized_pnl": held.unrealized_pnl,
            "unrealized_pct": held.unrealized_pct,
            "realized_pnl": held.realized_pnl,
            "currency": held.currency,
            "line": portfolio.summarise(held),
        }

    return payload
