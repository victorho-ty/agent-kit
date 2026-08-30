"""SQLite: every transaction ever seen, what has been said, and how each run went.

Three tables, three jobs:

* **estate_state** -- one row per configured estate: its failure streak, whether
  it has been seeded, and how many records the last successful check parsed. The
  last of those is what turns "this estate returned nothing" into either "a
  quiet fortnight" or "the payload shape changed and nothing parses any more".
* **transaction_row** -- every transaction ever seen, matched or not.
  ``reported_at`` doubles as the delivery ledger, so there is no second table to
  keep in sync: a transaction is pending when it matched and has not been
  stamped. A missed day therefore needs no catch-up, and a summary that failed
  to send is still pending tomorrow.
* **runs** -- one row per check including the failures, which is the agent's
  whole triage surface. It never parses stdout to find out what happened.

**Unmatched transactions are stored too, and that is the point.** The trend and
the charts are estate-wide -- every residential unit in the block, not only the
two-bedroom flats somebody asked to be told about -- because a median over the
handful of transactions matching a narrow filter is noise, not a market level.
Storing only the matches would make the trend unrecoverable after the fact.

Nothing here ever deletes or rewrites a transaction. The newest hundred records
are all Centanet will serve and there is no way to page behind them, so a row
that scrolls out of that window survives in this file or nowhere.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from . import settings
from .errors import DatabaseError
from .models import Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS estate_state (
  estate                TEXT PRIMARY KEY,
  first_seen_at         TEXT NOT NULL,
  last_check_at         TEXT,
  last_ok_at            TEXT,
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  seeded                INTEGER NOT NULL DEFAULT 0,   -- 1 once a cold start has been absorbed
  recent_yield          INTEGER NOT NULL DEFAULT 0,   -- records parsed on the last good check
  published_count       INTEGER                       -- Centanet's own total for the search
);

CREATE TABLE IF NOT EXISTS transaction_row (
  id                   INTEGER PRIMARY KEY,
  estate               TEXT NOT NULL,                 -- the config entry's name
  tx_id                TEXT NOT NULL,                 -- Centanet's own id
  deal_type            TEXT NOT NULL CHECK (deal_type IN ('sale', 'rental')),
  price                REAL NOT NULL,                 -- 成交價, or the monthly rent
  ins_date             TEXT NOT NULL,                 -- 成交日期, ISO date
  reg_date             TEXT,                          -- 登記日期; sales only
  estate_name          TEXT,
  building             TEXT,
  floor                TEXT,
  unit                 TEXT,
  address_line         TEXT,
  bedrooms             INTEGER,                       -- 間隔; NULL when unpublished
  saleable_area        REAL,                          -- 面積(實); NULL when unpublished
  saleable_unit_price  REAL,                          -- 呎價(實) / 呎租(實)
  gross_area           REAL,                          -- 面積(建); recorded, never averaged
  gross_unit_price     REAL,
  data_source          TEXT,
  first_or_second_hand TEXT,
  detail_url           TEXT,
  matched              INTEGER NOT NULL DEFAULT 0,    -- meets the entry's criteria
  match_reason         TEXT,
  size_range           TEXT,                          -- the band it fell in, for grouping
  area_missing         INTEGER NOT NULL DEFAULT 0,    -- no 面積(實), so no 呎價(實)
  first_seen_at        TEXT NOT NULL,
  reported_at          TEXT,                          -- the ledger; NULL means pending
  run_id               INTEGER,
  UNIQUE (estate, tx_id)
);

CREATE INDEX IF NOT EXISTS tx_pending ON transaction_row (matched, reported_at);
CREATE INDEX IF NOT EXISTS tx_bucket ON transaction_row (estate, deal_type, ins_date);

CREATE TABLE IF NOT EXISTS runs (
  id             INTEGER PRIMARY KEY,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  status         TEXT NOT NULL,                       -- ok | partial | error
  estates_checked INTEGER NOT NULL DEFAULT 0,
  seen           INTEGER NOT NULL DEFAULT 0,
  added          INTEGER NOT NULL DEFAULT 0,
  matched        INTEGER NOT NULL DEFAULT 0,
  errors         INTEGER NOT NULL DEFAULT 0,
  detail         TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path is not None else settings.db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseError(
            f"could not open the transaction archive at {path}: {exc}", path=str(path),
        ) from exc
    return conn


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------------------- estate state


def estate_state(conn: sqlite3.Connection, estate: str) -> dict | None:
    row = conn.execute("SELECT * FROM estate_state WHERE estate = ?", (estate,)).fetchone()
    return dict(row) if row else None


def ensure_estate(conn: sqlite3.Connection, estate: str, now: datetime) -> dict:
    conn.execute(
        "INSERT INTO estate_state (estate, first_seen_at) VALUES (?, ?) "
        "ON CONFLICT (estate) DO NOTHING",
        (estate, now.isoformat()),
    )
    conn.commit()
    return estate_state(conn, estate)


def record_check_success(
    conn: sqlite3.Connection,
    estate: str,
    now: datetime,
    *,
    parsed: int,
    published_count: int | None = None,
    seeded: bool | None = None,
) -> None:
    """Stamp a successful check.

    ``recent_yield`` only moves when the check actually parsed something: a
    zero-yield check must leave the previous count in place, or the zero-yield
    tripwire would forget what the estate used to return and never fire again
    after the first broken one.
    """
    conn.execute(
        """UPDATE estate_state
              SET last_check_at = ?, last_ok_at = ?, consecutive_failures = 0,
                  last_error = NULL, published_count = COALESCE(?, published_count)
            WHERE estate = ?""",
        (now.isoformat(), now.isoformat(), published_count, estate),
    )
    if parsed > 0:
        conn.execute("UPDATE estate_state SET recent_yield = ? WHERE estate = ?", (parsed, estate))
    if seeded is not None:
        conn.execute(
            "UPDATE estate_state SET seeded = ? WHERE estate = ?", (1 if seeded else 0, estate)
        )
    conn.commit()


def record_check_failure(conn: sqlite3.Connection, estate: str, now: datetime, error: str) -> None:
    conn.execute(
        """UPDATE estate_state
              SET last_check_at = ?, last_error = ?,
                  consecutive_failures = consecutive_failures + 1
            WHERE estate = ?""",
        (now.isoformat(), error, estate),
    )
    conn.commit()


def all_estate_state(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM estate_state").fetchall()
    return {row["estate"]: dict(row) for row in rows}


# --------------------------------------------------------------------------- transactions


def known_ids(conn: sqlite3.Connection, estate: str) -> set[str]:
    rows = conn.execute("SELECT tx_id FROM transaction_row WHERE estate = ?", (estate,))
    return {row["tx_id"] for row in rows}


def insert_transaction(
    conn: sqlite3.Connection,
    transaction: Transaction,
    result,
    now: datetime,
    *,
    run_id: int | None = None,
    reported: bool = False,
) -> int | None:
    """Store a newly seen transaction with the matcher's verdict.

    ``reported`` pre-stamps ``reported_at``. That is cold-start seeding, and the
    only way a transaction is ever born already delivered. Returns ``None`` if
    the row was already there, which is the normal case on every check after the
    first: the newest hundred barely changes from one day to the next.
    """
    cursor = conn.execute(
        """INSERT OR IGNORE INTO transaction_row (
               estate, tx_id, deal_type, price, ins_date, reg_date, estate_name, building,
               floor, unit, address_line, bedrooms, saleable_area, saleable_unit_price,
               gross_area, gross_unit_price, data_source, first_or_second_hand, detail_url,
               matched, match_reason, size_range, area_missing, first_seen_at, reported_at, run_id
           ) VALUES (
               :estate, :tx_id, :deal_type, :price, :ins_date, :reg_date, :estate_name, :building,
               :floor, :unit, :address_line, :bedrooms, :saleable_area, :saleable_unit_price,
               :gross_area, :gross_unit_price, :data_source, :first_or_second_hand, :detail_url,
               :matched, :match_reason, :size_range, :area_missing, :first_seen_at, :reported_at,
               :run_id
           )""",
        {
            "estate": transaction.estate,
            "tx_id": transaction.tx_id,
            "deal_type": transaction.deal_type,
            "price": transaction.price,
            "ins_date": _iso(transaction.ins_date),
            "reg_date": _iso(transaction.reg_date),
            "estate_name": transaction.estate_name,
            "building": transaction.building,
            "floor": transaction.floor,
            "unit": transaction.unit,
            "address_line": transaction.address_line,
            "bedrooms": transaction.bedrooms,
            "saleable_area": transaction.saleable_area,
            "saleable_unit_price": transaction.saleable_unit_price,
            "gross_area": transaction.gross_area,
            "gross_unit_price": transaction.gross_unit_price,
            "data_source": transaction.data_source,
            "first_or_second_hand": transaction.first_or_second_hand,
            "detail_url": transaction.detail_url,
            "matched": 1 if result.matched else 0,
            "match_reason": result.reason,
            "size_range": result.size_range.label if result.size_range else None,
            "area_missing": 1 if result.area_missing else 0,
            "first_seen_at": now.isoformat(),
            "reported_at": now.isoformat() if reported else None,
            "run_id": run_id,
        },
    )
    conn.commit()
    return cursor.lastrowid if cursor.rowcount else None


def pending(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """Matched transactions not yet reported, newest deal first.

    Newest first rather than oldest: unlike an events queue, a backlog here is a
    price history, and a summary that opens with a deal from five weeks ago
    reads as stale even when the rest of it is today's.
    """
    sql = (
        "SELECT * FROM transaction_row WHERE matched = 1 AND reported_at IS NULL "
        "ORDER BY ins_date DESC, id DESC"
    )
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM transaction_row WHERE matched = 1 AND reported_at IS NULL"
    ).fetchone()[0]


def mark_reported(conn: sqlite3.Connection, ids: list[int], now: datetime) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cursor = conn.execute(
        f"UPDATE transaction_row SET reported_at = ? "
        f"WHERE id IN ({placeholders}) AND reported_at IS NULL",
        (now.isoformat(), *ids),
    )
    conn.commit()
    return cursor.rowcount


def query(
    conn: sqlite3.Connection,
    *,
    estate: str | None = None,
    deal_type: str | None = None,
    since: date | str | None = None,
    until: date | str | None = None,
    bedrooms: int | None = None,
    matched: bool | None = None,
    with_unit_price: bool = False,
    limit: int | None = None,
    newest_first: bool = True,
) -> list[dict]:
    """The archive, filtered. Read-only, like everything an operator can ask for."""
    clauses: list[str] = []
    params: list = []
    if estate:
        clauses.append("estate = ?")
        params.append(estate)
    if deal_type:
        clauses.append("deal_type = ?")
        params.append(deal_type)
    if since is not None:
        clauses.append("ins_date >= ?")
        params.append(since if isinstance(since, str) else since.isoformat())
    if until is not None:
        clauses.append("ins_date <= ?")
        params.append(until if isinstance(until, str) else until.isoformat())
    if bedrooms is not None:
        clauses.append("bedrooms = ?")
        params.append(bedrooms)
    if matched is not None:
        clauses.append("matched = ?")
        params.append(1 if matched else 0)
    if with_unit_price:
        clauses.append("saleable_unit_price IS NOT NULL")

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "DESC" if newest_first else "ASC"
    sql = f"SELECT * FROM transaction_row{where} ORDER BY ins_date {order}, id {order}"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def buckets(conn: sqlite3.Connection) -> list[dict]:
    """Every (estate, deal_type) pair the archive actually holds, with its span."""
    rows = conn.execute(
        """SELECT estate, deal_type, COUNT(*) AS total,
                  SUM(saleable_unit_price IS NOT NULL) AS priced,
                  MIN(ins_date) AS earliest, MAX(ins_date) AS latest
             FROM transaction_row
            GROUP BY estate, deal_type
            ORDER BY estate, deal_type"""
    ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- runs


def start_run(conn: sqlite3.Connection, now: datetime) -> int:
    cursor = conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now.isoformat(),)
    )
    conn.commit()
    return cursor.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    now: datetime,
    status: str,
    counts: dict,
    detail: str | None = None,
) -> None:
    conn.execute(
        """UPDATE runs SET finished_at = ?, status = ?, estates_checked = ?, seen = ?,
                           added = ?, matched = ?, errors = ?, detail = ?
            WHERE id = ?""",
        (
            now.isoformat(), status,
            counts.get("estates_checked", 0), counts.get("seen", 0), counts.get("added", 0),
            counts.get("matched", 0), counts.get("errors", 0), detail, run_id,
        ),
    )
    conn.commit()


def recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


def consecutive_failures(conn: sqlite3.Connection) -> int:
    """Runs since the last one that reached every estate."""
    streak = 0
    for row in conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 50"):
        if row["status"] == "ok":
            return streak
        streak += 1
    return streak
