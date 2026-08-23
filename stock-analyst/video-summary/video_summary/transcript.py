"""Getting what was actually said, and putting it on disk.

The agent's ``youtube-content`` skill does this job for a model. A skill is
instructions, though, not something a subprocess can call, so the deterministic
path is a library: ``youtube-transcript-api`` reads YouTube's own caption
tracks, which is the same material by a different door.

Three decisions worth stating:

**A manual track beats an automatic one, and language order is the operator's.**
Auto-generated captions mangle exactly the words this desk cares about --
tickers, basis points, "the two-year" -- so a human-written track in a later
language is preferred over a machine one in the first. Within manual, and within
automatic, ``transcript_languages`` decides.

**The text goes to a file, not into the payload.** Forty minutes of speech is
forty thousand characters. The check hands the agent a path; the agent opens
only what it is about to write about. This is the single decision that keeps a
busy two-hourly wake-up affordable.

**A missing transcript is not an error.** Captions are generated some minutes
after an upload, and some channels disable them outright. Either way the video
is real and worth a line; the status says which case it is and the agent writes
accordingly. Only an explicit, single-video request for a transcript is allowed
to fail loudly.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from . import settings
from .errors import TranscriptError

_WHITESPACE = re.compile(r"\s+")

# Statuses that mean "YouTube has nothing for us", as opposed to "the attempt
# broke". The distinction decides whether retrying is worth anything.
_UNAVAILABLE_EXCEPTIONS = (
    "TranscriptsDisabled",
    "NoTranscriptFound",
    "NoTranscriptAvailable",
    "VideoUnavailable",
    "VideoUnplayable",
    "AgeRestricted",
)


@dataclasses.dataclass(frozen=True)
class TranscriptResult:
    """What one attempt came to."""

    status: str                       # ok | unavailable | error
    path: str | None = None
    chars: int | None = None
    language: str | None = None
    generated: bool | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def path_for(video_id: str, directory: Path | None = None) -> Path:
    directory = directory or settings.transcript_dir()
    return Path(directory) / f"{video_id}.txt"


def _flatten(snippets) -> str:
    """Caption cues into readable prose.

    Cues are three-second fragments with their own line breaks, and a
    line-per-cue file reads as a poem. Whitespace is collapsed and the text is
    rewrapped so the agent reads sentences.
    """
    parts = []
    for snippet in snippets:
        text = getattr(snippet, "text", None)
        if text is None and isinstance(snippet, dict):
            text = snippet.get("text")
        if text:
            parts.append(_WHITESPACE.sub(" ", str(text)).strip())
    return " ".join(part for part in parts if part)


def _client():
    """A transcript client, proxied if the host needs one.

    1.x is instance-based; 0.6.x was a set of classmethods. Supporting both is a
    few lines here and saves pinning an exact version on a host we do not
    control.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    proxy = settings.proxy_url()
    if proxy:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig

            return YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy)
            )
        except (ImportError, TypeError):
            # An older library with no proxy support. Better to try unproxied
            # and report the block than to fail before asking.
            pass
    return YouTubeTranscriptApi()


def _list_transcripts(video_id: str):
    from youtube_transcript_api import YouTubeTranscriptApi

    if not settings.proxy_url() and hasattr(YouTubeTranscriptApi, "list_transcripts"):
        return YouTubeTranscriptApi.list_transcripts(video_id)
    return _client().list(video_id)


def _choose(listing, languages: tuple[str, ...] | list[str]):
    """Manual first, then automatic, in the operator's language order."""
    languages = list(languages)
    for finder in ("find_manually_created_transcript", "find_generated_transcript"):
        try:
            return getattr(listing, finder)(languages)
        except Exception:  # noqa: BLE001 -- NoTranscriptFound and its friends
            continue
    # Last resort: any track at all, translated if the library offers it. A
    # French-only track on a rates channel is still better than silence.
    for track in listing:
        return track
    return None


def fetch(
    video_id: str,
    *,
    languages: tuple[str, ...] | list[str],
    directory: Path | None = None,
    title: str | None = None,
    url: str | None = None,
) -> TranscriptResult:
    """Fetch, flatten and store one transcript. Never raises."""
    try:
        listing = _list_transcripts(video_id)
        track = _choose(listing, languages)
        if track is None:
            return TranscriptResult(status="unavailable", error="no caption track offered")
        fetched = track.fetch()
        text = _flatten(fetched)
    except Exception as exc:  # noqa: BLE001 -- the library raises a wide family
        name = type(exc).__name__
        status = "unavailable" if name in _UNAVAILABLE_EXCEPTIONS else "error"
        # The library's messages run to several paragraphs of advice. The agent
        # needs the class name and a clause, not an essay it will relay.
        detail = _WHITESPACE.sub(" ", f"{name}: {exc}").strip()
        return TranscriptResult(status=status, error=detail[:200])

    if not text:
        return TranscriptResult(status="unavailable", error="caption track was empty")

    destination = path_for(video_id, directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    language = getattr(track, "language_code", None) or getattr(track, "language", None)
    generated = bool(getattr(track, "is_generated", False))
    header = "\n".join(
        line
        for line in (
            f"# {title}" if title else None,
            f"# {url}" if url else None,
            f"# video_id: {video_id}",
            f"# captions: {language}{' (auto-generated)' if generated else ''}",
            "",
        )
        if line is not None
    )
    destination.write_text(header + text + "\n", encoding="utf-8")

    return TranscriptResult(
        status="ok",
        path=str(destination),
        chars=len(text),
        language=language,
        generated=generated,
    )


def fetch_or_raise(video_id: str, **kwargs) -> TranscriptResult:
    """:func:`fetch`, but a failure is fatal.

    For ``video-summary transcript --video <id> --refresh``, where the caller
    asked for exactly this one thing and a quiet ``"status": "unavailable"``
    buried in a payload would be the wrong answer.
    """
    result = fetch(video_id, **kwargs)
    if not result.ok:
        raise TranscriptError(
            f"no transcript for {video_id}: {result.error or result.status}",
            video_id=video_id,
            status=result.status,
        )
    return result
