"""Filter spec -> rows. No LLM call ever sits on this path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import clock, predicates
from .accounts import AccountScope
from .models import Coupon, Predicate
from .predicates import EvalContext


@dataclass(frozen=True)
class FilterSpec:
    """What the agent translates a free-text question into."""

    statuses: tuple[str, ...] = ()
    merchant: str | None = None
    expiring_within_days: int | None = None
    include_expired: bool = False
    limit: int | None = None


@dataclass(frozen=True)
class UsableResult:
    coupon: Coupon
    caveats: list[Predicate] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            **self.coupon.to_dict(),
            "caveats": [predicates.describe(c) for c in self.caveats],
        }


def list_coupons(scope: AccountScope, spec: FilterSpec, now: datetime) -> list[Coupon]:
    sql = ["SELECT * FROM coupon WHERE account_id = ?"]
    params: list[object] = [scope.account_id]

    if spec.statuses:
        placeholders = ", ".join("?" for _ in spec.statuses)
        sql.append(f"AND status IN ({placeholders})")
        params.extend(spec.statuses)
    elif not spec.include_expired:
        sql.append("AND status NOT IN ('expired', 'void')")

    if spec.merchant:
        sql.append("AND merchant LIKE ?")
        params.append(f"%{spec.merchant}%")

    if spec.expiring_within_days is not None:
        horizon = clock.today(now) + timedelta(days=spec.expiring_within_days)
        sql.append("AND expires_on <= ?")
        params.append(clock.iso_date(horizon))

    sql.append("ORDER BY expires_on ASC, merchant ASC")
    if spec.limit:
        sql.append("LIMIT ?")
        params.append(spec.limit)

    rows = scope.conn.execute(" ".join(sql), params).fetchall()
    return [Coupon.from_row(row) for row in rows]


def usable_now(
    scope: AccountScope,
    now: datetime,
    *,
    at: datetime | None = None,
    channel: str | None = None,
    location: str | None = None,
    payment_method: str | None = None,
    spend: float | None = None,
) -> list[UsableResult]:
    """Active, unexpired coupons whose predicates do not explicitly FAIL.

    An UNKNOWN verdict never excludes — a coupon with only a min_spend condition
    is always returned, carrying that condition as a caveat.
    """
    moment = at or now
    today = clock.iso_date(clock.today(moment))
    rows = scope.conn.execute(
        "SELECT * FROM coupon WHERE account_id = ? AND status = 'active'"
        " AND expires_on >= ? ORDER BY expires_on ASC, merchant ASC",
        (scope.account_id, today),
    ).fetchall()

    ctx = EvalContext(
        at=moment,
        channel=channel,
        location=location,
        payment_method=payment_method,
        spend=spend,
    )

    results: list[UsableResult] = []
    for row in rows:
        coupon = Coupon.from_row(row)
        usable, caveats = predicates.evaluate_all(coupon.conditions, ctx)
        if usable:
            results.append(UsableResult(coupon=coupon, caveats=caveats))
    return results


def needs_review(scope: AccountScope) -> list[Coupon]:
    rows = scope.conn.execute(
        "SELECT * FROM coupon WHERE account_id = ? AND status = 'needs_review'"
        " ORDER BY created_at ASC",
        (scope.account_id,),
    ).fetchall()
    return [Coupon.from_row(row) for row in rows]


def counts_by_status(scope: AccountScope) -> dict[str, int]:
    return {
        row["status"]: row["n"]
        for row in scope.conn.execute(
            "SELECT status, COUNT(*) AS n FROM coupon WHERE account_id = ? GROUP BY status",
            (scope.account_id,),
        )
    }
