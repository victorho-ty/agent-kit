"""Failure modes, as a closed set of exit codes.

The agent branches on the exit code and on ``error`` in the JSON payload; it
never reads stderr and never pattern-matches a human sentence.

A *per-feed* failure is not one of these. One unreachable channel must never
abort a check of the other nine, so those are collected into ``feed_failures``
and the run finishes ``partial``. A transcript that cannot be fetched is not one
of these either: the video is still real, still new, and still worth a line --
it just arrives with ``transcript.status`` set to something other than ``ok``.

Adapted from news-radar/news_radar/errors.py; ERR_BROWSER is gone with the
browser renderer, and ERR_TRANSCRIPT takes its number for the one case where a
transcript failure *is* fatal: an explicit single-video request for it.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_CONFIG = 10       # feeds.json is malformed or names something unknown
    ERR_DB = 11           # the database could not be opened or written
    ERR_FETCH = 20        # the only feed asked for could not be reached
    ERR_TRANSCRIPT = 22   # a transcript was asked for by name and could not be produced
    ERR_NOT_FOUND = 30    # no video with that id, no feed with that name


class VideoSummaryError(Exception):
    """Base for everything this package raises on purpose."""

    exit_code: ExitCode = ExitCode.ERR_CONFIG
    error: str = "VideoSummaryError"

    def __init__(self, message: str, **detail):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def payload(self) -> dict:
        return {
            "ok": False,
            "error": self.error,
            "exit_code": int(self.exit_code),
            "message": self.message,
            "detail": self.detail,
        }


class ConfigError(VideoSummaryError):
    """feeds.json is malformed, or names a feed that does not exist."""

    exit_code = ExitCode.ERR_CONFIG
    error = "ERR_CONFIG"


class DatabaseError(VideoSummaryError):
    exit_code = ExitCode.ERR_DB
    error = "ERR_DB"


class FetchError(VideoSummaryError):
    """A feed could not be retrieved. Per-feed during a check; fatal only when
    it is the single feed the caller asked for."""

    exit_code = ExitCode.ERR_FETCH
    error = "ERR_FETCH"


class TranscriptError(VideoSummaryError):
    """No transcript could be produced for a video.

    During a check this is caught and recorded on the video rather than raised:
    a caption-less video is still news. It escapes only from
    ``video-summary transcript --video <id> --refresh``, where the caller asked
    for exactly this one thing.
    """

    exit_code = ExitCode.ERR_TRANSCRIPT
    error = "ERR_TRANSCRIPT"


class NotFoundError(VideoSummaryError):
    exit_code = ExitCode.ERR_NOT_FOUND
    error = "ERR_NOT_FOUND"
