"""Status transitions and the expiry sweep.

Every function takes an ``AccountScope`` and resolves the coupon id within it,
so a foreign id is a plain "not found".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import clock, store
from .accounts import AccountScope
from .errors import IllegalTransitionError
from .models import Coupon


@dataclass(frozen=True)
class TransitionResult:
    coupon_id: str
    previous_status: str
    new_status: str
    uses_remaining: int
    no_op: bool = False

    def to_dict(self) -> dict:
        return {
            "coupon_id": self.coupon_id,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "uses_remaining": self.uses_remaining,
            "no_op": self.no_op,
        }


def mark_used(scope: AccountScope, coupon_id: str, now: datetime, uses: int = 1) -> TransitionResult:
    """Consume uses. Idempotent: marking an already-used coupon is a no-op.

    The Telegram inline button can be double-tapped; that must be harmless.
    """
    coupon = store.get(scope, coupon_id)

    if coupon.status == "used":
        return TransitionResult(coupon.id, "used", "used", coupon.uses_remaining, no_op=True)
    if coupon.status not in ("active", "needs_review"):
        raise IllegalTransitionError(
            f"cannot mark {coupon.status} coupon as used",
            {"id": coupon.id, "status": coupon.status},
        )

    remaining = max(0, coupon.uses_remaining - max(1, uses))
    new_status = "used" if remaining == 0 else "active"
    used_at = clock.iso(now) if new_status == "used" else None

    scope.conn.execute(
        "UPDATE coupon SET uses_remaining = ?, status = ?, used_at = ?, updated_at = ?"
        " WHERE id = ? AND account_id = ?",
        (remaining, new_status, used_at, clock.iso(now), coupon.id, scope.account_id),
    )
    return TransitionResult(coupon.id, coupon.status, new_status, remaining)


def mark_unused(
    scope: AccountScope, coupon_id: str, now: datetime, *, force: bool = False
) -> TransitionResult:
    """Undo a use. Safe, because nothing was deleted."""
    coupon = store.get(scope, coupon_id)
    if coupon.status not in ("used", "active"):
        raise IllegalTransitionError(
            f"cannot un-use a {coupon.status} coupon",
            {"id": coupon.id, "status": coupon.status},
        )

    if coupon.used_at and not force:
        window = timedelta(hours=scope.config.undo_window_hours)
        if clock.to_local(now) - clock.parse_datetime(coupon.used_at) > window:
            raise IllegalTransitionError(
                f"used more than {scope.config.undo_window_hours}h ago; pass --force",
                {"id": coupon.id, "used_at": coupon.used_at},
            )

    scope.conn.execute(
        "UPDATE coupon SET uses_remaining = uses_total, status = 'active', used_at = NULL,"
        " updated_at = ? WHERE id = ? AND account_id = ?",
        (clock.iso(now), coupon.id, scope.account_id),
    )
    return TransitionResult(coupon.id, coupon.status, "active", coupon.uses_total)


def mark_void(
    scope: AccountScope, coupon_id: str, reason: str, now: datetime
) -> TransitionResult:
    """A mis-scan or a worthless coupon."""
    coupon = store.get(scope, coupon_id)
    if coupon.status == "void":
        return TransitionResult(coupon.id, "void", "void", coupon.uses_remaining, no_op=True)

    note = f"voided: {reason}"
    notes = f"{coupon.notes}\n{note}" if coupon.notes else note
    scope.conn.execute(
        "UPDATE coupon SET status = 'void', notes = ?, updated_at = ?"
        " WHERE id = ? AND account_id = ?",
        (notes, clock.iso(now), coupon.id, scope.account_id),
    )
    return TransitionResult(coupon.id, coupon.status, "void", coupon.uses_remaining)


def extend(scope: AccountScope, coupon_id: str, new_date: str, now: datetime) -> TransitionResult:
    """Push the expiry out, revive if expired, and re-arm the alerts."""
    coupon = store.get(scope, coupon_id)
    if coupon.status in ("used", "void"):
        raise IllegalTransitionError(
            f"cannot extend a {coupon.status} coupon",
            {"id": coupon.id, "status": coupon.status},
        )
    clock.parse_date(new_date)

    new_status = "active" if coupon.status == "expired" else coupon.status
    scope.conn.execute(
        "UPDATE coupon SET expires_on = ?, status = ?, expired_at = NULL, updated_at = ?"
        " WHERE id = ? AND account_id = ?",
        (new_date, new_status, clock.iso(now), coupon.id, scope.account_id),
    )
    # Clear the sent-set so alerts fire again against the new date.
    scope.conn.execute(
        "DELETE FROM alerts_sent WHERE coupon_id = ? AND account_id = ?",
        (coupon.id, scope.account_id),
    )
    return TransitionResult(coupon.id, coupon.status, new_status, coupon.uses_remaining)


def resolve_review(
    scope: AccountScope, coupon_id: str, as_status: str, now: datetime
) -> TransitionResult:
    """Take a coupon out of the needs_review queue."""
    if as_status not in ("active", "void"):
        raise IllegalTransitionError(
            f"review resolves to 'active' or 'void', not {as_status!r}",
            {"as": as_status},
        )
    coupon = store.get(scope, coupon_id)
    if coupon.status != "needs_review":
        raise IllegalTransitionError(
            f"coupon is {coupon.status}, not needs_review",
            {"id": coupon.id, "status": coupon.status},
        )
    scope.conn.execute(
        "UPDATE coupon SET status = ?, updated_at = ? WHERE id = ? AND account_id = ?",
        (as_status, clock.iso(now), coupon.id, scope.account_id),
    )
    return TransitionResult(coupon.id, coupon.status, as_status, coupon.uses_remaining)


def sweep_expiry(scope: AccountScope, now: datetime, *, commit: bool = True) -> list[Coupon]:
    """Expire this account's past-dated coupons. Idempotent."""
    today = clock.iso_date(clock.today(now))
    rows = scope.conn.execute(
        "SELECT * FROM coupon WHERE account_id = ? AND expires_on < ?"
        " AND status IN ('active', 'needs_review') ORDER BY expires_on",
        (scope.account_id, today),
    ).fetchall()
    swept = [Coupon.from_row(row) for row in rows]
    if not swept or not commit:
        return swept

    stamp = clock.iso(now)
    scope.conn.executemany(
        "UPDATE coupon SET status = 'expired', expired_at = ?, updated_at = ?"
        " WHERE id = ? AND account_id = ?",
        [(stamp, stamp, c.id, scope.account_id) for c in swept],
    )
    return swept
