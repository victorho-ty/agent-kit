"""Failure modes, as a closed set of exit codes.

The agent branches on the exit code and on ``error`` in the JSON payload; it
never reads stderr and never pattern-matches a human sentence.

A *per-ticker* failure is not one of these. One unreachable quote must never
abort a scan of the other nine, so those are collected into ``ticker_failures``
and the run finishes ``partial``. The same rule the other bundles use: a fatal
code means the caller asked for exactly one thing and that thing failed.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_CONFIG = 10        # watchlist.json is malformed or names something unknown
    ERR_DB = 11            # the database could not be opened or written
    ERR_FETCH = 20         # the only requested ticker could not be reached
    ERR_INSUFFICIENT = 21  # too few bars to compute what was asked for
    ERR_CHART = 22         # the renderer failed or the output directory is unwritable
    ERR_NOT_FOUND = 30     # no such ticker, position or run


class DeskError(Exception):
    """Base for everything this package raises on purpose."""

    exit_code: ExitCode = ExitCode.ERR_CONFIG
    error: str = "DeskError"

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


class ConfigError(DeskError):
    """watchlist.json is malformed, or names a ticker or field that does not exist."""

    exit_code = ExitCode.ERR_CONFIG
    error = "ERR_CONFIG"


class DatabaseError(DeskError):
    exit_code = ExitCode.ERR_DB
    error = "ERR_DB"


class FetchError(DeskError):
    """A provider could not be reached, or returned nothing usable.

    Per-ticker during a sync; fatal only when it is the single ticker the caller
    asked for.
    """

    exit_code = ExitCode.ERR_FETCH
    error = "ERR_FETCH"


class InsufficientDataError(DeskError):
    """Fewer bars than the requested computation needs.

    Distinct from ERR_FETCH on purpose: the network worked, the history is
    genuinely short. A newly listed ticker is the usual cause, and the answer is
    to wait, not to retry.
    """

    exit_code = ExitCode.ERR_INSUFFICIENT
    error = "ERR_INSUFFICIENT"


class ChartError(DeskError):
    exit_code = ExitCode.ERR_CHART
    error = "ERR_CHART"


class NotFoundError(DeskError):
    exit_code = ExitCode.ERR_NOT_FOUND
    error = "ERR_NOT_FOUND"
