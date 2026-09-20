"""SQLite persistence: schema, connection, expense writes and member aliases."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import categories, config

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member      TEXT    NOT NULL,
    description TEXT    NOT NULL,
    keyword     TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    currency    TEXT    NOT NULL,
    ts          TEXT    NOT NULL,   -- local ISO8601, drives all date bucketing
    ts_utc      TEXT    NOT NULL,
    message_id  TEXT,
    item_index  INTEGER NOT NULL,
    source_text TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expenses_ts ON expenses (ts);
CREATE INDEX IF NOT EXISTS idx_expenses_member ON expenses (member);
CREATE UNIQUE INDEX IF NOT EXISTS idx_expenses_message ON expenses (message_id, item_index)
    WHERE message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS keyword_category (
    keyword    TEXT PRIMARY KEY,
    category   TEXT NOT NULL,
    source     TEXT NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    alias      TEXT PRIMARY KEY,   -- lowercased telegram handle / id / nickname
    member     TEXT NOT NULL,      -- canonical display name
    created_at TEXT NOT NULL
);
"""


def connect(path=None) -> sqlite3.Connection:
    target = path or config.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    categories.seed(conn)
    return conn


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_member(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("SELECT member FROM members WHERE alias = ?", (name.strip().lower(),)).fetchone()
    return row["member"] if row else name.strip()


def set_alias(conn: sqlite3.Connection, alias: str, member: str) -> None:
    conn.execute(
        "INSERT INTO members (alias, member, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT(alias) DO UPDATE SET member = excluded.member",
        (alias.strip().lower(), member.strip(), _now_utc()),
    )
    conn.commit()


def insert_expense(
    conn: sqlite3.Connection,
    *,
    member: str,
    description: str,
    keyword: str,
    category: str,
    amount: float,
    currency: str,
    ts_local: str,
    ts_utc: str,
    message_id: str | None,
    item_index: int,
    source_text: str,
) -> int | None:
    """Insert one expense. Returns None when (message_id, item_index) already exists."""
    try:
        cur = conn.execute(
            "INSERT INTO expenses (member, description, keyword, category, amount, currency, ts, ts_utc,"
            " message_id, item_index, source_text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                member,
                description,
                keyword,
                category,
                amount,
                currency,
                ts_local,
                ts_utc,
                message_id,
                item_index,
                source_text,
                _now_utc(),
            ),
        )
    except sqlite3.IntegrityError:
        return None
    return cur.lastrowid


def recategorize(conn: sqlite3.Connection) -> list[dict]:
    """Re-resolve every still-uncategorized expense against the current mapping."""
    mapping = categories.load_mapping(conn)
    updated = []
    rows = conn.execute(
        "SELECT id, description FROM expenses WHERE category = ?", (categories.UNCATEGORIZED,)
    ).fetchall()
    for row in rows:
        category, keyword = categories.resolve(mapping, row["description"])
        if category:
            conn.execute(
                "UPDATE expenses SET category = ?, keyword = ? WHERE id = ?", (category, keyword, row["id"])
            )
            updated.append({"id": row["id"], "description": row["description"], "category": category})
    conn.commit()
    return updated


def delete_expense(conn: sqlite3.Connection, expense_id: int) -> bool:
    cur = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    return cur.rowcount > 0


def unmapped(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT keyword, description, COUNT(*) AS n, SUM(amount) AS total FROM expenses"
        " WHERE category = ? GROUP BY keyword ORDER BY n DESC",
        (categories.UNCATEGORIZED,),
    ).fetchall()
    return [dict(r) for r in rows]
