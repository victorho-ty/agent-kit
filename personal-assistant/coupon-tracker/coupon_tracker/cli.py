"""``couponctl`` — the only way the agent talks to the store.

Every command supports ``--json``, and every JSON payload echoes the account it
acted on so the caller can assert it got the account it asked for.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import (
    accounts,
    alerts,
    clock,
    config as config_mod,
    db,
    lifecycle,
    purge,
    query,
    store,
)
from .accounts import AccountScope
from .errors import AccountError, CouponError, ExitCode, NotFoundError
from .models import Coupon

ACCOUNT_ENV = "COUPONCTL_ACCOUNT"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return int(ExitCode.OK)

    try:
        return args.handler(args)
    except CouponError as exc:
        _fail(args, exc)
        return int(exc.exit_code)
    except ValueError as exc:
        _fail(args, CouponError(str(exc)))
        return int(ExitCode.ERR_CONFIG)


def _fail(args, exc: CouponError) -> None:
    if getattr(args, "json", False):
        print(json.dumps(exc.payload(), indent=2, ensure_ascii=False))
    else:
        print(f"error [{exc.exit_code.name}]: {exc.message}", file=sys.stderr)


def _emit(args, payload: dict, text: str | None = None) -> int:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif text is not None:
        print(text)
    return int(ExitCode.OK)


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #


def _load_config(args) -> config_mod.Config:
    return config_mod.load(getattr(args, "config", None))


def _open(args):
    cfg = _load_config(args)
    conn = db.open_migrated(cfg.db_path)
    return cfg, conn


def _now(args, cfg: config_mod.Config) -> datetime:
    override = getattr(args, "now", None)
    if override:
        return clock.parse_datetime(override, cfg.timezone)
    return clock.now(cfg.timezone)


def _scope(args) -> tuple[config_mod.Config, AccountScope, datetime]:
    """Resolve the account. Flag > env > error; never an implicit default."""
    cfg, conn = _open(args)
    now = _now(args, cfg)

    telegram_user = getattr(args, "telegram_user", None)
    if telegram_user:
        return cfg, accounts.open_scope_for_telegram(conn, cfg, telegram_user), now

    account_id = getattr(args, "account", None) or os.environ.get(ACCOUNT_ENV)
    if not account_id:
        raise AccountError(
            f"no account selected. Pass --account, --telegram-user, or set ${ACCOUNT_ENV}."
        )
    return cfg, accounts.open_scope(conn, cfg, account_id), now


# --------------------------------------------------------------------------- #
# Setup commands
# --------------------------------------------------------------------------- #


def cmd_init(args) -> int:
    root = Path(args.root or Path.cwd()).expanduser().resolve()
    config_path = config_mod.write_default(root)
    cfg = config_mod.load(config_path)
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    applied = db.migrate(conn)
    return _emit(
        args,
        {
            "ok": True,
            "root": str(cfg.root),
            "config": str(config_path),
            "db": str(cfg.db_path),
            "migrations_applied": applied,
        },
        f"initialised {cfg.root}\n  config: {config_path}\n  db: {cfg.db_path}",
    )


def cmd_migrate(args) -> int:
    cfg = _load_config(args)
    conn = db.connect(cfg.db_path)
    applied = db.migrate(conn)
    return _emit(
        args,
        {"ok": True, "applied": applied, "all": db.applied_migrations(conn)},
        f"applied {len(applied)} migration(s)",
    )


def cmd_doctor(args) -> int:
    cfg, conn = _open(args)
    problems: list[dict] = []

    # Unscoped invariants: no row may reference a missing account.
    for table in ("coupon", "media", "inbox_item"):
        orphans = conn.execute(
            f"SELECT COUNT(*) FROM {table} t"
            " LEFT JOIN account a ON a.id = t.account_id WHERE a.id IS NULL"
        ).fetchone()[0]
        if orphans:
            problems.append(
                {"kind": "orphan_rows", "table": table, "count": orphans,
                 "detail": "rows whose account_id names no account"}
            )

    per_account = []
    for account in accounts.list_accounts(conn):
        report = purge.doctor(accounts.open_scope(conn, cfg, account.id))
        per_account.append(report)
        problems.extend(report["problems"])

    # A media file must live under its owner's directory, and nowhere else.
    known_dirs = {a.id for a in accounts.list_accounts(conn)}
    if cfg.media_dir.is_dir():
        for entry in sorted(cfg.media_dir.iterdir()):
            if entry.is_dir() and entry.name not in known_dirs:
                problems.append(
                    {"kind": "stray_media_dir", "path": str(entry),
                     "detail": "media directory for no known account"}
                )

    clean = not problems
    payload = {
        "ok": clean,
        "clean": clean,
        "migrations": db.applied_migrations(conn),
        "accounts": per_account,
        "problems": problems,
    }
    text = "clean" if clean else f"{len(problems)} problem(s) found"
    _emit(args, payload, text)
    return int(ExitCode.OK if clean else ExitCode.ERR_DB)


# --------------------------------------------------------------------------- #
# Account commands
# --------------------------------------------------------------------------- #


def cmd_account_add(args) -> int:
    cfg, conn = _open(args)
    now = _now(args, cfg)
    account = accounts.create(
        conn,
        cfg,
        args.name,
        now,
        telegram_user_id=args.telegram_user,
        chat_id=args.chat_id,
    )
    return _emit(
        args,
        {"ok": True, **account.to_dict()},
        f"created account {account.id} ({account.display_name})",
    )


def cmd_account_list(args) -> int:
    cfg, conn = _open(args)
    rows = []
    for account in accounts.list_accounts(conn):
        counts = query.counts_by_status(accounts.open_scope(conn, cfg, account.id))
        rows.append({**account.to_dict(), "coupons": counts})
    text = "\n".join(
        f"{r['id']}  {r['display_name']:<20} tg={r['telegram_user_id'] or '-':<12}"
        f" coupons={sum(r['coupons'].values())}"
        for r in rows
    ) or "no accounts yet"
    return _emit(args, {"ok": True, "accounts": rows}, text)


def cmd_account_show(args) -> int:
    cfg, scope, _ = _scope(args)
    payload = accounts.summary(scope.conn, scope.account_id)
    text = (
        f"{payload['account']['display_name']} ({payload['account']['id']})\n"
        f"  telegram: {payload['account']['telegram_user_id'] or '-'}\n"
        f"  coupons: {payload['coupons']['total']} {payload['coupons']['by_status']}\n"
        f"  media: {payload['media']['count']} files, {payload['media']['bytes']} bytes\n"
        f"  inbox queued: {payload['inbox']['queued']}"
    )
    return _emit(args, {"ok": True, **payload}, text)


def cmd_account_set(args) -> int:
    cfg, conn = _open(args)
    now = _now(args, cfg)
    account_id = args.account or os.environ.get(ACCOUNT_ENV)
    if not account_id:
        raise AccountError(f"no account selected. Pass --account or set ${ACCOUNT_ENV}.")
    account = accounts.update(
        conn,
        account_id,
        now,
        display_name=args.name,
        telegram_user_id=args.telegram_user,
        chat_id=args.chat_id,
    )
    return _emit(args, {"ok": True, **account.to_dict()}, f"updated account {account.id}")


def cmd_account_delete(args) -> int:
    cfg, scope, now = _scope(args)
    manifest = accounts.delete(
        scope.conn, cfg, scope.account_id, now, commit=args.commit
    )
    mode = "deleted" if args.commit else "would delete"
    text = (
        f"{mode} account {manifest['account']['id']} ({manifest['account']['display_name']})\n"
        f"  coupons: {manifest['coupons']['total']}\n"
        f"  files: {manifest['totals']['files']} ({manifest['totals']['bytes_freed']} bytes)"
    )
    return _emit(args, {"ok": True, **manifest}, text)


# --------------------------------------------------------------------------- #
# Coupon commands
# --------------------------------------------------------------------------- #


def cmd_add(args) -> int:
    cfg, scope, now = _scope(args)
    if args.json_file:
        payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    else:
        payload = {
            "merchant": args.merchant,
            "title": args.title,
            "expires_on": args.expires_on,
            "code": args.code,
            "value_text": args.value_text,
            "uses_total": args.uses,
            "notes": args.notes,
        }
    missing = [k for k in ("merchant", "title", "expires_on") if not payload.get(k)]
    if missing:
        raise CouponError(f"missing required field(s): {', '.join(missing)}")

    media_id = None
    if args.media:
        media_id = store.register_media(scope, args.media, now).id

    coupon = store.add_coupon(
        scope,
        now,
        merchant=payload["merchant"],
        title=payload["title"],
        expires_on=payload["expires_on"],
        status=payload.get("status", "active"),
        expiry_precision=payload.get("expiry_precision", "exact"),
        expiry_assumed=bool(payload.get("expiry_assumed")),
        uses_total=int(payload.get("uses_total") or 1),
        conditions=payload.get("conditions"),
        code=payload.get("code"),
        value_text=payload.get("value_text"),
        notes=payload.get("notes"),
        raw_text=payload.get("raw_text"),
        source_kind=payload.get("source_kind", "manual"),
        media_id=media_id,
    )
    return _emit(
        args,
        {"ok": True, "account_id": scope.account_id, "coupon": coupon.to_dict()},
        f"added {coupon.id}  {coupon.merchant} — {coupon.title} (expires {coupon.expires_on})",
    )


def cmd_commit_candidates(args) -> int:
    cfg, scope, now = _scope(args)
    path = Path(args.path)
    if not path.is_absolute():
        path = scope.inbox_dir / path
    result = store.commit_candidates(scope, path, now, auto_confirm=args.auto_confirm)
    text = "\n".join(
        f"{c['status']:<12} {c['id']}  {c['merchant']} — {c['title']}"
        + (f"\n             ↳ {'; '.join(c['review_reasons'])}" if c["review_reasons"] else "")
        for c in result["committed"]
    ) or "no candidates in file"
    return _emit(args, {"ok": True, **result}, text)


def cmd_list(args) -> int:
    cfg, scope, now = _scope(args)
    spec = query.FilterSpec(
        statuses=tuple(args.status or ()),
        merchant=args.merchant,
        expiring_within_days=args.expiring_within,
        include_expired=args.include_expired,
        limit=args.limit,
    )
    coupons = query.list_coupons(scope, spec, now)
    return _emit(
        args,
        {
            "ok": True,
            "account_id": scope.account_id,
            "coupons": [c.to_dict() for c in coupons],
            "total": len(coupons),
        },
        _format_list(coupons, now) or "nothing matches",
    )


def cmd_usable_now(args) -> int:
    cfg, scope, now = _scope(args)
    at = clock.parse_datetime(args.at, cfg.timezone) if args.at else now
    results = query.usable_now(
        scope,
        now,
        at=at,
        channel=args.channel,
        location=args.location,
        payment_method=args.payment_method,
        spend=args.spend,
    )
    lines = []
    for result in results:
        line = _format_coupon(result.coupon, now)
        for caveat in result.caveats:
            line += f"\n     ⚠ {_describe(caveat)}"
        lines.append(line)
    return _emit(
        args,
        {
            "ok": True,
            "account_id": scope.account_id,
            "at": clock.iso(at),
            "coupons": [r.to_dict() for r in results],
            "total": len(results),
        },
        "\n".join(lines) or "nothing usable right now",
    )


def cmd_show(args) -> int:
    cfg, scope, now = _scope(args)
    coupon = store.get(scope, args.id)
    media = store.get_media(scope, coupon.media_id) if coupon.media_id else None
    payload = {
        "ok": True,
        "account_id": scope.account_id,
        "coupon": coupon.to_dict(),
        "media": {"id": media.id, "path": media.path, "bytes": media.bytes} if media else None,
    }
    text = (
        f"{coupon.merchant} — {coupon.title}\n"
        f"  id: {coupon.id}\n"
        f"  status: {coupon.status}  uses: {coupon.uses_remaining}/{coupon.uses_total}\n"
        f"  expires: {coupon.expires_on} ({coupon.expiry_precision}"
        f"{', assumed' if coupon.expiry_assumed else ''})\n"
        f"  code: {coupon.code or '-'}  value: {coupon.value_text or '-'}\n"
        f"  conditions: {', '.join(_describe(c) for c in coupon.conditions) or 'none'}\n"
        f"  notes: {coupon.notes or '-'}"
    )
    return _emit(args, payload, text)


def cmd_review(args) -> int:
    cfg, scope, now = _scope(args)
    if args.resolve:
        result = lifecycle.resolve_review(scope, args.resolve, args.as_status, now)
        return _emit(
            args,
            {"ok": True, "account_id": scope.account_id, **result.to_dict()},
            f"{result.coupon_id}: needs_review → {result.new_status}",
        )
    pending = query.needs_review(scope)
    return _emit(
        args,
        {
            "ok": True,
            "account_id": scope.account_id,
            "coupons": [c.to_dict() for c in pending],
            "total": len(pending),
        },
        _format_list(pending, now) or "review queue is empty",
    )


def _transition(args, action) -> int:
    cfg, scope, now = _scope(args)
    result = action(scope, now)
    verb = "no change" if result.no_op else f"{result.previous_status} → {result.new_status}"
    return _emit(
        args,
        {"ok": True, "account_id": scope.account_id, **result.to_dict()},
        f"{result.coupon_id}: {verb} (uses left: {result.uses_remaining})",
    )


def cmd_use(args) -> int:
    return _transition(args, lambda s, n: lifecycle.mark_used(s, args.id, n, args.uses))


def cmd_unuse(args) -> int:
    return _transition(args, lambda s, n: lifecycle.mark_unused(s, args.id, n, force=args.force))


def cmd_void(args) -> int:
    return _transition(args, lambda s, n: lifecycle.mark_void(s, args.id, args.reason, n))


def cmd_extend(args) -> int:
    return _transition(args, lambda s, n: lifecycle.extend(s, args.id, args.to, n))


def cmd_sweep_expiry(args) -> int:
    cfg, scope, now = _scope(args)
    swept = lifecycle.sweep_expiry(scope, now, commit=args.commit)
    verb = "expired" if args.commit else "would expire"
    return _emit(
        args,
        {
            "ok": True,
            "account_id": scope.account_id,
            "dry_run": not args.commit,
            "coupons": [c.to_dict() for c in swept],
            "total": len(swept),
        },
        f"{verb} {len(swept)} coupon(s)",
    )


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #


def cmd_alerts_due(args) -> int:
    """Unscoped by default: iterates accounts, isolating failures per account."""
    cfg, conn = _open(args)
    now = _now(args, cfg)
    account_id = args.account or os.environ.get(ACCOUNT_ENV)
    result = alerts.run(cfg, conn, now, commit=args.commit, account_id=account_id)

    blocks = []
    for group in result["groups"]:
        blocks.append(
            f"— {group['display_name']} (chat {group['chat_id']}) —\n"
            + alerts.format_group(group)
        )
    for skip in result["skipped"]:
        blocks.append(f"— {skip['display_name']}: {skip['due']} due but {skip['reason']}")
    for failure in result["failures"]:
        blocks.append(f"— {failure['display_name']}: FAILED {failure['error']}")

    _emit(args, {"ok": True, **result}, "\n\n".join(blocks) or "nothing due")

    # Non-zero only if every account failed — one bad account is not a run failure.
    totals = result["totals"]
    if totals["failed"] and not totals["accounts_with_alerts"]:
        return int(ExitCode.ERR_DB)
    return int(ExitCode.OK)


# --------------------------------------------------------------------------- #
# Purge
# --------------------------------------------------------------------------- #


def cmd_purge(args) -> int:
    cfg, scope, now = _scope(args)
    selection = purge.PurgeSelection(
        include_void=args.include_void,
        older_than_days=args.older_than,
        merchant=args.merchant,
        ids=tuple(args.id or ()),
    )
    manifest = purge.run(scope, selection, now, commit=args.commit)
    manifest["manifest_path"] = str(purge.write_manifest(scope, manifest, now))

    verb = "purged" if args.commit else "would purge"
    lines = [
        f"{verb} {manifest['totals']['coupons']} coupon(s), "
        f"deleting {manifest['totals']['media_deleted']} image(s) "
        f"({manifest['totals']['bytes_freed']} bytes)"
    ]
    for held in manifest["media_held"]:
        lines.append(f"  held: {held['path']} — {held['refs']} coupon(s) still reference it")
    for orphan in manifest["anomalies"]["orphan_files"]:
        lines.append(f"  anomaly: orphan file {orphan} (not deleted)")
    for missing in manifest["anomalies"]["missing_files"]:
        lines.append(f"  anomaly: missing file {missing['path']} (row kept)")

    _emit(args, {"ok": True, **manifest}, "\n".join(lines))
    return int(ExitCode.ERR_PURGE_UNSAFE if purge.is_unsafe(manifest) else ExitCode.OK)


def cmd_purge_manifest(args) -> int:
    cfg = _load_config(args)
    manifests = sorted(cfg.logs_dir.glob("purge-*.json"))
    if not manifests:
        raise NotFoundError("no purge manifests yet", {"logs_dir": str(cfg.logs_dir)})
    latest = manifests[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    return _emit(args, {"ok": True, "path": str(latest), **payload}, latest.read_text("utf-8"))


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def _describe(condition) -> str:
    from .predicates import describe
    from .models import Predicate

    if isinstance(condition, Predicate):
        return describe(condition)
    return str(condition)


def _format_coupon(coupon: Coupon, now: datetime) -> str:
    days = clock.days_between(clock.today(now), clock.parse_date(coupon.expires_on))
    if days < 0:
        urgency = f"expired {-days}d ago"
    elif days == 0:
        urgency = "expires TODAY"
    else:
        urgency = f"{days}d left"
    return (
        f"  {coupon.id}  {coupon.merchant} — {coupon.title}"
        f"  [{coupon.status}, {urgency}]"
    )


def _format_list(coupons: list[Coupon], now: datetime) -> str:
    return "\n".join(_format_coupon(c, now) for c in coupons)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _with_common(common: argparse.ArgumentParser):
    """A parser class that gives every subparser the global flags."""

    class _Sub(argparse.ArgumentParser):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("parents", []).append(common)
            super().__init__(*args, **kwargs)

    return _Sub


def _build_parser() -> argparse.ArgumentParser:
    # Global flags live on a parent parser so they work in either position:
    # `couponctl --json list` and `couponctl list --json` both parse.
    #
    # SUPPRESS is load-bearing: a subparser copies its whole namespace over the
    # parent's, so a plain default would clobber `--config` given before the
    # subcommand. Suppressed args simply stay absent, hence the getattr()s above.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", default=argparse.SUPPRESS, help="path to config.yaml or the skill root"
    )
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="machine-readable output",
    )
    common.add_argument(
        "--now", default=argparse.SUPPRESS,
        help="freeze the clock (ISO8601); for tests and replays",
    )

    parser = argparse.ArgumentParser(prog="couponctl", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="command", parser_class=_with_common(common))

    def scoped(sp):
        """Flags every account-scoped command shares."""
        sp.add_argument("--account", help=f"account id (or ${ACCOUNT_ENV})")
        sp.add_argument("--telegram-user", help="resolve the account by telegram user id")
        return sp

    p = sub.add_parser("init", help="create config.yaml, directories and the database")
    p.add_argument("--root", help="skill root (default: cwd)")
    p.set_defaults(handler=cmd_init)

    sub.add_parser("migrate", help="apply pending migrations").set_defaults(handler=cmd_migrate)
    sub.add_parser("doctor", help="integrity check across every account").set_defaults(
        handler=cmd_doctor
    )

    # -- accounts ----------------------------------------------------------- #
    account = sub.add_parser("account", help="manage accounts").add_subparsers(
        dest="account_cmd", parser_class=_with_common(common)
    )

    p = account.add_parser("add", help="create an account")
    p.add_argument("--name", required=True)
    p.add_argument("--telegram-user")
    p.add_argument("--chat-id")
    p.set_defaults(handler=cmd_account_add)

    account.add_parser("list", help="list accounts with counts").set_defaults(
        handler=cmd_account_list
    )

    scoped(account.add_parser("show", help="counts and telegram binding")).set_defaults(
        handler=cmd_account_show
    )

    p = account.add_parser("set", help="rename or re-bind an account")
    p.add_argument("--account")
    p.add_argument("--name")
    p.add_argument("--telegram-user")
    p.add_argument("--chat-id")
    p.set_defaults(handler=cmd_account_set)

    p = scoped(account.add_parser("delete", help="delete an account and everything it owns"))
    p.add_argument("--commit", action="store_true", help="actually delete (default: dry run)")
    p.set_defaults(handler=cmd_account_delete)

    # -- coupons ------------------------------------------------------------ #
    p = scoped(sub.add_parser("add", help="add one coupon"))
    p.add_argument("--json-file", help="read the coupon from a JSON file")
    p.add_argument("--merchant")
    p.add_argument("--title")
    p.add_argument("--expires-on", dest="expires_on")
    p.add_argument("--code")
    p.add_argument("--value-text", dest="value_text")
    p.add_argument("--uses", type=int, default=1)
    p.add_argument("--notes")
    p.add_argument("--media", help="image to register with this coupon")
    p.set_defaults(handler=cmd_add)

    p = scoped(sub.add_parser("commit-candidates", help="commit an agent candidates file"))
    p.add_argument("path", help="candidates JSON, absolute or relative to the account inbox")
    p.add_argument("--auto-confirm", action="store_true")
    p.set_defaults(handler=cmd_commit_candidates)

    p = scoped(sub.add_parser("list", help="list coupons"))
    p.add_argument("--status", action="append")
    p.add_argument("--merchant")
    p.add_argument("--expiring-within", type=int, dest="expiring_within")
    p.add_argument("--include-expired", action="store_true")
    p.add_argument("--limit", type=int)
    p.set_defaults(handler=cmd_list)

    p = scoped(sub.add_parser("usable-now", help="what can I use right now"))
    p.add_argument("--at", help="evaluate at this moment instead of now")
    p.add_argument("--channel", choices=("dine_in", "takeaway", "delivery"))
    p.add_argument("--location")
    p.add_argument("--payment-method", dest="payment_method")
    p.add_argument("--spend", type=float)
    p.set_defaults(handler=cmd_usable_now)

    p = scoped(sub.add_parser("show", help="one coupon in full"))
    p.add_argument("id")
    p.set_defaults(handler=cmd_show)

    p = scoped(sub.add_parser("review", help="walk the needs_review queue"))
    p.add_argument("--resolve", help="coupon id to resolve")
    p.add_argument("--as", dest="as_status", choices=("active", "void"), default="active")
    p.set_defaults(handler=cmd_review)

    p = scoped(sub.add_parser("use", help="mark used"))
    p.add_argument("id")
    p.add_argument("--uses", type=int, default=1)
    p.set_defaults(handler=cmd_use)

    p = scoped(sub.add_parser("unuse", help="undo a use"))
    p.add_argument("id")
    p.add_argument("--force", action="store_true")
    p.set_defaults(handler=cmd_unuse)

    p = scoped(sub.add_parser("void", help="mark void"))
    p.add_argument("id")
    p.add_argument("--reason", required=True)
    p.set_defaults(handler=cmd_void)

    p = scoped(sub.add_parser("extend", help="push the expiry date out"))
    p.add_argument("id")
    p.add_argument("--to", required=True, help="new expiry, YYYY-MM-DD")
    p.set_defaults(handler=cmd_extend)

    p = scoped(sub.add_parser("sweep-expiry", help="expire past-dated coupons"))
    p.add_argument("--commit", action="store_true")
    p.set_defaults(handler=cmd_sweep_expiry)

    # -- alerts ------------------------------------------------------------- #
    alert = sub.add_parser("alerts", help="what is due for an alert").add_subparsers(
        dest="alerts_cmd", parser_class=_with_common(common)
    )
    p = alert.add_parser("due", help="coupons due for an alert, grouped by account")
    p.add_argument("--account", help="restrict to one account (default: all)")
    p.add_argument(
        "--commit", action="store_true",
        help="also persist the expiry sweep (default: report only)",
    )
    p.set_defaults(handler=cmd_alerts_due)

    # -- purge -------------------------------------------------------------- #
    p = scoped(sub.add_parser("purge", help="delete used/expired coupons and orphaned media"))
    p.add_argument("--commit", action="store_true", help="actually delete (default: dry run)")
    p.add_argument("--include-void", action="store_true")
    p.add_argument("--older-than", type=int, metavar="DAYS")
    p.add_argument("--merchant")
    p.add_argument("--id", action="append")
    p.set_defaults(handler=cmd_purge)

    p = sub.add_parser("purge-manifest", help="show the most recent purge manifest")
    p.add_argument("--last", action="store_true", default=True)
    p.set_defaults(handler=cmd_purge_manifest)

    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
