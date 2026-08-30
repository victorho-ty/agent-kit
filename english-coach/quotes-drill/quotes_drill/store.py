"""Reads and writes. Every mutation goes through here.

The two writes that matter are `add_entry` -- which is idempotent on the text
itself, so re-adding a quote someone already saved is a no-op rather than a
duplicate in the queue -- and `record_attempt`, which is the only thing that
moves an entry's drill state.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from datetime import datetime

from . import clock, schedule
from .errors import NotFoundError, UsageError
from .models import Attempt, Entry

KINDS = ("quote", "vocab", "phrase")
ERROR_KINDS = ("none", "accuracy", "context", "register", "grammar", "fluency")
STATUSES = ("active", "retired")
# Apostrophes vanish so `don't` and `dont` are one entry; everything else that
# is not a letter, digit or space becomes a space, so punctuation and casing
# never split the same line into two rows.
APOSTROPHES = "'’ʼ´`"


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = "".join(ch for ch in folded if ch not in APOSTROPHES)
    kept = (ch if (ch.isalnum() or ch.isspace()) else " " for ch in folded)
    return " ".join("".join(kept).split())


def add_entry(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    text: str,
    category: str,
    kind: str = "quote",
    source: str | None = None,
    note: str | None = None,
) -> tuple[Entry, bool]:
    """Insert one entry. Returns it with `created=False` if it was already there."""
    text = text.strip()
    category = category.strip()
    if not text:
        raise UsageError("entry text is empty")
    if not category:
        raise UsageError("every entry needs a category; the agent supplies it")
    if kind not in KINDS:
        raise UsageError(f"kind must be one of {', '.join(KINDS)}", {"kind": kind})

    norm = normalize(text)
    if not norm:
        raise UsageError("entry text has no letters or digits", {"text": text})

    existing = conn.execute("SELECT * FROM entry WHERE norm_text = ?", (norm,)).fetchone()
    if existing is not None:
        return Entry.from_row(existing), False

    stamp = clock.to_iso(now)
    cursor = conn.execute(
        "INSERT INTO entry (text, norm_text, kind, category, source, note,"
        "                   created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (text, norm, kind, category, source, note, stamp, stamp),
    )
    conn.commit()
    return get_entry(conn, cursor.lastrowid), True


def get_entry(conn: sqlite3.Connection, entry_id: int) -> Entry:
    row = conn.execute("SELECT * FROM entry WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"no entry with id {entry_id}", {"entry_id": entry_id})
    return Entry.from_row(row)


def list_entries(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
    status: str | None = "active",
    limit: int = 20,
) -> list[Entry]:
    sql = "SELECT * FROM entry WHERE 1 = 1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if category:
        sql += " AND category = ? COLLATE NOCASE"
        params.append(category)
    sql += " ORDER BY times_tested ASC, COALESCE(last_tested_at, '') ASC, id ASC LIMIT ?"
    params.append(limit)
    return [Entry.from_row(row) for row in conn.execute(sql, params)]


def edit_entry(conn: sqlite3.Connection, now: datetime, entry_id: int, **fields) -> Entry:
    """Change the material, not the drill state. Unset fields are left alone."""
    allowed = ("text", "category", "kind", "source", "note", "status")
    changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not changes:
        raise UsageError("edit needs at least one field to change")
    if changes.get("kind") and changes["kind"] not in KINDS:
        raise UsageError(f"kind must be one of {', '.join(KINDS)}", {"kind": changes["kind"]})
    if changes.get("status") and changes["status"] not in STATUSES:
        raise UsageError(
            f"status must be one of {', '.join(STATUSES)}", {"status": changes["status"]}
        )

    get_entry(conn, entry_id)  # 404 before we write anything
    if "text" in changes:
        changes["norm_text"] = normalize(changes["text"])
        clash = conn.execute(
            "SELECT id FROM entry WHERE norm_text = ? AND id != ?",
            (changes["norm_text"], entry_id),
        ).fetchone()
        if clash is not None:
            raise UsageError(
                f"entry {clash['id']} already holds that text", {"entry_id": clash["id"]}
            )

    changes["updated_at"] = clock.to_iso(now)
    assignments = ", ".join(f"{column} = ?" for column in changes)
    conn.execute(
        f"UPDATE entry SET {assignments} WHERE id = ?", (*changes.values(), entry_id)
    )
    conn.commit()
    return get_entry(conn, entry_id)


def record_attempt(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    entry_id: int,
    score: int,
    transcript: str | None = None,
    feedback: str | None = None,
    error_kind: str | None = None,
    style: str | None = None,
) -> tuple[Entry, int]:
    """Log one answered drill and advance the entry. Returns it and its interval."""
    if not 0 <= score <= 5:
        raise UsageError("score must be between 0 and 5", {"score": score})
    if error_kind is not None and error_kind not in ERROR_KINDS:
        raise UsageError(
            f"error_kind must be one of {', '.join(ERROR_KINDS)}", {"error_kind": error_kind}
        )

    entry = get_entry(conn, entry_id)
    streak, interval_days = schedule.next_state(entry.streak, score)
    stamp = clock.to_iso(now)

    conn.execute(
        "INSERT INTO attempt (entry_id, score, transcript, feedback, error_kind, style,"
        "                     created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (entry_id, score, transcript, feedback, error_kind, style, stamp),
    )
    conn.execute(
        "UPDATE entry SET times_tested = times_tested + 1, last_tested_at = ?,"
        "                 last_score = ?, streak = ?, next_due_at = ?, updated_at = ?"
        " WHERE id = ?",
        (
            stamp,
            score,
            streak,
            clock.to_iso(schedule.due_at(now, interval_days)),
            stamp,
            entry_id,
        ),
    )
    conn.commit()
    return get_entry(conn, entry_id), interval_days


def attempts_for(conn: sqlite3.Connection, entry_id: int, limit: int = 5) -> list[Attempt]:
    rows = conn.execute(
        "SELECT * FROM attempt WHERE entry_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (entry_id, limit),
    )
    return [Attempt.from_row(row) for row in rows]


def categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT category, COUNT(*) AS entries FROM entry WHERE status = 'active'"
        " GROUP BY category COLLATE NOCASE ORDER BY entries DESC, category ASC"
    )
    return [{"category": row["category"], "entries": row["entries"]} for row in rows]
