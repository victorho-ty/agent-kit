"""SQLite storage for the round-trip price series.

Schema follows the README data model exactly:

- ``round_trip_prices`` -- one row per grid cell per run day, appended forever.
  A grid cell is (airline, dep_bucket) x ret_bucket for one (depart, return)
  date pair. ``min_price`` is the true round-trip total for all passengers.
- ``itineraries`` -- the full per-run detail the grid is aggregated from.
- ``runs`` -- one row per collect run per scope; the skill's freshness check
  and entire triage surface.

Writes use ``INSERT ... ON CONFLICT DO UPDATE`` against the unique constraint,
so re-running a day updates only the rows actually re-crawled and never deletes
the pairs it skipped.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_DB = Path.home() / ".local" / "share" / "hermes-ticket-prices" / "ticket_prices.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS round_trip_prices (
  id             INTEGER PRIMARY KEY,
  run_date       TEXT    NOT NULL,     -- YYYY-MM-DD, local
  scope          TEXT    NOT NULL,
  origin         TEXT    NOT NULL,
  dest           TEXT    NOT NULL,
  depart_date    TEXT    NOT NULL,
  return_date    TEXT    NOT NULL,
  airline        TEXT    NOT NULL,     -- outbound carrier
  return_airline TEXT,                 -- carried, not part of the key
  dep_bucket     TEXT    NOT NULL,     -- '00-03' .. '21-24'
  ret_bucket     TEXT    NOT NULL,
  out_stops      INTEGER NOT NULL,
  ret_stops      INTEGER NOT NULL,
  seat           TEXT    NOT NULL,
  currency       TEXT    NOT NULL,
  min_price      REAL    NOT NULL,     -- round-trip total, all passengers
  n_itineraries  INTEGER NOT NULL,
  created_at     TEXT    NOT NULL,
  UNIQUE (run_date, scope, depart_date, return_date,
          airline, dep_bucket, ret_bucket, out_stops, ret_stops, seat, currency)
);

CREATE TABLE IF NOT EXISTS itineraries (
  id             INTEGER PRIMARY KEY,
  run_date       TEXT    NOT NULL,
  scope          TEXT    NOT NULL,
  origin         TEXT    NOT NULL,
  dest           TEXT    NOT NULL,
  depart_date    TEXT    NOT NULL,
  return_date    TEXT    NOT NULL,
  out_airline    TEXT    NOT NULL,
  ret_airline    TEXT    NOT NULL,
  out_depart     TEXT    NOT NULL,     -- ISO8601 local, exact
  out_arrive     TEXT    NOT NULL,
  ret_depart     TEXT    NOT NULL,
  ret_arrive     TEXT    NOT NULL,
  out_stops      INTEGER NOT NULL,
  ret_stops      INTEGER NOT NULL,
  seat           TEXT    NOT NULL,
  currency       TEXT    NOT NULL,
  price          REAL    NOT NULL,     -- round-trip total for this combination
  created_at     TEXT    NOT NULL,
  UNIQUE (run_date, scope, depart_date, return_date,
          out_airline, ret_airline, out_depart, ret_depart, seat, currency)
);

CREATE TABLE IF NOT EXISTS runs (
  id             INTEGER PRIMARY KEY,
  scope          TEXT    NOT NULL,
  run_date       TEXT    NOT NULL,
  started_at     TEXT    NOT NULL,
  finished_at    TEXT,
  status         TEXT    NOT NULL,     -- ok | partial | blocked | error
  pairs_planned  INTEGER NOT NULL,
  pairs_succeeded INTEGER NOT NULL,
  pairs_failed   INTEGER NOT NULL,
  searches_used  INTEGER NOT NULL,
  rows_written   INTEGER NOT NULL,
  detail         TEXT                  -- free-form JSON for triage
);

CREATE INDEX IF NOT EXISTS idx_prices_series
  ON round_trip_prices (scope, airline, dep_bucket, ret_bucket, run_date);
CREATE INDEX IF NOT EXISTS idx_runs_scope_date
  ON runs (scope, run_date);
"""

# 3-hour departure buckets, local time. 21-24 is the red-eye bucket.
BUCKETS = ("00-03", "03-06", "06-09", "09-12", "12-15", "15-18", "18-21", "21-24")
_BUCKET_HOURS = tuple(int(b[:2]) for b in BUCKETS)


def bucket_from_hour(hour: int) -> str:
    """Map a departure hour (0-23, local) to its 3-hour bucket label."""
    for start, label in zip(_BUCKET_HOURS, BUCKETS):
        if hour >= start:
            best = label
    return best  # hour >= 21 lands on 21-24; hour 0 lands on 00-03


def db_path() -> Path:
    return Path(os.environ.get("TICKET_PRICES_DB", str(DEFAULT_DB))).expanduser()


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

_CELL_UPSERT = """
INSERT INTO round_trip_prices (
  run_date, scope, origin, dest, depart_date, return_date,
  airline, return_airline, dep_bucket, ret_bucket,
  out_stops, ret_stops, seat, currency, min_price, n_itineraries, created_at
) VALUES (
  :run_date, :scope, :origin, :dest, :depart_date, :return_date,
  :airline, :return_airline, :dep_bucket, :ret_bucket,
  :out_stops, :ret_stops, :seat, :currency, :min_price, :n_itineraries, :created_at
)
ON CONFLICT (run_date, scope, depart_date, return_date,
             airline, dep_bucket, ret_bucket, out_stops, ret_stops, seat, currency)
DO UPDATE SET
  return_airline = excluded.return_airline,
  min_price      = excluded.min_price,
  n_itineraries  = excluded.n_itineraries
"""

_ITINERARY_UPSERT = """
INSERT INTO itineraries (
  run_date, scope, origin, dest, depart_date, return_date,
  out_airline, ret_airline, out_depart, out_arrive, ret_depart, ret_arrive,
  out_stops, ret_stops, seat, currency, price, created_at
) VALUES (
  :run_date, :scope, :origin, :dest, :depart_date, :return_date,
  :out_airline, :ret_airline, :out_depart, :out_arrive, :ret_depart, :ret_arrive,
  :out_stops, :ret_stops, :seat, :currency, :price, :created_at
)
ON CONFLICT (run_date, scope, depart_date, return_date,
             out_airline, ret_airline, out_depart, ret_depart, seat, currency)
DO UPDATE SET
  out_arrive = excluded.out_arrive,
  ret_arrive = excluded.ret_arrive,
  ret_airline = excluded.ret_airline,
  out_stops  = excluded.out_stops,
  ret_stops  = excluded.ret_stops,
  price      = excluded.price
"""


def upsert_cell(conn: sqlite3.Connection, cell: dict) -> int:
    """Upsert one round-trip grid cell. Returns 1 if a row was inserted or changed."""
    row = {
        "run_date": cell["run_date"],
        "scope": cell["scope"],
        "origin": cell["origin"],
        "dest": cell["dest"],
        "depart_date": cell["depart_date"].isoformat(),
        "return_date": cell["return_date"].isoformat(),
        "airline": cell["airline"],
        "return_airline": cell.get("return_airline"),
        "dep_bucket": cell["dep_bucket"],
        "ret_bucket": cell["ret_bucket"],
        "out_stops": cell["out_stops"],
        "ret_stops": cell["ret_stops"],
        "seat": cell["seat"],
        "currency": cell["currency"],
        "min_price": cell["min_price"],
        "n_itineraries": cell.get("n_itineraries", 0),
        "created_at": _now(),
    }
    before = conn.total_changes
    conn.execute(_CELL_UPSERT, row)
    conn.commit()
    return 1 if conn.total_changes > before else 0


def upsert_itinerary(conn: sqlite3.Connection, row: dict) -> int:
    row = {
        "run_date": row["run_date"],
        "scope": row["scope"],
        "origin": row["origin"],
        "dest": row["dest"],
        "depart_date": row["depart_date"].isoformat(),
        "return_date": row["return_date"].isoformat(),
        "out_airline": row["out_airline"],
        "ret_airline": row["ret_airline"],
        "out_depart": row["out_depart"],
        "out_arrive": row["out_arrive"],
        "ret_depart": row["ret_depart"],
        "ret_arrive": row["ret_arrive"],
        "out_stops": row["out_stops"],
        "ret_stops": row["ret_stops"],
        "seat": row["seat"],
        "currency": row["currency"],
        "price": row["price"],
        "created_at": _now(),
    }
    cursor = conn.execute(_ITINERARY_UPSERT, row)
    conn.commit()
    return cursor.rowcount


def start_run(conn: sqlite3.Connection, scope: str, run_date: str, pairs_planned: int) -> int:
    cursor = conn.execute(
        "INSERT INTO runs (scope, run_date, started_at, status, pairs_planned,"
        " pairs_succeeded, pairs_failed, searches_used, rows_written) VALUES (?,?,?,?,?,0,0,0,0)",
        (scope, run_date, _now(), "running", pairs_planned),
    )
    conn.commit()
    return cursor.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, *, status: str, pairs_succeeded: int,
               pairs_failed: int, searches_used: int, rows_written: int, detail: dict | None = None) -> None:
    import json
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, pairs_succeeded=?, pairs_failed=?,"
        " searches_used=?, rows_written=?, detail=? WHERE id=?",
        (_now(), status, pairs_succeeded, pairs_failed, searches_used, rows_written,
         json.dumps(detail) if detail else None, run_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Reads (report / agent queries)
# ---------------------------------------------------------------------------

def latest_run(conn: sqlite3.Connection, scope: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE scope=? ORDER BY run_date DESC, id DESC LIMIT 1", (scope,)
    ).fetchone()


def run_dates(conn: sqlite3.Connection, scope: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT run_date FROM round_trip_prices WHERE scope=? ORDER BY run_date",
        (scope,),
    ).fetchall()
    return [r["run_date"] for r in rows]


def latest_cells(conn: sqlite3.Connection, scope: str, run_date: str) -> list[sqlite3.Row]:
    """Every grid cell recorded on a given run day, cheapest per cell."""
    return conn.execute(
        """SELECT scope, origin, dest, depart_date, return_date, airline, return_airline,
                  dep_bucket, ret_bucket, out_stops, ret_stops, seat, currency,
                  min_price, n_itineraries
           FROM round_trip_prices
           WHERE scope=? AND run_date=?
           ORDER BY airline, depart_date, dep_bucket, return_date, ret_bucket""",
        (scope, run_date),
    ).fetchall()


def cell_series(conn: sqlite3.Connection, scope: str, *, since: str | None = None) -> list[sqlite3.Row]:
    """The full daily time series, one row per (cell, run_date), oldest first."""
    sql = ("SELECT run_date, depart_date, return_date, airline, dep_bucket, ret_bucket,"
           " out_stops, ret_stops, seat, currency, min_price"
           " FROM round_trip_prices WHERE scope=?")
    params: list = [scope]
    if since:
        sql += " AND run_date >= ?"
        params.append(since)
    sql += " ORDER BY run_date, airline, depart_date, dep_bucket, return_date, ret_bucket"
    return conn.execute(sql, params).fetchall()


def distinct_airlines(conn: sqlite3.Connection, scope: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT airline FROM round_trip_prices WHERE scope=? ORDER BY airline", (scope,)
    ).fetchall()
    return [r["airline"] for r in rows]


def wo_w_movement(conn: sqlite3.Connection, scope: str, run_date: str, days: int = 7) -> list[dict]:
    """Week-over-week comparison per grid cell: latest price vs `days` ago."""
    from datetime import date as _date

    target = _date.fromisoformat(run_date) - timedelta(days=days)
    latest = _latest_cells_by_key(conn, scope, run_date)
    prior = _latest_cells_by_key(conn, scope, target.isoformat())
    out = []
    for key, row in latest.items():
        before = prior.get(key)
        price = row["min_price"]
        prev = before["min_price"] if before else None
        out.append({
            "airline": row["airline"],
            "depart_date": row["depart_date"],
            "return_date": row["return_date"],
            "dep_bucket": row["dep_bucket"],
            "ret_bucket": row["ret_bucket"],
            "price": price,
            "price_7d_ago": prev,
            "delta": round(price - prev, 2) if prev is not None else None,
            "delta_pct": round((price - prev) / prev * 100, 1) if prev else None,
        })
    out.sort(key=lambda r: (r["delta"] if r["delta"] is not None else float("inf")))
    return out


def _latest_cells_by_key(conn: sqlite3.Connection, scope: str, run_date: str) -> dict:
    """Cells of the most recent run <= run_date, keyed by the unique grid key."""
    rows = conn.execute(
        """SELECT depart_date, return_date, airline, dep_bucket, ret_bucket,
                  out_stops, ret_stops, seat, currency, min_price, run_date
           FROM round_trip_prices
           WHERE scope=? AND run_date <= ?
           ORDER BY run_date DESC""",
        (scope, run_date),
    ).fetchall()
    keyed: dict = {}
    for r in rows:
        key = (r["depart_date"], r["return_date"], r["airline"], r["dep_bucket"], r["ret_bucket"],
               r["out_stops"], r["ret_stops"], r["seat"], r["currency"])
        if key not in keyed:
            keyed[key] = r
    return keyed
