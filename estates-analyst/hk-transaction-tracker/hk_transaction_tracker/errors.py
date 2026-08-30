"""Failure modes, as a closed set of exit codes.

The agent branches on the exit code and on ``error`` in the JSON payload; it
never reads stderr and never pattern-matches a human sentence.

The split that matters here is ERR_FETCH against ERR_PARSE. ERR_FETCH is
Centanet being unreachable -- transient, tomorrow's run retries, not worth a
message. ERR_PARSE means the page came back fine and the embedded payload no
longer looks the way this code expects, which is the failure that would
otherwise present as "no new transactions" forever. One of those is worth
waking somebody for and the other is not.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_CONFIG = 10     # estates.json is missing, malformed, or an env override is unusable
    ERR_DB = 11         # the SQLite file is missing, unreadable or not our schema
    ERR_FETCH = 20      # the listing page could not be retrieved
    ERR_PARSE = 21      # retrieved fine, but the embedded payload is not what we expect
    ERR_RENDER = 22     # an image could not be drawn, or the image directory is unwritable
    ERR_NOT_FOUND = 30  # no such estate, or no data for the bucket asked about


class TrackerError(Exception):
    """Base for everything this package raises on purpose."""

    exit_code: ExitCode = ExitCode.ERR_CONFIG
    error: str = "TrackerError"

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


class ConfigError(TrackerError):
    exit_code = ExitCode.ERR_CONFIG
    error = "ERR_CONFIG"


class DatabaseError(TrackerError):
    exit_code = ExitCode.ERR_DB
    error = "ERR_DB"


class FetchError(TrackerError):
    """Centanet could not be reached. Transient; the next run retries."""

    exit_code = ExitCode.ERR_FETCH
    error = "ERR_FETCH"


class ParseError(TrackerError):
    """The page was retrieved but the embedded payload no longer parses.

    Distinct from ERR_FETCH on purpose. This one is indistinguishable from
    "nothing new was transacted" unless it is reported, and a quiet estate can
    genuinely go a fortnight without a deal -- so a silent parse failure could
    hide for months.
    """

    exit_code = ExitCode.ERR_PARSE
    error = "ERR_PARSE"


class RenderError(TrackerError):
    exit_code = ExitCode.ERR_RENDER
    error = "ERR_RENDER"


class NotFoundError(TrackerError):
    exit_code = ExitCode.ERR_NOT_FOUND
    error = "ERR_NOT_FOUND"
