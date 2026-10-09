"""The shapes that cross module boundaries.

``Entry``  what the feed parser pulled out of the Atom document -- no identity
           in our database yet, though YouTube has already given it one.
``Video``  a row in the database: an entry, when we first saw it, what its
           transcript came to, and whether it has been summarised.

There is no ``Summary`` type, here or in the database. The summary is written by
the agent's model and sent over Telegram; storing a copy would make this bundle
the owner of something it cannot check, and the ledger only needs to know that a
video went out, not what was said about it.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3

# A run's outcome. `skipped` is a success: nothing was enabled to check.
RUN_STATUSES = ("ok", "partial", "skipped", "error")

# Per-feed outcome within a run.
FEED_STATUSES = ("ok", "unchanged", "throttled", "zero_yield", "error")

# What a video is. YouTube's feed does not say, so this is filled in by a cheap
# follow-up request and is `unknown` whenever that request did not happen or did
# not answer. It is a label for the reader, never a filter.
VIDEO_KINDS = ("short", "video", "unknown")

# How the transcript went.
#   pending      -- never attempted (a --no-transcript check, or brand new)
#   ok           -- text on disk
#   unavailable  -- YouTube has no captions for it, or none in a configured language
#   error        -- the attempt itself failed (network, parser, rate limit)
#   skipped      -- the feed says transcript: false
TRANSCRIPT_STATUSES = ("pending", "ok", "unavailable", "error", "skipped")


@dataclasses.dataclass(frozen=True)
class Entry:
    """One ``<entry>`` of a YouTube channel feed."""

    feed: str
    video_id: str
    title: str
    url: str
    channel: str | None = None
    channel_url: str | None = None
    thumbnail_url: str | None = None
    published_text: str | None = None
    description: str | None = None

    def text_for_filtering(self) -> str:
        """Everything the exclude list is allowed to read."""
        parts = [self.title, self.description]
        return "\n".join(part for part in parts if part)


@dataclasses.dataclass(frozen=True)
class Video:
    """A row of the ``video`` table."""

    id: int
    video_id: str
    feed: str
    channel: str | None
    channel_url: str | None
    title: str
    url: str
    thumbnail_url: str | None
    kind: str
    published_text: str | None
    description: str | None
    first_seen_at: str
    transcript_status: str
    transcript_path: str | None
    transcript_chars: int | None
    transcript_lang: str | None
    transcript_error: str | None
    transcript_attempts: int
    summarised_at: str | None
    run_id: int | None
    scope_status: str
    scope_result: str | None
    scope_input_hash: str | None
    scope_checked_at: str | None
    scope_attempts: int
    scope_next_attempt_at: str | None
    scope_error: str | None
    scope_retryable: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Video":
        return cls(
            id=row["id"],
            video_id=row["video_id"],
            feed=row["feed"],
            channel=row["channel"],
            channel_url=row["channel_url"],
            title=row["title"],
            url=row["url"],
            thumbnail_url=row["thumbnail_url"],
            kind=row["kind"],
            published_text=row["published_text"],
            description=row["description"],
            first_seen_at=row["first_seen_at"],
            transcript_status=row["transcript_status"],
            transcript_path=row["transcript_path"],
            transcript_chars=row["transcript_chars"],
            transcript_lang=row["transcript_lang"],
            transcript_error=row["transcript_error"],
            transcript_attempts=row["transcript_attempts"],
            summarised_at=row["summarised_at"],
            run_id=row["run_id"],
            scope_status=row["scope_status"],
            scope_result=row["scope_result"],
            scope_input_hash=row["scope_input_hash"],
            scope_checked_at=row["scope_checked_at"],
            scope_attempts=row["scope_attempts"],
            scope_next_attempt_at=row["scope_next_attempt_at"],
            scope_error=row["scope_error"],
            scope_retryable=row["scope_retryable"],
        )

    def to_dict(self) -> dict:
        """The shape the CLI prints. Deliberately flat -- the agent reads it.

        The transcript is a *path*, never text. That is what keeps a check that
        found five long videos from arriving as two hundred thousand characters
        of context, and it lets the agent open only the ones it is about to
        write about.
        """
        return {
            "id": self.id,
            "video_id": self.video_id,
            "feed": self.feed,
            "channel": self.channel,
            "title": self.title,
            "url": self.url,
            "thumbnail_url": self.thumbnail_url,
            "kind": self.kind,
            "published_text": self.published_text,
            "first_seen_at": self.first_seen_at,
            "summarised_at": self.summarised_at,
            "scope": self.scope_payload(),
            "transcript": {
                "status": self.transcript_status,
                "path": self.transcript_path,
                "chars": self.transcript_chars,
                "language": self.transcript_lang,
                "attempts": self.transcript_attempts,
                "error": self.transcript_error,
            },
        }

    def scope_payload(self) -> dict:
        result = json.loads(self.scope_result) if self.scope_result else {
            "is_target_scope": None, "reason": None, "confidence_score": None,
        }
        return {
            **result, "status": self.scope_status,
            "checked_at": self.scope_checked_at, "attempts": self.scope_attempts,
            "next_attempt_at": self.scope_next_attempt_at, "error": self.scope_error,
            "input_hash": self.scope_input_hash,
            "retryable": bool(self.scope_retryable),
        }
