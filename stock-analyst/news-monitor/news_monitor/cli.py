"""JSON-in / JSON-out command line.

Every subcommand prints exactly one indented JSON object on stdout and exits 0
on success. A failure prints ``{"ok": false, "error": "ERR_...", ...}`` and exits
with the code from :class:`news_monitor.errors.ExitCode`, so the agent branches
on a number and a closed enum rather than on a sentence.

``check`` is the cron entry and the only command that produces something to
report. ``mark`` is the other half of it: the ledger is stamped *after* an item
has actually been written up, because a run can die between the payload and the
message and an item that was never reported must come round again.

``add``, ``enable`` and ``disable`` are the source list's whole editing surface.
There is no config file to edit -- the ``feed`` table is the source of truth.
All three act on the operator's request only; ``check`` never proposes a source.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit

from . import check as check_run
from . import clock, db, gate, settings
from .config import load_seeds, load_taxonomy
from .errors import (
    CandidateError, ConfigError, DatabaseError, DeliveryError, ExitCode, NewsMonitorError,
    NotFoundError,
)


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news-monitor",
        description="Poll a database of finance feeds and hand over every headline not seen before.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="Fetch every enabled feed, store what is new, and return the unseen items.",
    )
    check.add_argument("--feed", action="append", dest="feeds",
                       help="Repeatable. Name or url. Defaults to all enabled.")
    check.add_argument("--limit", type=int, default=None,
                       help="Items to hand over this run. Defaults to max_per_check.")
    check.add_argument("--dry-run", action="store_true",
                       help="Show what a feed would yield. Writes nothing, returns nothing.")
    check.add_argument("--seed", action="store_true",
                       help="Absorb what is published now without reporting it. "
                            "Implied by a feed's first check.")

    feeds = sub.add_parser("feeds", aliases=["list"],
                           help="The source list -- name, url, category, enable state and health.")
    feeds.add_argument("--enabled", action="store_true", help="Only the feeds that are on.")
    feeds.add_argument("--category", help="Only this category.")

    add = sub.add_parser(
        "add",
        help="Add a source the operator asked for. Fetched, parsed and gated before it "
             "becomes a row.",
    )
    add.add_argument("--url", required=True)
    add.add_argument("--name", help="Defaults to a slug of the feed's own title.")
    add.add_argument("--category", default="general",
                     help="The bucket it reports under. Defaults to general.")
    add.add_argument("--note", help="One line on what it covers. Reaches the agent verbatim.")
    add.add_argument("--enable", dest="force_state", action="store_const", const=True,
                     help="Enable even if the gate held it back.")
    add.add_argument("--disable", dest="force_state", action="store_const", const=False,
                     help="Store it off even if the gate passed it.")
    add.add_argument("--dry-run", action="store_true",
                     help="Report the gate verdict without writing a row.")

    enable = sub.add_parser("enable", help="Turn a feed on.")
    enable.add_argument("--feed", required=True, help="Name or url.")

    disable = sub.add_parser("disable", help="Turn a feed off. It is never deleted.")
    disable.add_argument("--feed", required=True, help="Name or url.")

    mark = sub.add_parser("mark", help="Acknowledge items after a confirmed external send.")
    mark.add_argument("--item", action="append", dest="items",
                      help="Repeatable. A row id or a fingerprint.")
    mark.add_argument("--all", action="store_true",
                      help="Every pending item. Use only when the whole batch went out.")

    discard = sub.add_parser("discard", help="Retire in-scope items intentionally omitted.")
    discard.add_argument("--item", action="append", dest="items", required=True,
                         help="Repeatable. A row id or a fingerprint.")

    deliver = sub.add_parser("deliver", help="Send a digest from stdin and mark its items on success.")
    deliver.add_argument("--item", action="append", dest="items", type=int, required=True,
                         help="Repeatable. A check.items row id included in the digest.")

    items = sub.add_parser("items", help="What has been seen, newest first.")
    items.add_argument("--feed", action="append", dest="feeds")
    items.add_argument("--pending", action="store_true", help="Only items waiting to be reported.")
    items.add_argument("--reported", action="store_true", help="Only items already reported.")
    items.add_argument("--since", help="ISO date or timestamp; filters on first_seen_at.")
    items.add_argument("--limit", type=int, default=20)

    runs = sub.add_parser("runs", help="Recent checks -- the liveness and triage surface.")
    runs.add_argument("--limit", type=int, default=5)

    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except NewsMonitorError as exc:
        _emit(exc.payload())
        return int(exc.exit_code)


def _dispatch(args) -> int:
    conn = db.connect()
    now = clock.now(settings.timezone())
    db.bootstrap(conn, load_seeds(), now)

    if args.command == "check":
        return _cmd_check(args, conn, now)
    if args.command in ("feeds", "list"):
        return _cmd_feeds(args, conn)
    if args.command == "add":
        return _cmd_add(args, conn, now)
    if args.command in ("enable", "disable"):
        return _cmd_set_state(args, conn, now, enabled=args.command == "enable")
    if args.command in ("mark", "discard", "deliver"):
        return _cmd_dispose(args, conn, now)
    if args.command == "items":
        return _cmd_items(args, conn)
    _emit({"ok": True, "runs": db.recent_runs(conn, args.limit), **db.pending_count(conn)})
    return int(ExitCode.OK)


def _cmd_check(args, conn, now) -> int:
    feeds = db.select_feeds(conn, args.feeds, include_disabled=False)
    if not feeds:
        _emit({
            "ok": True,
            "status": "skipped",
            "reason": "no_enabled_feeds",
            "message": "no enabled feed matches the request; nothing to check",
            "items": [],
            **db.pending_count(conn),
        })
        return int(ExitCode.OK)

    result = check_run.check(
        conn, load_taxonomy(), feeds, now,
        seed=args.seed, dry_run=args.dry_run, limit=args.limit,
    )
    _emit(result)
    return int(ExitCode.OK)


def _cmd_feeds(args, conn) -> int:
    counts = db.feed_counts(conn)
    rows = db.feeds(conn, include_disabled=not args.enabled)
    if args.category:
        rows = [feed for feed in rows if feed.category == args.category]
    _emit({
        "ok": True,
        "db": str(settings.db_path()),
        "taxonomy": str(settings.taxonomy_path()),
        "count": len(rows),
        "feeds": [
            {
                **feed.to_dict(),
                "items_seen": counts.get(feed.name, {}).get("items", 0),
                "items_pending": counts.get(feed.name, {}).get("pending", 0),
                "latest_seen_at": counts.get(feed.name, {}).get("latest_seen_at"),
            }
            for feed in rows
        ],
        **db.pending_count(conn),
    })
    return int(ExitCode.OK)


def _cmd_add(args, conn, now) -> int:
    taxonomy = load_taxonomy()
    url = args.url.strip()

    existing = db.find_feed(conn, url)
    if existing is not None:
        raise CandidateError(
            f"already tracked as {existing.name!r}",
            url=url, reason="already_tracked", feed=existing.name, enabled=existing.enabled,
        )

    probe = gate.probe(url, taxonomy)
    if db.find_feed(conn, probe.url) is not None:
        # The url redirected onto something already on the list -- a publisher
        # consolidating two sections, or a shortener. Caught here rather than by
        # the unique index, so the agent gets a reason instead of a DB error.
        tracked = db.find_feed(conn, probe.url)
        raise CandidateError(
            f"redirects onto {tracked.name!r}, which is already tracked",
            url=url, resolved_url=probe.url, reason="already_tracked", feed=tracked.name,
        )

    verdict = gate.judge(probe, now)
    enabled = verdict.enable if args.force_state is None else args.force_state
    name = args.name or gate.unique_name(
        gate.slugify(probe.title or "", urlsplit(probe.url).netloc),
        {feed.name for feed in db.feeds(conn)},
    )

    payload = {
        "ok": True,
        "url": probe.url,
        "name": name,
        "category": args.category,
        "feed_title": probe.title,
        "gate": {"verdict": verdict.reason, "passed": verdict.passed, **verdict.detail},
        "enabled": enabled,
        "overridden": args.force_state is not None and args.force_state != verdict.enable,
        "stored": not args.dry_run,
    }

    if not args.dry_run:
        db.add_feed(
            conn,
            name=name, url=probe.url, category=args.category, note=args.note,
            enabled=enabled, origin="added",
            gate_verdict=verdict.reason, gate_detail=json.dumps(verdict.detail),
            now=now,
        )
        # A feed enabled on its first check absorbs its back catalogue silently,
        # so nothing here needs to pre-stamp anything: check.py does it.

    _emit(payload)
    return int(ExitCode.OK)


def _cmd_set_state(args, conn, now, *, enabled: bool) -> int:
    feed = db.find_feed(conn, args.feed)
    if feed is None:
        raise NotFoundError(f"no feed matching {args.feed!r}", reference=args.feed)
    changed = db.set_enabled(conn, feed.name, enabled, now)
    _emit({
        "ok": True,
        "feed": feed.name,
        "url": feed.url,
        "enabled": enabled,
        # Not an error: asking for the state it is already in is a fine thing to
        # do after a retry, and saying so beats failing.
        "changed": changed,
    })
    return int(ExitCode.OK)


def _cmd_dispose(args, conn, now) -> int:
    body = ""
    if args.command == "deliver":
        body = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
        if not body:
            raise ConfigError("deliver needs a digest on stdin")
        if "MEDIA:" in body.upper():
            raise ConfigError("deliver accepts digest text only")

    try:
        with conn:
            # Keep eligibility and the final write in one transaction so a
            # concurrent delivery or discard cannot act on the same item.
            conn.execute("BEGIN IMMEDIATE")
            if args.command == "deliver":
                return _cmd_deliver(args, conn, body)
            if args.command == "discard":
                return _cmd_discard(args, conn, now)
            return _cmd_mark(args, conn, now)
    except sqlite3.Error as exc:
        raise DatabaseError("could not update the news ledger", action=args.command) from exc


def _resolve_items(conn, references):
    items = {}
    for reference in references:
        item = db.resolve_item(conn, str(reference))
        if item is None:
            raise NotFoundError(f"no item matching {reference!r}", reference=reference)
        if item.reported_at is None and not (item.scope and item.scope["is_target_scope"]):
            raise ConfigError("item has not passed JEV scope classification", item_id=item.id)
        items[item.id] = item
    return list(items.values())


def _cmd_mark(args, conn, now) -> int:
    if not args.items and not args.all:
        raise NotFoundError("mark needs --item <id> (repeatable) or --all")
    items = db.pending_items(conn, classified=True) if args.all else _resolve_items(conn, args.items)
    return _mark_items(conn, items, now)


def _mark_items(conn, items, now) -> int:
    wanted = [item.id for item in items if item.discarded_at is None]
    stamped = db.mark_reported(conn, wanted, now)
    _emit({
        "ok": True,
        "marked": stamped,
        "already_marked": [item.id for item in items if item.reported_at is not None],
        "already_discarded": [item.id for item in items if item.discarded_at is not None],
        **db.pending_count(conn),
    })
    return int(ExitCode.OK)


def _cmd_discard(args, conn, now) -> int:
    items = _resolve_items(conn, args.items)
    discarded = db.discard_items(conn, [item.id for item in items], now)
    _emit({
        "ok": True,
        "discarded": discarded,
        "already_discarded": [item.id for item in items if item.discarded_at is not None],
        "already_marked": [item.id for item in items if item.reported_at is not None],
        **db.pending_count(conn),
    })
    return int(ExitCode.OK)


def _cmd_deliver(args, conn, body: str) -> int:
    items = _resolve_items(conn, args.items)
    pending = [item for item in items if item.reported_at is None and item.discarded_at is None]
    if all(item.reported_at is not None for item in items):
        return _mark_items(conn, items, clock.now(settings.timezone()))
    if len(pending) != len(items):
        raise ConfigError("rebuild the digest using only pending items",
                          pending_ids=[item.id for item in pending])
    missing_urls = [item.id for item in items if item.url not in body]
    if missing_urls:
        raise ConfigError("digest is missing item URLs", item_ids=missing_urls)

    target = settings.delivery_target()
    if not target:
        raise ConfigError("set NEWS_MONITOR_DELIVERY_TARGET to the cron job's destination")
    try:
        result = subprocess.run(
            ["hermes", "-p", "stock-analyst", "send", "--to", target, "--file", "-", "--quiet"],
            input=body.encode("utf-8"), capture_output=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError("Hermes did not confirm delivery", reason=type(exc).__name__) from exc
    if result.returncode:
        raise DeliveryError("Hermes send failed", sender_exit_code=result.returncode)
    return _mark_items(conn, items, clock.now(settings.timezone()))


def _cmd_items(args, conn) -> int:
    names = None
    if args.feeds:
        names = [feed.name for feed in db.select_feeds(conn, args.feeds, include_disabled=True)]
    state = "pending" if args.pending else "reported" if args.reported else None
    rows = db.recent_items(conn, feed_names=names, since=args.since, state=state, limit=args.limit)
    _emit({
        "ok": True,
        "count": len(rows),
        "items": [row.to_dict() for row in rows],
        **db.pending_count(conn),
    })
    return int(ExitCode.OK)


def main() -> None:
    sys.exit(run())
