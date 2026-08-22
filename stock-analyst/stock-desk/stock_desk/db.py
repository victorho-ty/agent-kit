"""SQLite: the bar cache, the dedupe memory, and the trade log.

Three of these tables exist purely to stop the agent being woken for nothing:

* ``bars`` makes a sync incremental. Only days after the newest stored bar are
  ever fetched, so a daily run pulls one bar per ticker rather than a year of
  them.
* ``news.notified_at`` and ``events.notified_at`` are what make an alert fire on
  an event rather than on a schedule. The poller writes rows; a row with a null
  ``notified_at`` is the only reason anybody is disturbed. Poll as often as you
  like -- nothing downstream cares.
* ``setup_state`` remembers yesterday's stage, which is the only way to detect a
  *failed* breakout: triggered yesterday, back under the pivot today. Without it
  a failure is indistinguishable from a setup that never fired.

Writes go through :func:`connect` and nothing else. The agent never opens this
file.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

from . import settings
from .errors import DatabaseError
from .models import Bar, Fundamentals, Trade

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS bars (
    ticker      TEXT NOT NULL,
    day         TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    adj_close   REAL,
    volume      REAL NOT NULL,
    source      TEXT NOT NULL,
    PRIMARY KEY (ticker, day)
);
CREATE INDEX IF NOT EXISTS bars_ticker_day ON bars (ticker, day DESC);

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker      TEXT PRIMARY KEY,
    as_of       TEXT NOT NULL,
    pe          REAL,
    forward_pe  REAL,
    market_cap  REAL,
    beta        REAL,
    sector      TEXT,
    industry    TEXT,
    currency    TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    event_date  TEXT NOT NULL,
    detail      TEXT,
    detected_at TEXT NOT NULL,
    notified_at TEXT,
    UNIQUE (ticker, kind, event_date)
);
CREATE INDEX IF NOT EXISTS events_pending ON events (notified_at, event_date);

CREATE TABLE IF NOT EXISTS news (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker         TEXT NOT NULL,
    peer_of        TEXT,
    url_hash       TEXT NOT NULL,
    url            TEXT NOT NULL,
    title          TEXT NOT NULL,
    source         TEXT NOT NULL,
    published_at   TEXT,
    published_text TEXT,
    first_seen_at  TEXT NOT NULL,
    notified_at    TEXT,
    -- Which MCP server carried it. Kept because the two feeds disagree about
    -- the same story often enough that "who said so" is worth being able to ask.
    feed           TEXT NOT NULL DEFAULT '',
    summary        TEXT,
    -- Alpha Vantage model output, null on Yahoo rows. Null means unscored; it
    -- is never defaulted to zero, which would read as genuinely neutral.
    sentiment_score REAL,
    sentiment_label TEXT,
    relevance       REAL,
    -- The desk's own verdict, stamped at intake by news.store(). Suppressed
    -- rows keep theirs: the reason a story was dropped is worth being able to
    -- audit later, and the row is what carries it.
    event_class    TEXT,
    materiality    INTEGER,
    band           TEXT,
    suppressed     INTEGER NOT NULL DEFAULT 0,
    -- Scoped to the ticker, not global. One AMD story is genuinely news for
    -- every watchlist entry naming AMD as a competitor, and a global UNIQUE
    -- silently gave it to whichever entry happened to be polled first -- so
    -- NVDA lost its entire AMD peer feed to CBRS. Dedupe is per-ticker for the
    -- same reason clustering is: a story touching two positions belongs to both.
    UNIQUE (ticker, url_hash)
);
CREATE INDEX IF NOT EXISTS news_pending ON news (notified_at, suppressed, materiality DESC);
CREATE INDEX IF NOT EXISTS news_ticker ON news (ticker, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS macro (
    series      TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    value       REAL NOT NULL,
    fetched_at  TEXT NOT NULL,
    -- Null until a move has been reported. The same event-driven gate the news
    -- table uses: the refresher writes every reading it sees, and only a
    -- reading that moved materially past the last reported one is ever said
    -- out loud. Without this the desk announces the same 10-year yield every
    -- morning until nobody reads the macro section.
    notified_at TEXT,
    PRIMARY KEY (series, as_of)
);
CREATE INDEX IF NOT EXISTS macro_recent ON macro (series, as_of DESC);

CREATE TABLE IF NOT EXISTS positions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    side       TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity   REAL NOT NULL CHECK (quantity > 0),
    price      REAL NOT NULL CHECK (price >= 0),
    fee        REAL NOT NULL DEFAULT 0,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS positions_ticker ON positions (ticker, trade_date, id);

CREATE TABLE IF NOT EXISTS setup_state (
    ticker  TEXT PRIMARY KEY,
    as_of   TEXT NOT NULL,
    stage   TEXT NOT NULL,
    score   INTEGER NOT NULL,
    pivot   REAL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS runs_recent ON runs (kind, started_at DESC);
"""

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Casefold, strip accents, collapse whitespace. Used for title comparison."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WHITESPACE.sub(" ", stripped.casefold()).strip()


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open the database, creating and migrating it if needed."""
    resolved = Path(path) if path else settings.db_path()
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(resolved, timeout=30.0)
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseError(f"could not open the database at {resolved}", path=str(resolved)) from exc

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _migrate(conn)
        yield conn
        conn.commit()
    except DatabaseError:
        conn.rollback()
        raise
    except sqlite3.Error as exc:
        conn.rollback()
        raise DatabaseError(f"database error: {exc}", path=str(resolved)) from exc
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] > SCHEMA_VERSION:
        raise DatabaseError(
            f"database is at schema v{row['version']}, this build understands v{SCHEMA_VERSION}",
            found=row["version"],
            expected=SCHEMA_VERSION,
        )


# --------------------------------------------------------------------------- bars


def latest_bar_day(conn: sqlite3.Connection, ticker: str) -> date | None:
    row = conn.execute("SELECT MAX(day) AS day FROM bars WHERE ticker = ?", (ticker,)).fetchone()
    return date.fromisoformat(row["day"]) if row and row["day"] else None


def store_bars(conn: sqlite3.Connection, ticker: str, bars: Iterable[Bar], source: str) -> int:
    """Upsert. A same-day re-fetch overwrites, because an intraday bar stored
    before the close is provisional and the settled one must replace it."""
    rows = [
        (
            ticker,
            bar.day.isoformat(),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.adj_close,
            bar.volume,
            source,
        )
        for bar in bars
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO bars (ticker, day, open, high, low, close, adj_close, volume, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (ticker, day) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, adj_close=excluded.adj_close,
            volume=excluded.volume, source=excluded.source
        """,
        rows,
    )
    return len(rows)


def load_bars(conn: sqlite3.Connection, ticker: str, limit: int | None = None) -> list[Bar]:
    """Bars in ascending date order -- the order every indicator assumes."""
    sql = "SELECT * FROM bars WHERE ticker = ? ORDER BY day DESC"
    params: tuple = (ticker,)
    if limit:
        sql += " LIMIT ?"
        params = (ticker, limit)
    rows = conn.execute(sql, params).fetchall()
    bars = [
        Bar(
            day=date.fromisoformat(row["day"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            adj_close=row["adj_close"],
        )
        for row in rows
    ]
    bars.reverse()
    return bars


# ------------------------------------------------------------------- fundamentals


def store_fundamentals(conn: sqlite3.Connection, snapshot: Fundamentals) -> None:
    conn.execute(
        """
        INSERT INTO fundamentals (ticker, as_of, pe, forward_pe, market_cap, beta,
                                  sector, industry, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (ticker) DO UPDATE SET
            as_of=excluded.as_of, pe=excluded.pe, forward_pe=excluded.forward_pe,
            market_cap=excluded.market_cap, beta=excluded.beta,
            sector=excluded.sector, industry=excluded.industry,
            currency=excluded.currency
        """,
        (
            snapshot.ticker,
            snapshot.as_of.isoformat(),
            snapshot.pe,
            snapshot.forward_pe,
            snapshot.market_cap,
            snapshot.beta,
            snapshot.sector,
            snapshot.industry,
            snapshot.currency,
        ),
    )


def load_fundamentals(conn: sqlite3.Connection, ticker: str) -> Fundamentals | None:
    row = conn.execute("SELECT * FROM fundamentals WHERE ticker = ?", (ticker,)).fetchone()
    if row is None:
        return None
    return Fundamentals(
        ticker=row["ticker"],
        as_of=date.fromisoformat(row["as_of"]),
        pe=row["pe"],
        forward_pe=row["forward_pe"],
        market_cap=row["market_cap"],
        beta=row["beta"],
        sector=row["sector"],
        industry=row["industry"],
        currency=row["currency"],
    )


# ------------------------------------------------------------------- setup state


def load_setup_state(conn: sqlite3.Connection, ticker: str) -> dict | None:
    row = conn.execute("SELECT * FROM setup_state WHERE ticker = ?", (ticker,)).fetchone()
    return dict(row) if row else None


def store_setup_state(
    conn: sqlite3.Connection, ticker: str, as_of: date, stage: str, score: int, pivot: float | None
) -> None:
    conn.execute(
        """
        INSERT INTO setup_state (ticker, as_of, stage, score, pivot)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (ticker) DO UPDATE SET
            as_of=excluded.as_of, stage=excluded.stage,
            score=excluded.score, pivot=excluded.pivot
        """,
        (ticker, as_of.isoformat(), stage, int(score), pivot),
    )


# ---------------------------------------------------------------------- positions


def add_trade(conn: sqlite3.Connection, trade: Trade, created_at: datetime) -> int:
    cursor = conn.execute(
        """
        INSERT INTO positions (ticker, trade_date, side, quantity, price, fee, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade.ticker,
            trade.trade_date.isoformat(),
            trade.side,
            trade.quantity,
            trade.price,
            trade.fee,
            trade.note,
            created_at.isoformat(),
        ),
    )
    return int(cursor.lastrowid)


def load_trades(conn: sqlite3.Connection, ticker: str | None = None) -> list[Trade]:
    """Chronological. ``id`` breaks ties so two trades on one day keep their
    entry order -- which FIFO realisation depends on."""
    if ticker:
        rows = conn.execute(
            "SELECT * FROM positions WHERE ticker = ? ORDER BY trade_date, id", (ticker,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM positions ORDER BY ticker, trade_date, id").fetchall()
    return [
        Trade(
            ticker=row["ticker"],
            trade_date=date.fromisoformat(row["trade_date"]),
            side=row["side"],
            quantity=row["quantity"],
            price=row["price"],
            fee=row["fee"],
            note=row["note"],
            trade_id=row["id"],
        )
        for row in rows
    ]


def delete_trade(conn: sqlite3.Connection, trade_id: int) -> bool:
    cursor = conn.execute("DELETE FROM positions WHERE id = ?", (trade_id,))
    return cursor.rowcount > 0


# --------------------------------------------------------------------------- runs


def start_run(conn: sqlite3.Connection, kind: str, started_at: datetime) -> int:
    cursor = conn.execute(
        "INSERT INTO runs (kind, started_at, status) VALUES (?, ?, 'running')",
        (kind, started_at.isoformat()),
    )
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection, run_id: int, finished_at: datetime, status: str, detail: str | None
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, detail = ? WHERE id = ?",
        (finished_at.isoformat(), status, detail, run_id),
    )


def last_success(conn: sqlite3.Connection, kind: str) -> datetime | None:
    """When a run of this kind last completed without erroring.

    This is what "since the last run" means to the feeds: Alpha Vantage takes a
    ``time_from`` and honours it, so the window is the real gap between polls
    rather than a fixed lookback that either re-reads yesterday or skips a day
    after an outage. A failed run is deliberately not a boundary -- if the last
    attempt errored, the one before it is still the last time anything was
    actually read.
    """
    row = conn.execute(
        """SELECT started_at FROM runs
           WHERE kind = ? AND status IN ('ok', 'partial') AND finished_at IS NOT NULL
           ORDER BY started_at DESC LIMIT 1""",
        (kind,),
    ).fetchone()
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row["started_at"])
    except (TypeError, ValueError):
        return None


def recent_runs(conn: sqlite3.Connection, limit: int = 10, kind: str | None = None) -> list[dict]:
    if kind:
        rows = conn.execute(
            "SELECT * FROM runs WHERE kind = ? ORDER BY started_at DESC LIMIT ?", (kind, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]
