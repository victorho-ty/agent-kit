"""SQLite: the snapshot history, and the memory of what has been reported.

Three tables, one shape. A ``snap`` writes one ``snapshots`` row, one
``meeting_snapshots`` row per meeting, and one ``outcomes`` row per target-range
cell. That is the time series: ordered by ``taken_at`` it is history, grouped by
``meeting_date`` it is one meeting's path.

``snapshots.reported_at`` is the whole of the change-detection design. It is
stamped only when ``check-changes`` actually reports something, so the baseline
stays put through every quiet poll. A drift of a third of a point an hour is
therefore still measured against where it started rather than against an
hour ago, and is reported once it adds up -- which is precisely what a
threshold compared against the previous *poll* would hide forever.

Timestamps are stored as the isoformat of a timezone-aware instant in one fixed
zone, so lexical ordering is chronological ordering. Meeting dates are ISO8601
for the same reason -- it is what lets the purge compare them in SQL.

**This is not an archive.** A meeting that has been decided is dropped from
every hydrated snapshot and its rows are deleted outright. A snapshot records
what was upcoming when it was taken, and handing that back verbatim days later
is how an announced decision gets presented as a forecast. See
:func:`purge_past_meetings` and :func:`_hydrate`.

Writes go through :func:`connect` and nothing else. The agent never opens this
file.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from . import clock, settings
from .errors import DatabaseError

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at     TEXT NOT NULL UNIQUE,
    effr         REAL NOT NULL,
    effr_as_of   TEXT NOT NULL,
    target_low   REAL NOT NULL,
    target_high  REAL NOT NULL,
    price_source TEXT NOT NULL,
    status       TEXT NOT NULL,
    -- Null until check-changes has reported on this snapshot. See the module
    -- docstring: this is a baseline marker, not an audit trail.
    reported_at  TEXT
);
CREATE INDEX IF NOT EXISTS snapshots_recent ON snapshots (taken_at DESC);
CREATE INDEX IF NOT EXISTS snapshots_reported ON snapshots (reported_at DESC);

CREATE TABLE IF NOT EXISTS meeting_snapshots (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    meeting_date  TEXT NOT NULL,
    ordinal       INTEGER NOT NULL,
    contract      TEXT NOT NULL,
    price         REAL NOT NULL,
    price_as_of   TEXT,
    implied_rate  REAL NOT NULL,
    expected_rate REAL NOT NULL,
    method        TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, meeting_date)
);
CREATE INDEX IF NOT EXISTS meeting_snapshots_by_meeting
    ON meeting_snapshots (meeting_date, snapshot_id DESC);

CREATE TABLE IF NOT EXISTS outcomes (
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    meeting_date TEXT NOT NULL,
    step         INTEGER NOT NULL,
    band_low     REAL NOT NULL,
    band_high    REAL NOT NULL,
    probability  REAL NOT NULL,
    PRIMARY KEY (snapshot_id, meeting_date, step)
);
"""


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open the database, creating and migrating it if needed."""
    resolved = Path(path) if path else settings.db_path()
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(resolved, timeout=30.0)
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseError(
            f"could not open the database at {resolved}", path=str(resolved)
        ) from exc

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


def store_snapshot(
    conn: sqlite3.Connection,
    taken_at: datetime,
    policy,
    meetings,
    price_source: str,
    status: str,
    price_as_of: dict[str, str | None],
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO snapshots
            (taken_at, effr, effr_as_of, target_low, target_high, price_source, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            taken_at.isoformat(),
            policy.effr,
            policy.as_of.isoformat(),
            policy.target_low,
            policy.target_high,
            price_source,
            status,
        ),
    )
    snapshot_id = int(cursor.lastrowid)

    for meeting in meetings:
        conn.execute(
            """
            INSERT INTO meeting_snapshots
                (snapshot_id, meeting_date, ordinal, contract, price, price_as_of,
                 implied_rate, expected_rate, method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                meeting.meeting_date.isoformat(),
                meeting.ordinal,
                meeting.contract,
                meeting.price,
                price_as_of.get(meeting.contract),
                meeting.implied_rate,
                meeting.expected_rate,
                meeting.method,
            ),
        )
        conn.executemany(
            """
            INSERT INTO outcomes
                (snapshot_id, meeting_date, step, band_low, band_high, probability)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    meeting.meeting_date.isoformat(),
                    outcome.step,
                    outcome.band_low,
                    outcome.band_high,
                    outcome.probability,
                )
                for outcome in meeting.outcomes
            ],
        )
    return snapshot_id


def load_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return _hydrate(conn, row) if row else None


def latest_reported(conn: sqlite3.Connection) -> dict | None:
    """The snapshot check-changes last reported on -- the change baseline."""
    row = conn.execute(
        "SELECT * FROM snapshots WHERE reported_at IS NOT NULL ORDER BY taken_at DESC LIMIT 1"
    ).fetchone()
    return _hydrate(conn, row) if row else None


def reported_history(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """The last ``limit`` reported snapshots, oldest first.

    Only reported ones. A chart of every poll would be a chart of the futures
    price breathing; a chart of the reported ones is a chart of the changes the
    caller was actually told about.
    """
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE reported_at IS NOT NULL ORDER BY taken_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_hydrate(conn, row) for row in reversed(rows)]


def mark_reported(conn: sqlite3.Connection, snapshot_id: int, reported_at: datetime) -> None:
    conn.execute(
        "UPDATE snapshots SET reported_at = ? WHERE id = ?",
        (reported_at.isoformat(), snapshot_id),
    )


def purge_past_meetings(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Delete every stored reading for a meeting that has already happened.
    ``meeting_date < today``, so a meeting is kept through its own decision day:
    the announcement lands in the afternoon and until it does the futures are
    still pricing it. That is the same boundary :func:`fed_watch.config.fomc.upcoming`
    draws, and the two must not disagree.

    Dates are stored as ISO8601, so the lexical comparison SQLite makes here is
    the chronological one.
    """
    cutoff = (today or clock.today()).isoformat()
    outcomes = conn.execute("DELETE FROM outcomes WHERE meeting_date < ?", (cutoff,)).rowcount
    meetings = conn.execute(
        "DELETE FROM meeting_snapshots WHERE meeting_date < ?", (cutoff,)
    ).rowcount
    return {
        "cutoff": cutoff,
        "meetings": max(meetings, 0),
        "outcomes": max(outcomes, 0),
    }


def count_snapshots(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"])


def _hydrate(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """One snapshot with its meetings and their outcome tables attached.

    **Meetings that have already happened are dropped here**, which is the only
    reason every reader gets that for free. A stored snapshot is a record of
    what was upcoming when it was taken, and replaying it verbatim days later is
    how a decided meeting ends up presented as a forecast -- charted from stale
    points, or listed as the next decision because no change has been reported
    since it passed. The fetch path has always filtered by today; this is the
    same filter on the way back out.

    Purging removes these rows for good (see :func:`purge_past_meetings`), but
    only runs when something is written. Between purges this is what holds.
    """
    cutoff = clock.today().isoformat()
    snapshot = dict(row)
    meetings = [
        dict(entry)
        for entry in conn.execute(
            """
            SELECT * FROM meeting_snapshots
            WHERE snapshot_id = ? AND meeting_date >= ? ORDER BY ordinal
            """,
            (row["id"], cutoff),
        ).fetchall()
    ]
    # Ordinals are renumbered because they are read as "1 is the next decision".
    # Carrying the stored ones through a filter would leave a payload whose
    # first meeting is ordinal 2, which says the next decision is missing.
    for position, meeting in enumerate(meetings, 1):
        meeting["ordinal"] = position
    for meeting in meetings:
        meeting["outcomes"] = [
            dict(outcome)
            for outcome in conn.execute(
                """
                SELECT step, band_low, band_high, probability FROM outcomes
                WHERE snapshot_id = ? AND meeting_date = ? ORDER BY step
                """,
                (row["id"], meeting["meeting_date"]),
            ).fetchall()
        ]
    snapshot["meetings"] = meetings
    return snapshot
