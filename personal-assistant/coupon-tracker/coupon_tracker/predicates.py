"""The closed predicate enum, its validation and its evaluation.

Adding a kind later is one entry in ``KINDS`` plus one evaluator — no migration,
and no ad-hoc columns. An invented kind fails validation loudly rather than
passing through as something the query path silently ignores.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any, Callable

from . import clock
from .errors import PredicateSchemaError
from .models import Predicate

CHANNELS = ("dine_in", "takeaway", "delivery")

KINDS = (
    "channel",
    "time_window",
    "date_window",
    "location",
    "min_spend",
    "payment_method",
    "other",
)


class Verdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvalContext:
    """What the caller knows about the moment of use. Anything absent is UNKNOWN."""

    at: datetime
    channel: str | None = None
    location: str | None = None
    payment_method: str | None = None
    spend: float | None = None


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate(conditions: list[dict] | None) -> list[Predicate]:
    """Turn raw JSON conditions into Predicates, or raise PredicateSchemaError."""
    if conditions is None:
        return []
    if not isinstance(conditions, list):
        raise PredicateSchemaError("conditions must be a JSON array")

    validated: list[Predicate] = []
    for index, raw in enumerate(conditions):
        if not isinstance(raw, dict):
            raise PredicateSchemaError(f"condition {index} must be an object")
        kind = raw.get("kind")
        if kind not in KINDS:
            raise PredicateSchemaError(
                f"condition {index}: unknown kind {kind!r}",
                {"index": index, "kind": kind, "allowed": list(KINDS)},
            )
        params = raw.get("params")
        if params is not None and not isinstance(params, dict):
            raise PredicateSchemaError(f"condition {index}: params must be an object or null")
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise PredicateSchemaError(f"condition {index}: text must be a string or null")

        _VALIDATORS[kind](index, params or {})
        validated.append(Predicate(kind=kind, params=params, text=text))
    return validated


def _v_channel(index: int, params: dict) -> None:
    allow = params.get("allow")
    if not isinstance(allow, list) or not allow:
        raise PredicateSchemaError(f"condition {index}: channel.allow must be a non-empty list")
    unknown = [c for c in allow if c not in CHANNELS]
    if unknown:
        raise PredicateSchemaError(
            f"condition {index}: unknown channels {unknown}",
            {"allowed": list(CHANNELS)},
        )


def _v_time_window(index: int, params: dict) -> None:
    days = params.get("days")
    if days is not None:
        if not isinstance(days, list) or not all(
            isinstance(d, int) and 0 <= d <= 6 for d in days
        ):
            raise PredicateSchemaError(
                f"condition {index}: time_window.days must be integers 0..6 (Mon0..Sun6)"
            )
    for key in ("from", "to"):
        value = params.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not _parse_hhmm(value):
            raise PredicateSchemaError(
                f"condition {index}: time_window.{key} must be 'HH:MM', got {value!r}"
            )


def _v_date_window(index: int, params: dict) -> None:
    for key in ("from", "to"):
        value = params.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise PredicateSchemaError(f"condition {index}: date_window.{key} must be a string")
        try:
            clock.parse_date(value)
        except ValueError as exc:
            raise PredicateSchemaError(
                f"condition {index}: date_window.{key} must be YYYY-MM-DD, got {value!r}"
            ) from exc


def _v_location(index: int, params: dict) -> None:
    branches = params.get("branches")
    if branches is not None and (
        not isinstance(branches, list) or not all(isinstance(b, str) for b in branches)
    ):
        raise PredicateSchemaError(f"condition {index}: location.branches must be a list of strings")
    region = params.get("region")
    if region is not None and not isinstance(region, str):
        raise PredicateSchemaError(f"condition {index}: location.region must be a string")


def _v_min_spend(index: int, params: dict) -> None:
    amount = params.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise PredicateSchemaError(f"condition {index}: min_spend.amount must be a number")
    ccy = params.get("ccy")
    if ccy is not None and not isinstance(ccy, str):
        raise PredicateSchemaError(f"condition {index}: min_spend.ccy must be a string")


def _v_payment_method(index: int, params: dict) -> None:
    methods = params.get("methods")
    if not isinstance(methods, list) or not methods:
        raise PredicateSchemaError(
            f"condition {index}: payment_method.methods must be a non-empty list"
        )
    if not all(isinstance(m, str) for m in methods):
        raise PredicateSchemaError(f"condition {index}: payment_method.methods must be strings")


def _v_other(index: int, params: dict) -> None:
    return None


_VALIDATORS: dict[str, Callable[[int, dict], None]] = {
    "channel": _v_channel,
    "time_window": _v_time_window,
    "date_window": _v_date_window,
    "location": _v_location,
    "min_spend": _v_min_spend,
    "payment_method": _v_payment_method,
    "other": _v_other,
}


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def evaluate(predicate: Predicate, ctx: EvalContext) -> Verdict:
    evaluator = _EVALUATORS.get(predicate.kind)
    if evaluator is None:
        return Verdict.UNKNOWN
    return evaluator(predicate.params or {}, ctx)


def evaluate_all(
    predicates: list[Predicate], ctx: EvalContext
) -> tuple[bool, list[Predicate]]:
    """Return (usable_now, caveats).

    UNKNOWN never excludes — it surfaces as a caveat. Only an explicit FAIL
    (wrong day, outside the time or date window, wrong branch) excludes.
    """
    caveats: list[Predicate] = []
    usable = True
    for predicate in predicates:
        verdict = evaluate(predicate, ctx)
        if verdict is Verdict.FAIL:
            usable = False
            caveats.append(predicate)
        elif verdict is Verdict.UNKNOWN:
            caveats.append(predicate)
    return usable, caveats


def _e_channel(params: dict, ctx: EvalContext) -> Verdict:
    if ctx.channel is None:
        return Verdict.UNKNOWN
    return Verdict.PASS if ctx.channel in (params.get("allow") or []) else Verdict.FAIL


def _e_time_window(params: dict, ctx: EvalContext) -> Verdict:
    local = clock.to_local(ctx.at)
    days = params.get("days")
    if days and local.weekday() not in days:
        return Verdict.FAIL

    start = _parse_hhmm(params.get("from") or "00:00")
    end = _parse_hhmm(params.get("to") or "23:59")
    if start is None or end is None:
        return Verdict.UNKNOWN

    current = local.time()
    if start <= end:
        return Verdict.PASS if start <= current <= end else Verdict.FAIL
    # Window wraps past midnight, e.g. 22:00 -> 02:00.
    return Verdict.PASS if current >= start or current <= end else Verdict.FAIL


def _e_date_window(params: dict, ctx: EvalContext) -> Verdict:
    today = clock.today(ctx.at)
    start = _maybe_date(params.get("from"))
    end = _maybe_date(params.get("to"))
    if start and today < start:
        return Verdict.FAIL
    if end and today > end:
        return Verdict.FAIL
    return Verdict.PASS


def _e_location(params: dict, ctx: EvalContext) -> Verdict:
    """String match only, as documented — no geocoding, no branch table."""
    if ctx.location is None:
        return Verdict.UNKNOWN
    needle = ctx.location.strip().casefold()
    if not needle:
        return Verdict.UNKNOWN

    haystack = [str(b) for b in (params.get("branches") or [])]
    region = params.get("region")
    if region:
        haystack.append(str(region))
    if not haystack:
        return Verdict.UNKNOWN

    for candidate in haystack:
        folded = candidate.casefold()
        if needle in folded or folded in needle:
            return Verdict.PASS
    return Verdict.FAIL


def _e_advisory(params: dict, ctx: EvalContext) -> Verdict:
    """min_spend and payment_method are never evaluated — always a caveat."""
    return Verdict.UNKNOWN


_EVALUATORS: dict[str, Callable[[dict, EvalContext], Verdict]] = {
    "channel": _e_channel,
    "time_window": _e_time_window,
    "date_window": _e_date_window,
    "location": _e_location,
    "min_spend": _e_advisory,
    "payment_method": _e_advisory,
    "other": _e_advisory,
}


def describe(predicate: Predicate) -> str:
    """Human-readable caveat text; falls back to the original source text."""
    if predicate.text:
        return predicate.text
    params = predicate.params or {}
    match predicate.kind:
        case "channel":
            return "only " + ", ".join(params.get("allow") or [])
        case "time_window":
            return f"{params.get('from', '00:00')}–{params.get('to', '23:59')}"
        case "date_window":
            return f"{params.get('from', '…')} to {params.get('to', '…')}"
        case "location":
            return ", ".join(params.get("branches") or []) or str(params.get("region") or "")
        case "min_spend":
            return f"min spend {params.get('ccy', 'HKD')} {params.get('amount')}"
        case "payment_method":
            return "pay with " + ", ".join(params.get("methods") or [])
    return predicate.kind


def _parse_hhmm(value: str) -> time | None:
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def _maybe_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return clock.parse_date(str(value))
    except ValueError:
        return None
