"""Which coupons are due for an alert, grouped by the account that owns them.

This module does **not** send anything. Hermes owns the Telegram channel; a cron
entry runs ``couponctl alerts due`` and the agent relays each group to that
group's own ``chat_id``.

There is no sent-ledger. Due-ness is recomputed from ``expires_on`` on every
run, which means:

- a run that never happened (machine off) needs no catch-up logic — the next run
  reports whatever is due at that moment;
- ``extend`` needs no cache invalidation;
- a coupon inside the window is reported once per run until it expires or is
  used, so ``alert_days_before`` is the repeat-rate control. At the default of 1
  that is at most two messages: the day before, and the day itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import accounts, clock, lifecycle, query
from .accounts import AccountScope
from .config import Config
from .models import Coupon

PRE_EXPIRY = "pre_expiry"
EXPIRY_DAY = "expiry_day"


def due_for_account(
    scope: AccountScope, now: datetime, *, commit: bool = False
) -> list[tuple[Coupon, str]]:
    """Active coupons within the alert window, each tagged with its alert kind.

    Sweeps expiry first so a coupon that lapsed overnight is classified as
    expired rather than announced as due today.
    """
    lifecycle.sweep_expiry(scope, now, commit=commit)

    today = clock.today(now)
    horizon = today + timedelta(days=scope.config.alert_days_before)
    rows = scope.conn.execute(
        "SELECT * FROM coupon WHERE account_id = ? AND status = 'active'"
        " AND expires_on >= ? AND expires_on <= ?"
        " ORDER BY expires_on ASC, merchant ASC",
        (scope.account_id, clock.iso_date(today), clock.iso_date(horizon)),
    ).fetchall()

    due: list[tuple[Coupon, str]] = []
    for row in rows:
        coupon = Coupon.from_row(row)
        kind = EXPIRY_DAY if coupon.expires_on == clock.iso_date(today) else PRE_EXPIRY
        due.append((coupon, kind))
    return due


def run(config: Config, conn, now: datetime, *, commit: bool = False,
        account_id: str | None = None) -> dict:
    """Every account's due coupons, grouped so a group cannot be misaddressed.

    The payload has no flat coupon list on purpose: the only way to read a
    coupon out of it is through the group that carries the ``chat_id`` it
    belongs to. One account failing does not stop the rest.
    """
    if account_id:
        targets = [accounts.get(conn, account_id)]
        targets = [t for t in targets if t is not None]
    else:
        targets = accounts.list_accounts(conn)

    groups: list[dict] = []
    failures: list[dict] = []
    skipped: list[dict] = []

    for account in targets:
        try:
            scope = accounts.open_scope(conn, config, account.id)
            due = due_for_account(scope, now, commit=commit)
        except Exception as exc:  # one bad account must not silence the others
            failures.append(
                {"account_id": account.id, "display_name": account.display_name,
                 "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        if not due:
            continue
        if not account.chat_id:
            # A warning, not an error: the account is fine, we just can't reach it.
            skipped.append(
                {"account_id": account.id, "display_name": account.display_name,
                 "due": len(due), "reason": "account has no chat_id"}
            )
            continue

        groups.append(
            {
                "account_id": account.id,
                "display_name": account.display_name,
                "chat_id": account.chat_id,
                "coupons": [
                    {
                        **coupon.to_dict(),
                        "alert_kind": kind,
                        "days_left": clock.days_between(
                            clock.today(now), clock.parse_date(coupon.expires_on)
                        ),
                    }
                    for coupon, kind in due
                ],
            }
        )

    return {
        "ran_at": clock.iso(now),
        "dry_run": not commit,
        "alert_days_before": config.alert_days_before,
        "groups": groups,
        "skipped": skipped,
        "failures": failures,
        "totals": {
            "accounts_with_alerts": len(groups),
            "coupons": sum(len(g["coupons"]) for g in groups),
            "skipped": len(skipped),
            "failed": len(failures),
        },
    }


def format_group(group: dict) -> str:
    """A ready-to-send message body. The agent may reword it; the facts are here."""
    lines = []
    for coupon in group["coupons"]:
        days = coupon["days_left"]
        when = "expires TODAY" if days == 0 else f"expires tomorrow" if days == 1 else f"{days}d left"
        value = f" ({coupon['value_text']})" if coupon.get("value_text") else ""
        lines.append(f"• {coupon['merchant']} — {coupon['title']}{value} — {when}")
        if coupon.get("conditions"):
            from .predicates import describe
            from .models import Predicate

            caveats = ", ".join(
                describe(Predicate(c["kind"], c.get("params"), c.get("text")))
                for c in coupon["conditions"]
            )
            lines.append(f"    ⚠ {caveats}")
    header = "Coupon" if len(group["coupons"]) == 1 else f"{len(group['coupons'])} coupons"
    return f"{header} expiring soon:\n" + "\n".join(lines)
