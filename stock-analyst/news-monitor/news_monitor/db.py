"""SQLite: which feeds exist, what they published, and what has gone out.

Three tables, three jobs:

* **feed** -- the source list *and* its fetch health, in one table on purpose.
  Every sibling bundle keeps what-is-watched in a JSON file and the health in
  the database; this one cannot, because ``add`` writes sources at run time and
  a file the tools also wrote would be a second truth that drifts. ``enabled``
  is a column here rather than a key in a file for exactly that reason.
* **item** -- every headline ever seen. ``reported_at`` doubles as the ledger,
  so there is no second table to keep in sync: an item is pending when it has
  not been stamped.
* **runs** -- one row per check including the failures, which is the agent's
  whole triage surface. It never parses stdout.

Nothing is ever deleted. A feed that has gone bad is disabled, not dropped --
its items still name it, and its row keeps the record of why it went quiet.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from . import settings
from .errors import DatabaseError
from .models import Feed, Item, pack

SCHEMA = """
CREATE TABLE IF NOT EXISTS feed (
  id                    INTEGER PRIMARY KEY,
  name                  TEXT NOT NULL UNIQUE,
  url                   TEXT NOT NULL UNIQUE,
  category              TEXT NOT NULL DEFAULT 'general',
  note                  TEXT,
  enabled               INTEGER NOT NULL DEFAULT 1,
  origin                TEXT NOT NULL DEFAULT 'seed',    -- seed | added
  gate_verdict          TEXT,                            -- pass, or why it was held back
  gate_detail           TEXT,
  added_at              TEXT NOT NULL,
  state_changed_at      TEXT,
  last_check_at         TEXT,
  last_ok_at            TEXT,
  etag                  TEXT,
  last_modified         TEXT,
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  seeded                INTEGER NOT NULL DEFAULT 0,      -- 1 once a cold start has been absorbed
  recent_yield          INTEGER NOT NULL DEFAULT 0       -- entries on the last successful check
);

CREATE TABLE IF NOT EXISTS item (
  id              INTEGER PRIMARY KEY,
  fingerprint     TEXT NOT NULL UNIQUE,   -- canonical url, else feed|guid, else feed|hash(title)
  feed            TEXT NOT NULL,          -- the feed that saw it first
  feed_category   TEXT NOT NULL,
  title           TEXT NOT NULL,
  url             TEXT NOT NULL,
  summary         TEXT,                   -- the publisher's own header paragraph, capped
  published_text  TEXT,                   -- the publisher's own words, never parsed
  sectors         TEXT,                   -- comma-joined hints from classify.py
  signals         TEXT,
  first_seen_at   TEXT NOT NULL,
  reported_at     TEXT,                   -- the ledger; NULL means pending
  run_id          INTEGER
);

CREATE INDEX IF NOT EXISTS item_pending ON item (reported_at);
CREATE INDEX IF NOT EXISTS item_feed_seen ON item (feed, first_seen_at);

CREATE TABLE IF NOT EXISTS runs (
  id             INTEGER PRIMARY KEY,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  status         TEXT NOT NULL,           -- ok | partial | skipped | error
  feeds_checked  INTEGER NOT NULL DEFAULT 0,
  entries_seen   INTEGER NOT NULL DEFAULT 0,
  items_new      INTEGER NOT NULL DEFAULT 0,
  items_returned INTEGER NOT NULL DEFAULT 0,
  errors         INTEGER NOT NULL DEFAULT 0,
  detail         TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database, creating it if needed.

    WAL because the writer is a cron job and the reader is an agent session that
    may be running at the same time: without it a check landing mid-``feeds``
    blocks one of them for no reason.
    """
    resolved = Path(path) if path is not None else settings.db_path()
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(resolved, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseError(
            f"could not open the database at {resolved}: {exc}", path=str(resolved)
        ) from exc
    return conn


# --------------------------------------------------------------------------- feeds


def bootstrap(conn: sqlite3.Connection, seeds, now: datetime) -> list[str]:
    """Insert the shipped feeds into an empty database. Returns what was added.

    ``DO NOTHING`` on both unique columns, so this is safe on every run and a
    seed whose url was later replaced by hand is left alone.
    """
    added = []
    for seed in seeds:
        cursor = conn.execute(
            """INSERT INTO feed (name, url, category, note, enabled, origin, gate_verdict, added_at)
               VALUES (?, ?, ?, ?, 1, 'seed', 'seeded', ?)
               ON CONFLICT DO NOTHING""",
            (seed.name, seed.url, seed.category, seed.note, now.isoformat()),
        )
        if cursor.rowcount:
            added.append(seed.name)
    conn.commit()
    return added


def add_feed(
    conn: sqlite3.Connection,
    *,
    name: str,
    url: str,
    category: str,
    note: str | None,
    enabled: bool,
    origin: str,
    gate_verdict: str,
    gate_detail: str | None,
    now: datetime,
) -> int:
    cursor = conn.execute(
        """INSERT INTO feed (name, url, category, note, enabled, origin,
                             gate_verdict, gate_detail, added_at, state_changed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name, url, category, note, 1 if enabled else 0, origin,
            gate_verdict, gate_detail, now.isoformat(), now.isoformat(),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def find_feed(conn: sqlite3.Connection, reference: str) -> Feed | None:
    """A feed by name or by url, whichever the caller had to hand."""
    row = conn.execute(
        "SELECT * FROM feed WHERE name = ? OR url = ?", (reference, reference)
    ).fetchone()
    return Feed.from_row(row) if row else None


def feeds(conn: sqlite3.Connection, *, include_disabled: bool = True) -> list[Feed]:
    sql = "SELECT * FROM feed"
    if not include_disabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY category, name"
    return [Feed.from_row(row) for row in conn.execute(sql).fetchall()]


def select_feeds(
    conn: sqlite3.Connection,
    names: list[str] | None,
    *,
    include_disabled: bool = False,
) -> list[Feed]:
    """The feeds a command should act on.

    Named feeds are returned even when disabled -- naming one is an explicit
    request for it, and silently returning nothing would look like a broken
    feed rather than a paused one.
    """
    if not names:
        return feeds(conn, include_disabled=include_disabled)
    chosen = []
    for name in names:
        feed = find_feed(conn, name)
        if feed is not None:
            chosen.append(feed)
    return chosen


def set_enabled(conn: sqlite3.Connection, name: str, enabled: bool, now: datetime) -> bool:
    cursor = conn.execute(
        "UPDATE feed SET enabled = ?, state_changed_at = ? WHERE name = ? AND enabled != ?",
        (1 if enabled else 0, now.isoformat(), name, 1 if enabled else 0),
    )
    conn.commit()
    return bool(cursor.rowcount)


def record_feed_success(
    conn: sqlite3.Connection,
    name: str,
    now: datetime,
    *,
    etag: str | None,
    last_modified: str | None,
    yield_count: int,
    seeded: bool | None = None,
) -> None:
    """Stamp a successful check.

    ``recent_yield`` is only moved when the feed actually produced entries: a
    zero-yield check must leave the previous count in place, or the zero-yield
    guard would forget what the feed used to return and never fire again after
    the first broken check.
    """
    conn.execute(
        """UPDATE feed
              SET last_check_at = ?, last_ok_at = ?, etag = ?, last_modified = ?,
                  consecutive_failures = 0, last_error = NULL
            WHERE name = ?""",
        (now.isoformat(), now.isoformat(), etag, last_modified, name),
    )
    if yield_count > 0:
        conn.execute("UPDATE feed SET recent_yield = ? WHERE name = ?", (yield_count, name))
    if seeded is not None:
        conn.execute("UPDATE feed SET seeded = ? WHERE name = ?", (1 if seeded else 0, name))
    conn.commit()


def record_feed_failure(conn: sqlite3.Connection, name: str, now: datetime, error: str) -> None:
    conn.execute(
        """UPDATE feed
              SET last_check_at = ?, last_error = ?,
                  consecutive_failures = consecutive_failures + 1
            WHERE name = ?""",
        (now.isoformat(), error, name),
    )
    conn.commit()


# --------------------------------------------------------------------------- items


def find_item(conn: sqlite3.Connection, fingerprint: str) -> Item | None:
    row = conn.execute("SELECT * FROM item WHERE fingerprint = ?", (fingerprint,)).fetchone()
    return Item.from_row(row) if row else None


def resolve_item(conn: sqlite3.Connection, reference: str | int) -> Item | None:
    """An item by row id or by fingerprint, whichever the caller had to hand."""
    try:
        row_id = int(reference)
    except (TypeError, ValueError):
        return find_item(conn, str(reference))
    row = conn.execute("SELECT * FROM item WHERE id = ?", (row_id,)).fetchone()
    return Item.from_row(row) if row else find_item(conn, str(reference))


def insert_item(
    conn: sqlite3.Connection,
    entry,
    *,
    category: str,
    sectors: list[str],
    signals: list[str],
    now: datetime,
    run_id: int | None = None,
    reported: bool = False,
) -> int | None:
    """Store a newly seen entry, or ``None`` if another feed got there first.

    ``reported`` pre-stamps the ledger -- that is cold-start seeding, and the
    only way an item is ever born already handed over.
    """
    cursor = conn.execute(
        """INSERT INTO item (fingerprint, feed, feed_category, title, url, summary,
                             published_text, sectors, signals, first_seen_at, reported_at, run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (fingerprint) DO NOTHING""",
        (
            entry.fingerprint, entry.feed, category, entry.title, entry.url, entry.summary,
            entry.published_text, pack(sectors), pack(signals), now.isoformat(),
            now.isoformat() if reported else None, run_id,
        ),
    )
    conn.commit()
    return cursor.lastrowid if cursor.rowcount else None


def pending_items(
    conn: sqlite3.Connection,
    feed_names: list[str] | None = None,
    limit: int | None = None,
) -> list[Item]:
    """Everything not yet handed over, oldest first so a backlog drains in order.

    Note what this does *not* take: a time range. "Since the last run" is
    defined by the ledger, not by the clock, which is why a missed cron run
    costs nothing and a caught-up one repeats nothing.
    """
    sql = "SELECT * FROM item WHERE reported_at IS NULL"
    params: list = []
    if feed_names is not None:
        if not feed_names:
            return []
        sql += f" AND feed IN ({','.join('?' * len(feed_names))})"
        params.extend(feed_names)
    sql += " ORDER BY id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [Item.from_row(row) for row in conn.execute(sql, params).fetchall()]


def mark_reported(conn: sqlite3.Connection, item_ids: list[int], now: datetime) -> list[int]:
    """Stamp items as handed over. Returns the ones that were actually pending."""
    stamped = []
    for item_id in item_ids:
        cursor = conn.execute(
            "UPDATE item SET reported_at = ? WHERE id = ? AND reported_at IS NULL",
            (now.isoformat(), item_id),
        )
        if cursor.rowcount:
            stamped.append(item_id)
    conn.commit()
    return stamped


def pending_count(conn: sqlite3.Connection) -> dict:
    count = conn.execute("SELECT COUNT(*) FROM item WHERE reported_at IS NULL").fetchone()[0]
    return {"pending_items": count}


def recent_items(
    conn: sqlite3.Connection,
    *,
    feed_names: list[str] | None = None,
    since: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> list[Item]:
    """Newest first. ``state`` is ``pending`` | ``reported`` | ``None``."""
    clauses, params = [], []
    if feed_names is not None:
        if not feed_names:
            return []
        clauses.append(f"feed IN ({','.join('?' * len(feed_names))})")
        params.extend(feed_names)
    if since:
        clauses.append("first_seen_at >= ?")
        params.append(since)
    if state == "pending":
        clauses.append("reported_at IS NULL")
    elif state == "reported":
        clauses.append("reported_at IS NOT NULL")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM item{where} ORDER BY id DESC LIMIT ?", (*params, limit)
    ).fetchall()
    return [Item.from_row(row) for row in rows]


def feed_counts(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT feed,
                  COUNT(*) AS items,
                  SUM(CASE WHEN reported_at IS NULL THEN 1 ELSE 0 END) AS pending,
                  MAX(first_seen_at) AS latest_seen_at
             FROM item GROUP BY feed"""
    ).fetchall()
    return {row["feed"]: dict(row) for row in rows}


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
        """UPDATE runs SET finished_at = ?, status = ?, feeds_checked = ?, entries_seen = ?,
                           items_new = ?, items_returned = ?, errors = ?, detail = ?
            WHERE id = ?""",
        (
            now.isoformat(), status,
            counts.get("feeds_checked", 0), counts.get("entries_seen", 0),
            counts.get("items_new", 0), counts.get("items_returned", 0),
            counts.get("errors", 0), detail, run_id,
        ),
    )
    conn.commit()


def recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]
