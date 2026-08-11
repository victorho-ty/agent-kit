"""SQLite: what we have seen, what we have reported, and how each scan went.

Adapted from education-radar/education_radar/db.py. ``site_state`` and ``runs``
are unchanged; ``listing`` became ``item``, losing the verdict and review
columns and gaining ``source_domain``.

Three tables, three jobs:

* **site_state** -- one row per source: its conditional-GET validators, its
  failure streak, whether it has been seeded, what it yielded last time, and
  when it was last scanned (which is also what the per-source throttle reads).
* **item** -- every candidate ever seen. ``digested_at`` doubles as the ledger,
  so there is no second table to keep in sync: an item is pending when it has
  not been stamped. That is what lets the scan and the digest run on completely
  independent schedules -- a digest asks "what has not been sent", never "what
  happened in the last N hours".
* **runs** -- one row per scan including the failures, which is the agent's
  whole triage surface. It never parses stdout.

Nothing is ever deleted.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import settings
from .models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS site_state (
  source                TEXT PRIMARY KEY,
  first_seen_at         TEXT NOT NULL,
  last_scan_at          TEXT,
  last_ok_at            TEXT,
  etag                  TEXT,
  last_modified         TEXT,
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  seeded                INTEGER NOT NULL DEFAULT 0,   -- 1 once a cold start has been absorbed
  recent_yield          INTEGER NOT NULL DEFAULT 0    -- candidates on the last successful scan
);

CREATE TABLE IF NOT EXISTS item (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,
  item_key       TEXT NOT NULL,
  url            TEXT NOT NULL,
  title          TEXT NOT NULL,
  summary        TEXT,
  detail_text    TEXT,
  date_text      TEXT,                                -- the source's own words, never parsed
  source_domain  TEXT NOT NULL,                       -- 'theverge.com', the digest's label
  first_seen_at  TEXT NOT NULL,
  digested_at    TEXT,                                -- the ledger; NULL means pending
  run_id         INTEGER,
  UNIQUE (source, item_key)
);

CREATE INDEX IF NOT EXISTS item_pending ON item (digested_at);
CREATE INDEX IF NOT EXISTS item_source_seen ON item (source, first_seen_at);

CREATE TABLE IF NOT EXISTS runs (
  id               INTEGER PRIMARY KEY,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  status           TEXT NOT NULL,                     -- ok | partial | skipped | error
  sources_scanned  INTEGER NOT NULL DEFAULT 0,
  items_seen       INTEGER NOT NULL DEFAULT 0,
  items_new        INTEGER NOT NULL DEFAULT 0,
  items_excluded   INTEGER NOT NULL DEFAULT 0,
  errors           INTEGER NOT NULL DEFAULT 0,
  detail           TEXT
);
"""

_WHITESPACE = re.compile(r"\s+")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path is not None else settings.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# --------------------------------------------------------------------------- identity


def normalize(value: str | None) -> str:
    """Fold case, width and whitespace so 'OpenAI  RELEASES' == 'openai releases'."""
    if not value:
        return ""
    folded = unicodedata.normalize("NFKC", value).strip().casefold()
    return _WHITESPACE.sub(" ", folded)


def item_key(source: str, url: str, title: str) -> str:
    """An item's identity *within one source*.

    Title and URL together, because either alone is wrong: a wire page that
    links every headline to the same hub would collapse to one item on URL
    alone, and a site that re-titles a story in place would look new on title
    alone.

    Scoped by source on purpose. Recognising that two *different* sources are
    carrying the same story is a separate job with a separate answer -- see
    :mod:`news_radar.cluster` -- and conflating the two here would mean a
    rewording on one outlet silently suppressed the story everywhere.
    """
    payload = f"{source}|{normalize(url)}|{normalize(title)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def domain_of(url: str) -> str:
    """The bare host, which is what a digest shows as the source label."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# --------------------------------------------------------------------------- source state


def site_state(conn: sqlite3.Connection, source: str) -> dict | None:
    row = conn.execute("SELECT * FROM site_state WHERE source = ?", (source,)).fetchone()
    return dict(row) if row else None


def ensure_source(conn: sqlite3.Connection, source: str, now: datetime) -> dict:
    conn.execute(
        "INSERT INTO site_state (source, first_seen_at) VALUES (?, ?) ON CONFLICT (source) DO NOTHING",
        (source, now.isoformat()),
    )
    conn.commit()
    return site_state(conn, source)


def throttled_until(state: dict | None, min_interval_minutes: int, now: datetime) -> datetime | None:
    """When this source may next be fetched, or ``None`` if it may be fetched now.

    Reads ``last_scan_at`` rather than ``last_ok_at``: a source that is failing
    should be backed off too, not retried at full speed.
    """
    if not min_interval_minutes or not state or not state.get("last_scan_at"):
        return None
    try:
        last = datetime.fromisoformat(state["last_scan_at"])
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    ready = last + timedelta(minutes=min_interval_minutes)
    return ready if ready > now else None


def record_source_success(
    conn: sqlite3.Connection,
    source: str,
    now: datetime,
    *,
    etag: str | None,
    last_modified: str | None,
    yield_count: int,
    seeded: bool | None = None,
) -> None:
    """Stamp a successful scan.

    ``recent_yield`` is only moved when the source actually produced candidates:
    a zero-yield scan must leave the previous count in place, or the zero-yield
    guard would forget what the page used to return and never fire again after
    the first broken scan.
    """
    conn.execute(
        """UPDATE site_state
              SET last_scan_at = ?, last_ok_at = ?, etag = ?, last_modified = ?,
                  consecutive_failures = 0, last_error = NULL
            WHERE source = ?""",
        (now.isoformat(), now.isoformat(), etag, last_modified, source),
    )
    if yield_count > 0:
        conn.execute("UPDATE site_state SET recent_yield = ? WHERE source = ?", (yield_count, source))
    if seeded is not None:
        conn.execute("UPDATE site_state SET seeded = ? WHERE source = ?", (1 if seeded else 0, source))
    conn.commit()


def record_source_failure(conn: sqlite3.Connection, source: str, now: datetime, error: str) -> None:
    conn.execute(
        """UPDATE site_state
              SET last_scan_at = ?, last_error = ?, consecutive_failures = consecutive_failures + 1
            WHERE source = ?""",
        (now.isoformat(), error, source),
    )
    conn.commit()


def all_source_state(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM site_state").fetchall()
    return {row["source"]: dict(row) for row in rows}


# --------------------------------------------------------------------------- items


def find_item(conn: sqlite3.Connection, source: str, key: str) -> Item | None:
    row = conn.execute("SELECT * FROM item WHERE source = ? AND item_key = ?", (source, key)).fetchone()
    return Item.from_row(row) if row else None


def insert_item(
    conn: sqlite3.Connection,
    candidate,
    now: datetime,
    *,
    run_id: int | None = None,
    digested: bool = False,
) -> int:
    """Store a newly seen candidate.

    ``digested`` pre-stamps ``digested_at`` -- that is cold-start seeding, and
    the only way an item is ever born already reported.
    """
    cursor = conn.execute(
        """INSERT INTO item (source, item_key, url, title, summary, detail_text, date_text,
                             source_domain, first_seen_at, digested_at, run_id)
           VALUES (:source, :item_key, :url, :title, :summary, :detail_text, :date_text,
                   :source_domain, :first_seen_at, :digested_at, :run_id)""",
        {
            "source": candidate.source,
            "item_key": item_key(candidate.source, candidate.url, candidate.title),
            "url": candidate.url,
            "title": candidate.title,
            "summary": candidate.summary,
            "detail_text": candidate.detail_text,
            "date_text": candidate.date_text,
            "source_domain": domain_of(candidate.url),
            "first_seen_at": now.isoformat(),
            "digested_at": now.isoformat() if digested else None,
            "run_id": run_id,
        },
    )
    conn.commit()
    return cursor.lastrowid


def pending_items(conn: sqlite3.Connection, sources: list[str] | None = None,
                  limit: int | None = None) -> list[Item]:
    """Everything not yet digested, oldest first so a backlog drains in order.

    Note what this does *not* take: a time range. "Since the last digest" is
    defined by the ledger, not by the clock, which is why the scan and the
    digest can run on schedules that have nothing to do with each other.
    """
    sql = "SELECT * FROM item WHERE digested_at IS NULL"
    params: list = []
    if sources is not None:
        if not sources:
            return []
        sql += f" AND source IN ({','.join('?' * len(sources))})"
        params.extend(sources)
    sql += " ORDER BY id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [Item.from_row(row) for row in conn.execute(sql, params).fetchall()]


def mark_digested(conn: sqlite3.Connection, ids: list[int], now: datetime) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cursor = conn.execute(
        f"UPDATE item SET digested_at = ? WHERE id IN ({placeholders}) AND digested_at IS NULL",
        (now.isoformat(), *ids),
    )
    conn.commit()
    return cursor.rowcount


def pending_count(conn: sqlite3.Connection) -> dict:
    count = conn.execute("SELECT COUNT(*) FROM item WHERE digested_at IS NULL").fetchone()[0]
    return {"pending_items": count}


def recent_items(
    conn: sqlite3.Connection,
    *,
    sources: list[str] | None = None,
    since: str | None = None,
    limit: int = 20,
) -> list[Item]:
    clauses, params = [], []
    if sources is not None:
        if not sources:
            return []
        clauses.append(f"source IN ({','.join('?' * len(sources))})")
        params.extend(sources)
    if since:
        clauses.append("first_seen_at >= ?")
        params.append(since)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM item{where} ORDER BY id DESC LIMIT ?", (*params, limit)
    ).fetchall()
    return [Item.from_row(row) for row in rows]


# --------------------------------------------------------------------------- runs


def start_run(conn: sqlite3.Connection, now: datetime) -> int:
    cursor = conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now.isoformat(),)
    )
    conn.commit()
    return cursor.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, now: datetime, status: str, counts: dict,
               detail: str | None = None) -> None:
    conn.execute(
        """UPDATE runs SET finished_at = ?, status = ?, sources_scanned = ?, items_seen = ?,
                           items_new = ?, items_excluded = ?, errors = ?, detail = ?
            WHERE id = ?""",
        (
            now.isoformat(), status,
            counts.get("sources_scanned", 0), counts.get("items_seen", 0),
            counts.get("items_new", 0), counts.get("items_excluded", 0),
            counts.get("errors", 0), detail, run_id,
        ),
    )
    conn.commit()


def recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]
