"""The shapes that cross module boundaries.

``Candidate``  what the extractor pulled off a page -- no identity yet.
``Item``       a row in the database: a candidate, when it was first seen, and
               whether it has been digested.
``Story``      one or more items that are the same piece of news, assembled at
               digest time and never stored.

Adapted from education-radar/education_radar/models.py. The verdict and review
enums are gone: this skill infers nothing about an item, because the human
already said what a source is about by giving it a category.
"""

from __future__ import annotations

import dataclasses
import sqlite3

# How a page is turned into candidates.
SOURCE_KINDS = ("rss", "html", "regex")

# How a page is retrieved.
RENDER_MODES = ("static", "browser")

# A run's outcome. `skipped` is a success: nothing was enabled to scan.
RUN_STATUSES = ("ok", "partial", "skipped", "error")

# Per-source outcome within a run.
SOURCE_STATUSES = ("ok", "unchanged", "throttled", "zero_yield", "error")


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One item as it appeared on a page or in a feed, before it has an identity."""

    source: str
    title: str
    url: str
    summary: str | None = None
    date_text: str | None = None
    detail_text: str | None = None

    def text_for_filtering(self) -> str:
        """Everything the exclude list is allowed to read."""
        parts = [self.title, self.summary, self.date_text]
        return "\n".join(part for part in parts if part)


@dataclasses.dataclass(frozen=True)
class Item:
    """A row of the ``item`` table."""

    id: int
    source: str
    item_key: str
    url: str
    title: str
    summary: str | None
    detail_text: str | None
    date_text: str | None
    source_domain: str
    first_seen_at: str
    digested_at: str | None
    run_id: int | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Item":
        return cls(
            id=row["id"],
            source=row["source"],
            item_key=row["item_key"],
            url=row["url"],
            title=row["title"],
            summary=row["summary"],
            detail_text=row["detail_text"],
            date_text=row["date_text"],
            source_domain=row["source_domain"],
            first_seen_at=row["first_seen_at"],
            digested_at=row["digested_at"],
            run_id=row["run_id"],
        )

    def to_dict(self) -> dict:
        """The shape the CLI prints. Deliberately flat -- the agent reads it."""
        return {
            "id": self.id,
            "source": self.source,
            "source_domain": self.source_domain,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "date_text": self.date_text,
            "first_seen_at": self.first_seen_at,
            "digested_at": self.digested_at,
        }


@dataclasses.dataclass(frozen=True)
class Story:
    """One piece of news, as carried by one or more sources.

    Assembled by :mod:`news_radar.cluster` at digest time and never stored: the
    membership depends on which items happen to be pending together, so
    persisting it would be recording an accident.
    """

    title: str
    url: str
    items: tuple[Item, ...]

    @property
    def domains(self) -> tuple[str, ...]:
        """Contributing sources, deduplicated, in first-seen order."""
        seen, ordered = set(), []
        for item in self.items:
            if item.source_domain not in seen:
                seen.add(item.source_domain)
                ordered.append(item.source_domain)
        return tuple(ordered)

    def to_dict(self) -> dict:
        primary = self.items[0]
        return {
            "ids": [item.id for item in self.items],
            "title": self.title,
            "url": self.url,
            "sources": list(self.domains),
            "summary": primary.summary,
            "published_text": primary.date_text,
        }
