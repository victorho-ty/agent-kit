"""Connection, migrations and transactions.

Together with ``accounts.py`` this is the only place that touches an unscoped
connection. Everything else goes through an ``AccountScope``.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

from .errors import DatabaseError

MIGRATIONS_PACKAGE = "coupon_tracker.migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the pragmas the schema depends on.

    ``foreign_keys`` is load-bearing: account deletion relies on the ON DELETE
    CASCADE chains, and they are silently ignored without it.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path, isolation_level=None)
    except sqlite3.Error as exc:  # pragma: no cover - filesystem dependent
        raise DatabaseError(f"cannot open database {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One explicit transaction. Rolls back on any exception.

    Nested use is not supported and never needed: the modules that write take a
    scope and open exactly one transaction at their top-level entry point.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every unapplied migration in name order. Idempotent."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}

    newly_applied: list[str] = []
    for name, sql in _migration_files():
        if name in applied:
            continue
        # executescript() implicitly commits before it runs, so an outer BEGIN
        # would be gone by the time we COMMIT. The transaction has to live
        # inside the script — that keeps the schema change and its
        # schema_migrations row atomic.
        escaped = name.replace("'", "''")
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{sql}\n"
            f"INSERT INTO schema_migrations (name) VALUES ('{escaped}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # already rolled back by the failed script
            raise DatabaseError(f"migration {name} failed: {exc}") from exc
        newly_applied.append(name)
    return newly_applied


def applied_migrations(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM schema_migrations ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def _migration_files() -> list[tuple[str, str]]:
    files = []
    for entry in resources.files(MIGRATIONS_PACKAGE).iterdir():
        if entry.name.endswith(".sql"):
            files.append((entry.name, entry.read_text(encoding="utf-8")))
    return sorted(files)


def open_migrated(db_path: str | Path) -> sqlite3.Connection:
    """Connect and bring the schema up to date — the normal entry point."""
    conn = connect(db_path)
    migrate(conn)
    return conn
