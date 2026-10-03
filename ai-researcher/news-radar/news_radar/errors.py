"""Failure modes, as a closed set of exit codes.

The agent branches on the exit code and on ``error`` in the JSON payload; it
never reads stderr and never pattern-matches a human sentence.

A *per-source* failure is not one of these. One unreachable feed must never
abort a scan of the other ten, so those are collected into ``source_failures``
and the run finishes ``partial``.

Adapted from education-radar/education_radar/errors.py; ERR_ILLEGAL_VERDICT is
gone with the review queue, which this skill has no equivalent of.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_CONFIG = 10      # sources.json is malformed or names something unknown
    ERR_DB = 11          # the database could not be opened or written
    ERR_FETCH = 20       # the only requested source could not be reached
    ERR_BROWSER = 21     # a source needs "render": "browser" and Playwright is not usable
    ERR_NOT_FOUND = 30   # no item with that id, no source with that name


class RadarError(Exception):
    """Base for everything this package raises on purpose."""

    exit_code: ExitCode = ExitCode.ERR_CONFIG
    error: str = "RadarError"

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


class ConfigError(RadarError):
    """sources.json is malformed, or names a source or category that does not exist."""

    exit_code = ExitCode.ERR_CONFIG
    error = "ERR_CONFIG"


class DatabaseError(RadarError):
    exit_code = ExitCode.ERR_DB
    error = "ERR_DB"


class FetchError(RadarError):
    """A page could not be retrieved. Per-source during a scan; fatal only when
    it is the single source the caller asked for."""

    exit_code = ExitCode.ERR_FETCH
    error = "ERR_FETCH"


class BrowserError(RadarError):
    """A source is marked ``"render": "browser"`` and Chromium is not available.

    Almost always means ``playwright install chromium`` was never run in this
    venv -- the Python package alone does not ship the browser.
    """

    exit_code = ExitCode.ERR_BROWSER
    error = "ERR_BROWSER"


class NotFoundError(RadarError):
    exit_code = ExitCode.ERR_NOT_FOUND
    error = "ERR_NOT_FOUND"
