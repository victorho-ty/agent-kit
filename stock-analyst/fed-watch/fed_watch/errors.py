"""Failure modes, as a closed set of exit codes.

The agent branches on the exit code and on ``error`` in the JSON payload; it
never reads stderr and never pattern-matches a human sentence.

A *per-contract* fetch failure is not one of these when another meeting still
resolved: one unreachable contract must never discard the meeting that did
price, so it is collected into ``failures`` and the snapshot is stored
``partial``. A fatal code means nothing usable came back at all.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_CONFIG = 10        # fomc.json is malformed, or the calendar has run out
    ERR_DB = 11            # the database could not be opened or written
    ERR_FETCH = 20         # no price or no EFFR could be reached
    ERR_INSUFFICIENT = 21  # too few stored snapshots to answer what was asked
    ERR_CHART = 22         # the renderer failed or the output directory is unwritable


class FedWatchError(Exception):
    """Base for everything this package raises on purpose."""

    exit_code: ExitCode = ExitCode.ERR_CONFIG
    error: str = "FedWatchError"

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


class ConfigError(FedWatchError):
    """fomc.json is malformed, or does not reach far enough forward.

    The second is the one that actually happens. The FOMC calendar is published
    about a year ahead and this file is a copy of it, so it expires quietly
    unless somebody notices -- which is why running out is an error with a
    remedy rather than an empty result.
    """

    exit_code = ExitCode.ERR_CONFIG
    error = "ERR_CONFIG"


class DatabaseError(FedWatchError):
    exit_code = ExitCode.ERR_DB
    error = "ERR_DB"


class FetchError(FedWatchError):
    exit_code = ExitCode.ERR_FETCH
    error = "ERR_FETCH"


class InsufficientDataError(FedWatchError):
    """Fewer stored snapshots than the request needs.

    Distinct from ERR_FETCH on purpose: the network worked, the history is
    genuinely short. A newly installed bundle is the usual cause, and the answer
    is to let the snapshots accumulate, not to retry.
    """

    exit_code = ExitCode.ERR_INSUFFICIENT
    error = "ERR_INSUFFICIENT"


class ChartError(FedWatchError):
    exit_code = ExitCode.ERR_CHART
    error = "ERR_CHART"
