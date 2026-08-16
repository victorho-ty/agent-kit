"""The incremental bar cache: the reason a daily sync is cheap.

Only days after the newest stored bar are ever fetched. A first sync pulls two
years; every sync after it pulls one bar per ticker. That is what makes polling
frequently affordable, and it is why the detector can demand a full year of
history for its percentiles without that costing anything per run.

The last stored bar is always re-fetched rather than skipped. A bar written
while the market was still open is provisional -- its close is not the close --
and overwriting it on the next run is how the settled figure lands.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta

from . import db, settings
from .errors import FetchError
from .models import Bar
from .providers import FALLBACK, PRIMARY

# How much history a first sync pulls. The detector wants 252 sessions for its
# percentile windows, and a year of calendar days is not a year of sessions.
INITIAL_HISTORY_DAYS = 730


def sync_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    today: date,
    force_full: bool = False,
) -> dict:
    """Fetch and store what is missing for one ticker.

    Returns a per-ticker result rather than raising, unless *both* providers
    fail -- one dead ticker must not abort a scan of the other nine.
    """
    latest = None if force_full else db.latest_bar_day(conn, ticker)
    start = (latest - timedelta(days=1)) if latest else (today - timedelta(days=INITIAL_HISTORY_DAYS))

    fetched: list[Bar] = []
    source = PRIMARY.name
    primary_error: str | None = None

    try:
        fetched = PRIMARY.daily_bars(ticker, start=start)
    except FetchError as exc:
        primary_error = exc.message

    if not fetched:
        try:
            fetched = FALLBACK.daily_bars(ticker, start=start)
            source = FALLBACK.name
        except FetchError as exc:
            return {
                "ticker": ticker,
                "status": "failed",
                "stored": 0,
                "source": None,
                "error": primary_error or exc.message,
                "fallback_error": exc.message,
            }

    stored = db.store_bars(conn, ticker, fetched, source)
    newest = max((bar.day for bar in fetched), default=latest)
    return {
        "ticker": ticker,
        "status": "ok" if stored else "unchanged",
        "stored": stored,
        "source": source,
        "latest_bar": newest.isoformat() if newest else None,
        "degraded": source == FALLBACK.name,
        "error": primary_error if source == FALLBACK.name else None,
    }


def sync(
    conn: sqlite3.Connection,
    tickers: list[str],
    today: date,
    force_full: bool = False,
    delay: float | None = None,
) -> dict:
    """Sync a list of tickers, pacing the requests.

    ``status`` is ``ok`` when every ticker answered, ``partial`` when some did
    not, and ``error`` when none did. The agent branches on that and on nothing
    else.
    """
    pause = settings.request_delay() if delay is None else delay
    results = []
    for index, ticker in enumerate(tickers):
        if index and pause:
            time.sleep(pause)
        results.append(sync_ticker(conn, ticker, today, force_full=force_full))

    failures = [row for row in results if row["status"] == "failed"]
    if not results:
        status = "skipped"
    elif len(failures) == len(results):
        status = "error"
    elif failures:
        status = "partial"
    else:
        status = "ok"

    return {
        "status": status,
        "tickers": len(results),
        "stored": sum(row["stored"] for row in results),
        "degraded": [row["ticker"] for row in results if row.get("degraded")],
        "failures": failures,
        "results": results,
    }


def history(conn: sqlite3.Connection, ticker: str, limit: int | None = None) -> list[Bar]:
    """Cached bars, ascending. The only read path the rest of the package uses."""
    return db.load_bars(conn, ticker, limit=limit)


def window(bars: list[Bar], lookback_days: int) -> list[Bar]:
    """The trailing ``lookback_days`` *calendar* days of bars.

    Calendar days, not sessions, because that is what a person means by "the last
    90 days" -- and what the chart axis will show.
    """
    if not bars:
        return []
    cutoff = bars[-1].day - timedelta(days=lookback_days)
    return [bar for bar in bars if bar.day >= cutoff]
