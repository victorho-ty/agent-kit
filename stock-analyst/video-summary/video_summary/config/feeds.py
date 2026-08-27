"""What is watched, and how each channel is treated.

Adapted from news-radar/news_radar/config/sources.py. Three differences worth
knowing:

**There are no categories.** A digest needed sections; this skill sends one
video at a time, in the order they were published, so a taxonomy would be
furniture. What a channel is about is said by its ``name`` and its ``note``,
both of which reach the agent.

**There is no ``kind``.** Every source here is a YouTube feed, which is Atom
with a fixed shape maintained by YouTube. Nothing can be broken by a redesign
and no selectors exist to get wrong.

**There is no schedule.** The cron entry is the cadence -- restating it here
would create a second source of truth that drifts. What cron cannot express is
per-feed politeness, so that is the one knob provided: ``min_interval_minutes``
is a floor on how often one feed may be fetched.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from ..errors import ConfigError

CONFIG_FILE = Path(__file__).parent / "feeds.json"

DEFAULT_DELAY_SECONDS = 2.0
DEFAULT_MAX_ITEMS = 15
# How many videos one `check` will hand over at once. A backlog is drained a
# few at a time rather than arriving as fifteen Telegram notifications, and the
# rest stay pending -- the ledger loses nothing.
DEFAULT_MAX_PER_CHECK = 5
# The cap the agent is told to write to. Telegram's own limit is 4096
# characters; 800 is a deliberate choice about what a person reads standing up,
# not a protocol limit. One message per video carries both the summary and the
# link -- there is no photo send, so nothing here is near a wall.
DEFAULT_SUMMARY_CHAR_CAP = 800
# Captions are generated after upload, not at it. A video whose transcript is
# not ready is held back for this long rather than being sent without one --
# one cron cycle is usually enough. After that it goes out regardless, with
# transcript.status saying why there is nothing to summarise.
DEFAULT_TRANSCRIPT_GRACE_MINUTES = 120
# Transcript attempts per video before it is left alone. A video with captions
# genuinely disabled never grows any, and retrying it every two hours forever
# is a request YouTube did not ask for.
DEFAULT_MAX_TRANSCRIPT_ATTEMPTS = 3
DEFAULT_TRANSCRIPT_LANGUAGES = ("en", "en-US", "en-GB", "zh-Hant", "zh-Hans", "zh")

# A channel feed url carries the channel id in a query parameter; a playlist
# feed carries a playlist id. Either is accepted, and anything else is a config
# error rather than a fetch that mysteriously returns nothing.
FEED_HOST = "www.youtube.com"
FEED_PATH = "/feeds/videos.xml"
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


@dataclasses.dataclass(frozen=True)
class Feed:
    """One channel (or playlist) to watch."""

    name: str
    url: str
    note: str | None = None
    transcript: bool = True
    min_interval_minutes: int = 0
    max_items: int = DEFAULT_MAX_ITEMS
    enabled: bool = True

    def __post_init__(self):
        if not self.name.strip():
            raise ConfigError("a feed needs a name")
        if not self.url.startswith(("http://", "https://")):
            raise ConfigError(f"feed {self.name!r}: url must be http(s), got {self.url!r}")
        parts = urlsplit(self.url)
        if parts.path != FEED_PATH:
            raise ConfigError(
                f"feed {self.name!r}: url must be a YouTube feed "
                f"(https://{FEED_HOST}{FEED_PATH}?channel_id=UC...), got {self.url!r}"
            )
        query = parse_qs(parts.query)
        if not (query.get("channel_id") or query.get("playlist_id") or query.get("user")):
            raise ConfigError(
                f"feed {self.name!r}: url needs a channel_id, playlist_id or user parameter, "
                f"got {self.url!r}"
            )
        channel_id = (query.get("channel_id") or [None])[0]
        if channel_id and not _CHANNEL_ID.match(channel_id):
            raise ConfigError(
                f"feed {self.name!r}: channel_id {channel_id!r} does not look like a channel id "
                "(they start UC and are 24 characters). A handle such as @someone is not one -- "
                "open the channel page and read the id out of its source, or use the ?user= form."
            )
        if self.max_items < 1:
            raise ConfigError(f"feed {self.name!r}: max_items must be >= 1")
        if self.min_interval_minutes < 0:
            raise ConfigError(f"feed {self.name!r}: min_interval_minutes must be >= 0")

    @property
    def channel_id(self) -> str | None:
        return (parse_qs(urlsplit(self.url).query).get("channel_id") or [None])[0]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "channel_id": self.channel_id,
            "note": self.note,
            "transcript": self.transcript,
            "min_interval_minutes": self.min_interval_minutes,
            "max_items": self.max_items,
            "enabled": self.enabled,
        }


@dataclasses.dataclass(frozen=True)
class FeedConfig:
    """The whole config file."""

    timezone_name: str
    feeds: tuple[Feed, ...]
    exclude_keywords: tuple[str, ...]
    request_delay_seconds: float
    max_per_check: int
    summary_char_cap: int
    transcript_languages: tuple[str, ...]
    transcript_grace_minutes: int
    max_transcript_attempts: int
    detect_shorts: bool
    path: Path

    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def select(self, names: list[str] | None = None, include_disabled: bool = False) -> list[Feed]:
        chosen = list(self.feeds)
        if names:
            by_name = {feed.name: feed for feed in chosen}
            unknown = [name for name in names if name not in by_name]
            if unknown:
                raise ConfigError(f"unknown feed(s) {unknown}; {self.path} defines {sorted(by_name)}")
            chosen = [by_name[name] for name in names]
        if not include_disabled:
            chosen = [feed for feed in chosen if feed.enabled]
        return chosen

    def feed(self, name: str) -> Feed | None:
        return next((feed for feed in self.feeds if feed.name == name), None)


# --------------------------------------------------------------------------- loading


def strip_comments(source: str) -> str:
    """Drop whole-line ``//`` comments so the shipped config can carry examples.

    Only lines whose first non-space characters are ``//`` are removed, which is
    what keeps a ``https://`` inside a value safe. Lines are blanked rather than
    deleted so a JSON error still reports the line number the operator sees in
    their editor.
    """
    return "\n".join("" if line.lstrip().startswith("//") else line for line in source.splitlines())


def _load_feed(entry: object) -> Feed:
    if not isinstance(entry, dict):
        raise ConfigError(f"every feed must be a JSON object, got {entry!r}")
    try:
        return Feed(**entry)
    except TypeError as exc:
        raise ConfigError(f"feed {entry.get('name', '<unnamed>')!r}: {exc}") from exc


def load_config(path: Path | str | None = None) -> FeedConfig:
    """Read and validate ``feeds.json``."""
    if path is None:
        from .. import settings

        path = settings.config_path()
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.loads(strip_comments(handle.read()))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: the config must be a JSON object")

    feeds = tuple(_load_feed(entry) for entry in raw.get("feeds", []))
    duplicate = _first_duplicate(feed.name for feed in feeds)
    if duplicate:
        raise ConfigError(f"{path}: duplicate feed name {duplicate!r}")
    duplicate = _first_duplicate(feed.url for feed in feeds)
    if duplicate:
        raise ConfigError(f"{path}: two feeds share the url {duplicate!r}")

    timezone_name = raw.get("timezone")
    if timezone_name is None:
        from .. import settings

        timezone_name = str(settings.timezone())
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:  # ZoneInfoNotFoundError and friends
        raise ConfigError(f"{path}: unknown timezone {timezone_name!r}: {exc}") from exc

    delay = float(raw.get("request_delay_seconds", DEFAULT_DELAY_SECONDS))
    if delay < 0:
        raise ConfigError(f"{path}: request_delay_seconds must be >= 0")

    max_per_check = int(raw.get("max_per_check", DEFAULT_MAX_PER_CHECK))
    if max_per_check < 1:
        raise ConfigError(f"{path}: max_per_check must be >= 1")

    cap = int(raw.get("summary_char_cap", DEFAULT_SUMMARY_CHAR_CAP))
    if not 0 < cap <= 4096:
        raise ConfigError(f"{path}: summary_char_cap must be between 1 and 4096 (Telegram's own limit)")

    languages = tuple(raw.get("transcript_languages", DEFAULT_TRANSCRIPT_LANGUAGES))
    if not languages:
        raise ConfigError(f"{path}: transcript_languages must list at least one language code")

    grace = int(raw.get("transcript_grace_minutes", DEFAULT_TRANSCRIPT_GRACE_MINUTES))
    if grace < 0:
        raise ConfigError(f"{path}: transcript_grace_minutes must be >= 0")

    attempts = int(raw.get("max_transcript_attempts", DEFAULT_MAX_TRANSCRIPT_ATTEMPTS))
    if attempts < 1:
        raise ConfigError(f"{path}: max_transcript_attempts must be >= 1")

    return FeedConfig(
        timezone_name=timezone_name,
        feeds=feeds,
        exclude_keywords=tuple(raw.get("exclude", ())),
        request_delay_seconds=delay,
        max_per_check=max_per_check,
        summary_char_cap=cap,
        transcript_languages=languages,
        transcript_grace_minutes=grace,
        max_transcript_attempts=attempts,
        detect_shorts=bool(raw.get("detect_shorts", True)),
        path=path,
    )


def _first_duplicate(values) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None
