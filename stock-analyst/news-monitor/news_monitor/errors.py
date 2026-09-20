"""Failure modes, as a closed set of exit codes.

The agent branches on the exit code and on ``error`` in the JSON payload; it
never reads stderr and never pattern-matches a human sentence.

A *per-feed* failure is not one of these. One unreachable publisher must never
abort a check of the other twenty, so those are collected into ``feed_failures``
and the run finishes ``partial``.

``ERR_CANDIDATE`` is the one this bundle adds over its siblings: a url offered
to ``add`` that is not a feed at all. It is fatal because the caller asked for
exactly that url and got nothing -- unlike a candidate that *is* a feed but
fails the quality gate, which is stored disabled and is a success.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_CONFIG = 10     # seeds.json or taxonomy.json is malformed
    ERR_DB = 11         # the database could not be opened or written
    ERR_FETCH = 20      # the only feed asked for could not be reached
    ERR_CANDIDATE = 21  # a url offered to `add` is not a usable feed
    ERR_NOT_FOUND = 30  # no feed with that name, no item with that id


class NewsMonitorError(Exception):
    """Base for everything this package raises on purpose."""

    exit_code: ExitCode = ExitCode.ERR_CONFIG
    error: str = "NewsMonitorError"

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


class ConfigError(NewsMonitorError):
    exit_code = ExitCode.ERR_CONFIG
    error = "ERR_CONFIG"


class DatabaseError(NewsMonitorError):
    exit_code = ExitCode.ERR_DB
    error = "ERR_DB"


class FetchError(NewsMonitorError):
    """A document could not be retrieved, or was not parseable as a feed.

    Per-feed during a check; fatal only when it is the single feed the caller
    named.
    """

    exit_code = ExitCode.ERR_FETCH
    error = "ERR_FETCH"


class CandidateError(NewsMonitorError):
    """A url offered to ``add`` is unreachable, not XML, or already tracked."""

    exit_code = ExitCode.ERR_CANDIDATE
    error = "ERR_CANDIDATE"


class NotFoundError(NewsMonitorError):
    exit_code = ExitCode.ERR_NOT_FOUND
    error = "ERR_NOT_FOUND"
