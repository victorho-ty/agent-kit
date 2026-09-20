"""Dataclasses mirroring the schema, plus the row -> object mapping."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

STATUSES = ("needs_review", "active", "used", "expired", "void")
EXPIRY_PRECISIONS = ("exact", "end_of_month", "inferred")
SOURCE_KINDS = ("telegram_photo", "telegram_text", "manual", "file")
INBOX_STATES = ("queued", "processing", "done", "failed")


@dataclass(frozen=True)
class Account:
    id: str
    display_name: str
    telegram_user_id: str | None
    chat_id: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Account":
        return cls(
            id=row["id"],
            display_name=row["display_name"],
            telegram_user_id=row["telegram_user_id"],
            chat_id=row["chat_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "telegram_user_id": self.telegram_user_id,
            "chat_id": self.chat_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Predicate:
    kind: str
    params: dict[str, Any] | None
    text: str | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "params": self.params, "text": self.text}


@dataclass(frozen=True)
class MediaRef:
    id: str
    account_id: str
    sha256: str
    path: str
    mime: str
    bytes: int
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MediaRef":
        return cls(
            id=row["id"],
            account_id=row["account_id"],
            sha256=row["sha256"],
            path=row["path"],
            mime=row["mime"],
            bytes=row["bytes"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class Coupon:
    id: str
    account_id: str
    merchant: str
    title: str
    expires_on: str
    expiry_precision: str
    expiry_assumed: bool
    status: str
    uses_total: int
    uses_remaining: int
    conditions: list[Predicate] = field(default_factory=list)
    code: str | None = None
    value_text: str | None = None
    notes: str | None = None
    raw_text: str | None = None
    source_kind: str = "manual"
    source_ref: str | None = None
    media_id: str | None = None
    extraction_confidence: float | None = None
    dedupe_key: str = ""
    created_at: str = ""
    updated_at: str = ""
    used_at: str | None = None
    expired_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Coupon":
        raw_conditions = json.loads(row["conditions_json"] or "[]")
        return cls(
            id=row["id"],
            account_id=row["account_id"],
            merchant=row["merchant"],
            title=row["title"],
            expires_on=row["expires_on"],
            expiry_precision=row["expiry_precision"],
            expiry_assumed=bool(row["expiry_assumed"]),
            status=row["status"],
            uses_total=row["uses_total"],
            uses_remaining=row["uses_remaining"],
            conditions=[
                Predicate(kind=c.get("kind"), params=c.get("params"), text=c.get("text"))
                for c in raw_conditions
            ],
            code=row["code"],
            value_text=row["value_text"],
            notes=row["notes"],
            raw_text=row["raw_text"],
            source_kind=row["source_kind"],
            source_ref=row["source_ref"],
            media_id=row["media_id"],
            extraction_confidence=row["extraction_confidence"],
            dedupe_key=row["dedupe_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            used_at=row["used_at"],
            expired_at=row["expired_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "merchant": self.merchant,
            "title": self.title,
            "code": self.code,
            "value_text": self.value_text,
            "expires_on": self.expires_on,
            "expiry_precision": self.expiry_precision,
            "expiry_assumed": self.expiry_assumed,
            "status": self.status,
            "uses_total": self.uses_total,
            "uses_remaining": self.uses_remaining,
            "conditions": [c.to_dict() for c in self.conditions],
            "notes": self.notes,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "media_id": self.media_id,
            "extraction_confidence": self.extraction_confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "used_at": self.used_at,
            "expired_at": self.expired_at,
        }


@dataclass(frozen=True)
class Candidate:
    """One extracted coupon, before it becomes a row."""

    merchant: str
    title: str
    expires_on: str
    expiry_precision: str = "exact"
    expiry_assumed: bool = False
    uses_total: int = 1
    code: str | None = None
    value_text: str | None = None
    notes: str | None = None
    conditions: list[Predicate] = field(default_factory=list)
    confidence: float | None = None
