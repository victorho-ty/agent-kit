"""Closed exit-code enum. The agent branches on these, never on stderr text."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERR_CONFIG = 10
    ERR_DB = 11
    ERR_ACCOUNT = 12
    ERR_PREDICATE_SCHEMA = 20
    ERR_CANDIDATE_MISMATCH = 21
    ERR_DEDUPE_COLLISION = 22
    ERR_NOT_FOUND = 30
    ERR_ILLEGAL_TRANSITION = 31
    ERR_TELEGRAM = 40
    ERR_PURGE_UNSAFE = 50


class CouponError(Exception):
    """Base for every error the CLI turns into an exit code."""

    exit_code: ExitCode = ExitCode.ERR_CONFIG

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


class ConfigError(CouponError):
    exit_code = ExitCode.ERR_CONFIG


class DatabaseError(CouponError):
    exit_code = ExitCode.ERR_DB


class AccountError(CouponError):
    """Account missing, unknown, duplicate, or a payload/path from another account."""

    exit_code = ExitCode.ERR_ACCOUNT


class PredicateSchemaError(CouponError):
    exit_code = ExitCode.ERR_PREDICATE_SCHEMA


class CandidateMismatchError(CouponError):
    exit_code = ExitCode.ERR_CANDIDATE_MISMATCH


class DedupeCollisionError(CouponError):
    exit_code = ExitCode.ERR_DEDUPE_COLLISION


class NotFoundError(CouponError):
    exit_code = ExitCode.ERR_NOT_FOUND


class IllegalTransitionError(CouponError):
    exit_code = ExitCode.ERR_ILLEGAL_TRANSITION


class TelegramError(CouponError):
    exit_code = ExitCode.ERR_TELEGRAM


class PurgeUnsafeError(CouponError):
    """Anomalies found during purge. Clean cases still proceed."""

    exit_code = ExitCode.ERR_PURGE_UNSAFE
