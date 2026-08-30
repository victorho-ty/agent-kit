"""SQLite: what is being learned, and every attempt at using it.

Two tables, two jobs:

* **entry** -- one row per quote, word or phrase, carrying its own drill state:
  `times_tested`, a single `last_tested_at`, the current spacing `streak` and
  the `next_due_at` those two produce. State lives on the row rather than being
  derived from the attempt log, because the queue is an `ORDER BY` and an
  aggregate would make it a join.
* **attempt** -- one row per drill answered: what was said, what it scored, what
  was wrong with it. This is the history `stats` reads and the reason a weak
  entry can be named. Nothing here is ever deleted; an entry that is finished
  with is `retired`, which keeps its record and takes it out of the queue.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .errors import DatabaseError

SCHEMA = """
CREATE TABLE IF NOT EXISTS entry (
  id             INTEGER PRIMARY KEY,
  text           TEXT NOT NULL,
  norm_text      TEXT NOT NULL UNIQUE,       -- dedupe key; see store.normalize
  kind           TEXT NOT NULL CHECK (kind IN ('quote','vocab','phrase')),
  category       TEXT NOT NULL,              -- the agent's label: Food, Joke, ...
  source         TEXT,                       -- who said it, where it came from
  note           TEXT,                       -- register, trap, why it was kept
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  times_tested   INTEGER NOT NULL DEFAULT 0,
  last_tested_at TEXT,
  last_score     INTEGER,
  streak         INTEGER NOT NULL DEFAULT 0, -- consecutive good answers; drives the ladder
  next_due_at    TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS entry_queue ON entry (status, times_tested, last_tested_at);

CREATE TABLE IF NOT EXISTS attempt (
  id          INTEGER PRIMARY KEY,
  entry_id    INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  score       INTEGER NOT NULL CHECK (score BETWEEN 0 AND 5),
  transcript  TEXT,                          -- what the speaker actually said
  feedback    TEXT,                          -- the coaching line, as spoken back
  error_kind  TEXT CHECK (error_kind IN
                ('none','accuracy','context','register','grammar','fluency')),
  style       TEXT,                          -- the style the model was asked for
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS attempt_entry ON attempt (entry_id, created_at);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the pragmas the schema depends on.

    `foreign_keys` is load-bearing: an entry's attempts go with it through the
    ON DELETE CASCADE, and that is silently ignored without it.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as exc:  # pragma: no cover - filesystem dependent
        raise DatabaseError(f"cannot open database {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    return conn
