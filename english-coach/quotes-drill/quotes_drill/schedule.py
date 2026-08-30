"""What comes next, and when it comes back. The whole spacing policy is here.

Two decisions, both arithmetic, both deliberately kept away from the model:

* **which entry to drill** -- fewest drills first, then the one left alone
  longest. A model asked to choose would drill what it finds interesting.
* **when that entry is due again** -- a Leitner ladder driven by the score it
  just earned, so a word that keeps coming back easy stops interrupting and a
  word that was missed returns tomorrow.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import clock
from .errors import NoEntriesError
from .models import Entry

# Rungs, in days. Climbing one rung per good answer reaches a month in five
# passes; a miss drops straight back to the bottom.
LADDER_DAYS = [1, 2, 4, 8, 16, 32]
# At or above this score the answer counts as good and the entry climbs.
GOOD_SCORE = 4
# Below this it counts as missed and the streak is lost. A 3 holds its rung.
POOR_SCORE = 3


@dataclass(frozen=True)
class Pick:
    entry: Entry
    reason: str  # never_tested | due | not_due | cooling
    due: bool


def next_state(streak: int, score: int) -> tuple[int, int]:
    """The streak and interval an entry carries after scoring `score`."""
    if score < POOR_SCORE:
        streak = 0
    elif score >= GOOD_SCORE:
        streak += 1
    return streak, LADDER_DAYS[min(streak, len(LADDER_DAYS) - 1)]


def due_at(now: datetime, interval_days: int) -> datetime:
    return now + timedelta(days=interval_days)


def pick(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    count: int = 1,
    category: str | None = None,
    cooldown_hours: float = 0.0,
) -> tuple[list[Pick], dict]:
    """The queue. Never-drilled entries first, then whatever is due.

    Falls back rather than returning nothing: an entry that is merely not due
    yet is still better than a silent drill, and comes back flagged `due:
    false` so the agent can say so. Only an empty store is an error.
    """
    sql = "SELECT * FROM entry WHERE status = 'active'"
    params: list[str] = []
    if category:
        sql += " AND category = ? COLLATE NOCASE"
        params.append(category)
    # The user's rule, spelled as an ORDER BY: least drilled, then the one left
    # alone longest. `id` breaks the tie so two runs never disagree.
    sql += " ORDER BY times_tested ASC, COALESCE(last_tested_at, '') ASC, id ASC"

    entries = [Entry.from_row(row) for row in conn.execute(sql, params)]
    if not entries:
        raise NoEntriesError(
            "no active entries to drill"
            + (f" in category {category!r}" if category else ""),
            {"category": category},
        )

    cutoff = now - timedelta(hours=cooldown_hours)
    cooling = [e for e in entries if _tested_since(e, cutoff)]
    cooling_ids = {e.id for e in cooling}
    ready = [e for e in entries if e.id not in cooling_ids]

    due = [e for e in ready if _is_due(e, now)]
    early = [e for e in ready if not _is_due(e, now)]

    if due:
        picks = [Pick(e, "never_tested" if e.times_tested == 0 else "due", True) for e in due]
    elif early:
        picks = [Pick(e, "not_due", False) for e in early]
    else:
        picks = [Pick(e, "cooling", False) for e in cooling]

    counts = {
        "active": len(entries),
        "due_now": len(due),
        "never_tested": sum(1 for e in entries if e.times_tested == 0),
        "cooling": len(cooling),
    }
    return picks[:count], counts


def _tested_since(entry: Entry, cutoff: datetime) -> bool:
    return entry.last_tested_at is not None and clock.parse(entry.last_tested_at) > cutoff


def _is_due(entry: Entry, now: datetime) -> bool:
    if entry.times_tested == 0 or entry.next_due_at is None:
        return True
    return clock.parse(entry.next_due_at) <= now
