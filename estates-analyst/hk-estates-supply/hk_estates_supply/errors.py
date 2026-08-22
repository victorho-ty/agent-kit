"""Failure modes, as a closed set of exit codes.

The agent branches on the exit code and on ``error`` in the JSON payload; it
never reads stderr and never pattern-matches a human sentence.

The split that matters is ERR_FETCH against ERR_PARSE. ERR_FETCH means the
Housing Bureau could not be reached -- transient, retry tomorrow, say nothing.
ERR_PARSE means the page or the PDF was reached and no longer looks the way this
code expects, which is the failure that would otherwise present as "no new
quarter" forever. One of those is worth waking somebody for and the other is not.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_CONFIG = 10     # a path or an environment override points somewhere unusable
    ERR_HISTORY = 11    # the history CSV is missing, empty or malformed
    ERR_FETCH = 20      # the index page or the PDF could not be retrieved
    ERR_PARSE = 21      # retrieved fine, but the expected link or figure is not there
    ERR_RENDER = 22     # an image could not be drawn, or the image directory is unwritable
    ERR_NOT_FOUND = 30  # no such quarter in the history


class SupplyError(Exception):
    """Base for everything this package raises on purpose."""

    exit_code: ExitCode = ExitCode.ERR_CONFIG
    error: str = "SupplyError"

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


class ConfigError(SupplyError):
    exit_code = ExitCode.ERR_CONFIG
    error = "ERR_CONFIG"


class HistoryError(SupplyError):
    """The history CSV is missing, empty, or a row does not have five columns."""

    exit_code = ExitCode.ERR_HISTORY
    error = "ERR_HISTORY"


class FetchError(SupplyError):
    """The Housing Bureau could not be reached. Transient; tomorrow's run retries."""

    exit_code = ExitCode.ERR_FETCH
    error = "ERR_FETCH"


class ParseError(SupplyError):
    """The page or the PDF was retrieved but no longer contains what is expected.

    Distinct from ERR_FETCH on purpose. A parse failure is indistinguishable from
    "nothing new was published" unless it is reported, and this monitor is quiet
    for three months at a time -- so a silent parse failure could hide for a year.
    """

    exit_code = ExitCode.ERR_PARSE
    error = "ERR_PARSE"


class RenderError(SupplyError):
    exit_code = ExitCode.ERR_RENDER
    error = "ERR_RENDER"


class NotFoundError(SupplyError):
    exit_code = ExitCode.ERR_NOT_FOUND
    error = "ERR_NOT_FOUND"
