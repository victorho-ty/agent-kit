"""Closed exit-code enum. The agent branches on these, never on stderr text."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_USAGE = 10
    ERR_CONFIG = 11
    ERR_DB = 12
    ERR_NOT_FOUND = 30
    ERR_NO_ENTRIES = 31


class QuotesDrillError(Exception):
    """Base for every error the CLI turns into an exit code."""

    exit_code: ExitCode = ExitCode.ERR_USAGE

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def payload(self) -> dict:
        return {
            "ok": False,
            "error": type(self).__name__,
            "exit_code": int(self.exit_code),
            "code": self.exit_code.name,
            "message": self.message,
            "details": self.details,
        }


class UsageError(QuotesDrillError):
    exit_code = ExitCode.ERR_USAGE


class ConfigError(QuotesDrillError):
    exit_code = ExitCode.ERR_CONFIG


class DatabaseError(QuotesDrillError):
    exit_code = ExitCode.ERR_DB


class NotFoundError(QuotesDrillError):
    exit_code = ExitCode.ERR_NOT_FOUND


class NoEntriesError(QuotesDrillError):
    """Nothing to drill: the store is empty, or every match is retired.

    Its own code because it is the one failure with an obvious next move --
    ask for material -- rather than a bug to report.
    """

    exit_code = ExitCode.ERR_NO_ENTRIES
