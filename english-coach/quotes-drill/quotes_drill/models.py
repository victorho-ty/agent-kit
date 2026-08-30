"""Row shapes. Everything the CLI prints is built from these."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Entry:
    id: int
    text: str
    kind: str
    category: str
    source: str | None
    note: str | None
    status: str
    times_tested: int
    last_tested_at: str | None
    last_score: int | None
    streak: int
    next_due_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Entry":
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Attempt:
    id: int
    entry_id: int
    score: int
    transcript: str | None
    feedback: str | None
    error_kind: str | None
    style: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Attempt":
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})

    def as_dict(self) -> dict:
        return asdict(self)
