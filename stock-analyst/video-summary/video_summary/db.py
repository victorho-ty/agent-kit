"""SQLite: what has been posted, what was said in it, and what has gone out.

Adapted from news-radar/news_radar/db.py. Three tables, three jobs:

* **feed_state** -- one row per feed: its conditional-GET validators, its
  failure streak, whether it has been seeded, what it yielded last time, and
  when it was last checked (which is also what the per-feed throttle reads).
* **video** -- every video ever seen, with its transcript state alongside it.
  ``summarised_at`` doubles as the ledger, so there is no second table to keep
  in sync: a video is pending when it has not been stamped.
* **runs** -- one row per check including the failures, which is the agent's
  whole triage surface. It never parses stdout.

Nothing is ever deleted.

**The identity is YouTube's own ``video_id``, and it is globally unique here.**
news-radar had to hash source, url and title together because two outlets
publishing the same story are genuinely two items. Here a channel feed and a
playlist feed carrying the same upload are the same upload, and sending it twice
would be a bug rather than a feature -- so the id YouTube already assigned is
the primary key and the second feed to see a video simply does not re-insert it.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from . import settings
from .models import Video

SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_state (
  feed                  TEXT PRIMARY KEY,
  first_seen_at         TEXT NOT NULL,
  last_check_at         TEXT,
  last_ok_at            TEXT,
  etag                  TEXT,
  last_modified         TEXT,
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  seeded                INTEGER NOT NULL DEFAULT 0,   -- 1 once a cold start has been absorbed
  recent_yield          INTEGER NOT NULL DEFAULT 0    -- entries on the last successful check
);

CREATE TABLE IF NOT EXISTS video (
  id                   INTEGER PRIMARY KEY,
  video_id             TEXT NOT NULL UNIQUE,          -- YouTube's own id; globally unique
  feed                 TEXT NOT NULL,                 -- the feed that saw it first
  channel              TEXT,
  channel_url          TEXT,
  title                TEXT NOT NULL,
  url                  TEXT NOT NULL,
  thumbnail_url        TEXT,                          -- handed to Telegram as a string
  kind                 TEXT NOT NULL DEFAULT 'unknown',  -- short | video | unknown
  published_text       TEXT,                          -- YouTube's own words, never parsed
  description          TEXT,
  first_seen_at        TEXT NOT NULL,
  transcript_status    TEXT NOT NULL DEFAULT 'pending',
  transcript_path      TEXT,
  transcript_chars     INTEGER,
  transcript_lang      TEXT,
  transcript_error     TEXT,
  transcript_attempts  INTEGER NOT NULL DEFAULT 0,
  summarised_at        TEXT,                          -- the ledger; NULL means pending
  run_id               INTEGER
);

CREATE INDEX IF NOT EXISTS video_pending ON video (summarised_at);
CREATE INDEX IF NOT EXISTS video_feed_seen ON video (feed, first_seen_at);

CREATE TABLE IF NOT EXISTS runs (
  id                  INTEGER PRIMARY KEY,
  started_at          TEXT NOT NULL,
  finished_at         TEXT,
  status              TEXT NOT NULL,                  -- ok | partial | skipped | error
  feeds_checked       INTEGER NOT NULL DEFAULT 0,
  entries_seen        INTEGER NOT NULL DEFAULT 0,
  videos_new          INTEGER NOT NULL DEFAULT 0,
  videos_excluded     INTEGER NOT NULL DEFAULT 0,
  transcripts_ok      INTEGER NOT NULL DEFAULT 0,
  transcripts_failed  INTEGER NOT NULL DEFAULT 0,
  errors              INTEGER NOT NULL DEFAULT 0,
  detail              TEXT
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


def normalize(value: str | None) -> str:
    """Fold case, width and whitespace, for the exclude list only."""
    if not value:
        return ""
    folded = unicodedata.normalize("NFKC", value).strip().casefold()
    return _WHITESPACE.sub(" ", folded)


# --------------------------------------------------------------------------- feed state


def feed_state(conn: sqlite3.Connection, feed: str) -> dict | None:
    row = conn.execute("SELECT * FROM feed_state WHERE feed = ?", (feed,)).fetchone()
    return dict(row) if row else None


def ensure_feed(conn: sqlite3.Connection, feed: str, now: datetime) -> dict:
    conn.execute(
        "INSERT INTO feed_state (feed, first_seen_at) VALUES (?, ?) ON CONFLICT (feed) DO NOTHING",
        (feed, now.isoformat()),
    )
    conn.commit()
    return feed_state(conn, feed)


def throttled_until(state: dict | None, min_interval_minutes: int, now: datetime) -> datetime | None:
    """When this feed may next be fetched, or ``None`` if it may be fetched now.

    Reads ``last_check_at`` rather than ``last_ok_at``: a feed that is failing
    should be backed off too, not retried at full speed.
    """
    if not min_interval_minutes or not state or not state.get("last_check_at"):
        return None
    try:
        last = datetime.fromisoformat(state["last_check_at"])
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    ready = last + timedelta(minutes=min_interval_minutes)
    return ready if ready > now else None


def record_feed_success(
    conn: sqlite3.Connection,
    feed: str,
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
        """UPDATE feed_state
              SET last_check_at = ?, last_ok_at = ?, etag = ?, last_modified = ?,
                  consecutive_failures = 0, last_error = NULL
            WHERE feed = ?""",
        (now.isoformat(), now.isoformat(), etag, last_modified, feed),
    )
    if yield_count > 0:
        conn.execute("UPDATE feed_state SET recent_yield = ? WHERE feed = ?", (yield_count, feed))
    if seeded is not None:
        conn.execute("UPDATE feed_state SET seeded = ? WHERE feed = ?", (1 if seeded else 0, feed))
    conn.commit()


def record_feed_failure(conn: sqlite3.Connection, feed: str, now: datetime, error: str) -> None:
    conn.execute(
        """UPDATE feed_state
              SET last_check_at = ?, last_error = ?, consecutive_failures = consecutive_failures + 1
            WHERE feed = ?""",
        (now.isoformat(), error, feed),
    )
    conn.commit()


def all_feed_state(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM feed_state").fetchall()
    return {row["feed"]: dict(row) for row in rows}


# --------------------------------------------------------------------------- videos


def find_video(conn: sqlite3.Connection, video_id: str) -> Video | None:
    row = conn.execute("SELECT * FROM video WHERE video_id = ?", (video_id,)).fetchone()
    return Video.from_row(row) if row else None


def resolve_video(conn: sqlite3.Connection, reference: str | int) -> Video | None:
    """A video by YouTube id or by row id, whichever the caller had to hand."""
    video = find_video(conn, str(reference))
    if video:
        return video
    try:
        row_id = int(reference)
    except (TypeError, ValueError):
        return None
    row = conn.execute("SELECT * FROM video WHERE id = ?", (row_id,)).fetchone()
    return Video.from_row(row) if row else None


def insert_video(
    conn: sqlite3.Connection,
    entry,
    now: datetime,
    *,
    run_id: int | None = None,
    kind: str = "unknown",
    summarised: bool = False,
) -> int:
    """Store a newly seen entry.

    ``summarised`` pre-stamps ``summarised_at`` -- that is cold-start seeding,
    and the only way a video is ever born already reported.
    """
    cursor = conn.execute(
        """INSERT INTO video (video_id, feed, channel, channel_url, title, url, thumbnail_url,
                              kind, published_text, description, first_seen_at, summarised_at, run_id)
           VALUES (:video_id, :feed, :channel, :channel_url, :title, :url, :thumbnail_url,
                   :kind, :published_text, :description, :first_seen_at, :summarised_at, :run_id)""",
        {
            "video_id": entry.video_id,
            "feed": entry.feed,
            "channel": entry.channel,
            "channel_url": entry.channel_url,
            "title": entry.title,
            "url": entry.url,
            "thumbnail_url": entry.thumbnail_url,
            "kind": kind,
            "published_text": entry.published_text,
            "description": entry.description,
            "first_seen_at": now.isoformat(),
            "summarised_at": now.isoformat() if summarised else None,
            "run_id": run_id,
        },
    )
    conn.commit()
    return cursor.lastrowid


def record_transcript(conn: sqlite3.Connection, video_id: str, result) -> None:
    """Store the outcome of one transcript attempt, and count it.

    The attempt counter is what stops a video with captions genuinely disabled
    from being asked about every two hours until the heat death of the universe.
    """
    conn.execute(
        """UPDATE video
              SET transcript_status = ?, transcript_path = ?, transcript_chars = ?,
                  transcript_lang = ?, transcript_error = ?,
                  transcript_attempts = transcript_attempts + 1
            WHERE video_id = ?""",
        (result.status, result.path, result.chars, result.language, result.error, video_id),
    )
    conn.commit()


def set_transcript_status(conn: sqlite3.Connection, video_id: str, status: str) -> None:
    """Set the status without counting an attempt -- for ``skipped``."""
    conn.execute("UPDATE video SET transcript_status = ? WHERE video_id = ?", (status, video_id))
    conn.commit()


def pending_videos(
    conn: sqlite3.Connection,
    feeds: list[str] | None = None,
    limit: int | None = None,
) -> list[Video]:
    """Everything not yet summarised, oldest first so a backlog drains in order.

    Note what this does *not* take: a time range. "Since the last message" is
    defined by the ledger, not by the clock, which is why a missed cron run
    costs nothing and a caught-up one repeats nothing.
    """
    sql = "SELECT * FROM video WHERE summarised_at IS NULL"
    params: list = []
    if feeds is not None:
        if not feeds:
            return []
        sql += f" AND feed IN ({','.join('?' * len(feeds))})"
        params.extend(feeds)
    sql += " ORDER BY id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [Video.from_row(row) for row in conn.execute(sql, params).fetchall()]


def mark_summarised(conn: sqlite3.Connection, video_ids: list[str], now: datetime) -> list[str]:
    """Stamp videos as sent. Returns the ones that were actually still pending."""
    if not video_ids:
        return []
    stamped = []
    for video_id in video_ids:
        cursor = conn.execute(
            "UPDATE video SET summarised_at = ? WHERE video_id = ? AND summarised_at IS NULL",
            (now.isoformat(), video_id),
        )
        if cursor.rowcount:
            stamped.append(video_id)
    conn.commit()
    return stamped


def pending_count(conn: sqlite3.Connection) -> dict:
    count = conn.execute("SELECT COUNT(*) FROM video WHERE summarised_at IS NULL").fetchone()[0]
    return {"pending_videos": count}


def recent_videos(
    conn: sqlite3.Connection,
    *,
    feeds: list[str] | None = None,
    since: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> list[Video]:
    """Newest first. ``state`` is ``pending`` | ``summarised`` | ``None``."""
    clauses, params = [], []
    if feeds is not None:
        if not feeds:
            return []
        clauses.append(f"feed IN ({','.join('?' * len(feeds))})")
        params.extend(feeds)
    if since:
        clauses.append("first_seen_at >= ?")
        params.append(since)
    if state == "pending":
        clauses.append("summarised_at IS NULL")
    elif state == "summarised":
        clauses.append("summarised_at IS NOT NULL")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM video{where} ORDER BY id DESC LIMIT ?", (*params, limit)
    ).fetchall()
    return [Video.from_row(row) for row in rows]


def feed_counts(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT feed,
                  COUNT(*) AS videos,
                  SUM(CASE WHEN summarised_at IS NULL THEN 1 ELSE 0 END) AS pending,
                  MAX(first_seen_at) AS latest_seen_at
             FROM video GROUP BY feed"""
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
                           videos_new = ?, videos_excluded = ?, transcripts_ok = ?,
                           transcripts_failed = ?, errors = ?, detail = ?
            WHERE id = ?""",
        (
            now.isoformat(), status,
            counts.get("feeds_checked", 0), counts.get("entries_seen", 0),
            counts.get("videos_new", 0), counts.get("videos_excluded", 0),
            counts.get("transcripts_ok", 0), counts.get("transcripts_failed", 0),
            counts.get("errors", 0), detail, run_id,
        ),
    )
    conn.commit()


def recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]
