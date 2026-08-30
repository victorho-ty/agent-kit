"""Progress, as facts rather than encouragement.

The agent is told to show progress with numbers it did not invent, so every
figure a session can quote -- how many entries, how many due, the day streak,
which entries keep going wrong -- is computed here and handed over whole.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import clock


def collect(conn: sqlite3.Connection, now: datetime, *, weakest: int = 5) -> dict:
    totals = {
        row["status"]: row["entries"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS entries FROM entry GROUP BY status"
        )
    }
    entries = [dict(row) for row in conn.execute("SELECT * FROM entry WHERE status = 'active'")]
    due_now = sum(
        1
        for e in entries
        if e["times_tested"] == 0
        or e["next_due_at"] is None
        or clock.parse(e["next_due_at"]) <= now
    )

    return {
        "entries": {
            "active": totals.get("active", 0),
            "retired": totals.get("retired", 0),
            "never_tested": sum(1 for e in entries if e["times_tested"] == 0),
            "due_now": due_now,
        },
        "attempts": _attempt_counts(conn, now),
        "day_streak": _day_streak(conn, now),
        "by_category": _by_category(conn),
        "weakest": _weakest(conn, weakest),
    }


def _attempt_counts(conn: sqlite3.Connection, now: datetime) -> dict:
    def since(days: int) -> str:
        return clock.to_iso(now - timedelta(days=days))

    row = conn.execute(
        "SELECT COUNT(*) AS total,"
        "       SUM(created_at >= ?) AS last_7d,"
        "       SUM(created_at >= ?) AS last_30d"
        " FROM attempt",
        (since(7), since(30)),
    ).fetchone()
    recent = conn.execute(
        "SELECT AVG(score) AS mean FROM (SELECT score FROM attempt"
        "  ORDER BY created_at DESC, id DESC LIMIT 20)"
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "last_7d": row["last_7d"] or 0,
        "last_30d": row["last_30d"] or 0,
        "mean_score_last_20": round(recent["mean"], 2) if recent["mean"] is not None else None,
    }


def _day_streak(conn: sqlite3.Connection, now: datetime) -> int:
    """Consecutive days ending today (or yesterday) with at least one attempt.

    Yesterday still counts: a streak should not read as broken at breakfast,
    before the day's drill has happened.
    """
    days = [
        row["day"]
        for row in conn.execute(
            "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM attempt"
            " ORDER BY day DESC LIMIT 400"
        )
    ]
    if not days:
        return 0
    today = now.date()
    expected = today if days[0] == today.isoformat() else today - timedelta(days=1)
    streak = 0
    for day in days:
        if day != expected.isoformat():
            break
        streak += 1
        expected -= timedelta(days=1)
    return streak


def _by_category(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT e.category AS category, COUNT(DISTINCT e.id) AS entries,"
        "       COUNT(a.id) AS attempts, AVG(a.score) AS mean_score"
        " FROM entry e LEFT JOIN attempt a ON a.entry_id = e.id"
        " WHERE e.status = 'active'"
        " GROUP BY e.category COLLATE NOCASE"
        " ORDER BY entries DESC, category ASC"
    )
    return [
        {
            "category": row["category"],
            "entries": row["entries"],
            "attempts": row["attempts"],
            "mean_score": round(row["mean_score"], 2) if row["mean_score"] is not None else None,
        }
        for row in rows
    ]


def _weakest(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """Entries drilled at least twice with the lowest average score."""
    rows = conn.execute(
        "SELECT e.id AS id, e.text AS text, e.category AS category,"
        "       COUNT(a.id) AS attempts, AVG(a.score) AS mean_score, e.last_score AS last_score"
        " FROM entry e JOIN attempt a ON a.entry_id = e.id"
        " WHERE e.status = 'active'"
        " GROUP BY e.id HAVING attempts >= 2"
        " ORDER BY mean_score ASC, attempts DESC LIMIT ?",
        (limit,),
    )
    return [
        {
            "id": row["id"],
            "text": row["text"],
            "category": row["category"],
            "attempts": row["attempts"],
            "mean_score": round(row["mean_score"], 2),
            "last_score": row["last_score"],
        }
        for row in rows
    ]
