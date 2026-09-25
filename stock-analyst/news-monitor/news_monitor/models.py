"""The three things this bundle moves around, and the shapes they reach the agent in.

``Entry`` is what a feed document yielded and has no database identity yet.
``Item`` is a row -- an entry that survived the fingerprint check and was
stored. ``Feed`` is a row of the source list, config and fetch health in one
object because they are one table.
"""

from __future__ import annotations

import dataclasses


def _split(value: str | None) -> list[str]:
    return [part for part in (value or "").split(",") if part]


def _join(values) -> str:
    return ",".join(values)


@dataclasses.dataclass(frozen=True)
class Entry:
    """One thing a feed document said. Not yet stored, not yet deduplicated."""

    feed: str
    title: str
    url: str
    fingerprint: str
    summary: str | None = None
    # The source's own words, kept as a string. Ordering is by the order we first
    # saw things, so nothing here needs this parsed -- and parsing it would only
    # create a way to be wrong about a date nobody reads.
    published_text: str | None = None


@dataclasses.dataclass(frozen=True)
class Feed:
    """One source: what it is, whether it is on, and how it has been behaving."""

    id: int
    name: str
    url: str
    category: str
    note: str | None
    enabled: bool
    origin: str           # seed | added
    gate_verdict: str | None
    added_at: str
    last_check_at: str | None = None
    last_ok_at: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    seeded: bool = False
    recent_yield: int = 0

    @classmethod
    def from_row(cls, row) -> "Feed":
        return cls(
            id=row["id"],
            name=row["name"],
            url=row["url"],
            category=row["category"],
            note=row["note"],
            enabled=bool(row["enabled"]),
            origin=row["origin"],
            gate_verdict=row["gate_verdict"],
            added_at=row["added_at"],
            last_check_at=row["last_check_at"],
            last_ok_at=row["last_ok_at"],
            etag=row["etag"],
            last_modified=row["last_modified"],
            consecutive_failures=row["consecutive_failures"],
            last_error=row["last_error"],
            seeded=bool(row["seeded"]),
            recent_yield=row["recent_yield"],
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "category": self.category,
            "note": self.note,
            "enabled": self.enabled,
            "origin": self.origin,
            "gate_verdict": self.gate_verdict,
            "added_at": self.added_at,
            "last_ok_at": self.last_ok_at,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "recent_yield": self.recent_yield,
            "seeded": self.seeded,
        }


@dataclasses.dataclass(frozen=True)
class Item:
    """One stored headline, and whether it has been handed over yet."""

    id: int
    fingerprint: str
    feed: str
    feed_category: str
    title: str
    url: str
    summary: str | None
    published_text: str | None
    sectors: list[str]
    signals: list[str]
    first_seen_at: str
    reported_at: str | None

    @classmethod
    def from_row(cls, row) -> "Item":
        return cls(
            id=row["id"],
            fingerprint=row["fingerprint"],
            feed=row["feed"],
            feed_category=row["feed_category"],
            title=row["title"],
            url=row["url"],
            summary=row["summary"],
            published_text=row["published_text"],
            sectors=_split(row["sectors"]),
            signals=_split(row["signals"]),
            first_seen_at=row["first_seen_at"],
            reported_at=row["reported_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "feed": self.feed,
            "category": self.feed_category,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "published_text": self.published_text,
            "sector_hints": self.sectors,
            "signals": self.signals,
            "first_seen_at": self.first_seen_at,
            "reported_at": self.reported_at,
        }


def pack(values) -> str:
    return _join(values)
