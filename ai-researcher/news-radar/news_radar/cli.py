"""JSON-in / JSON-out command line.

Every subcommand prints exactly one indented JSON object on stdout and exits 0
on success. A failure prints ``{"ok": false, "error": "ERR_...", ...}`` and exits
with the code from :class:`news_radar.errors.ExitCode`, so the agent branches on
a number and a closed enum rather than on a sentence.

Two commands, two schedules. ``scan`` collects and says nothing; ``digest``
reports. They are wired to separate cron entries on purpose -- see SKILL.md.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import clock, db, digest as digest_rules, render, scan as scan_run, settings
from .config import load_config
from .errors import ExitCode, RadarError


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news-radar",
        description="Watch configured news sources and digest what is new, by category.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Fetch every enabled source once and store what is new.")
    scan.add_argument("--source", action="append", dest="sources", help="Repeatable. Defaults to all enabled.")
    scan.add_argument("--category", action="append", dest="categories", help="Repeatable.")
    scan.add_argument("--dry-run", action="store_true",
                      help="Show what was caught and how it would be stored. Writes nothing.")
    scan.add_argument("--seed", action="store_true",
                      help="Absorb what is published now without reporting it. Implied by a first scan.")
    scan.add_argument("--ignore-throttle", action="store_true",
                      help="Fetch even a source inside its min_interval_minutes. For testing a source.")

    dig = sub.add_parser("digest", help="Everything not yet reported, clustered and in sections.")
    dig.add_argument("--commit", action="store_true", help="Stamp the items as reported before returning.")
    dig.add_argument("--category", action="append", dest="categories", help="Repeatable.")
    dig.add_argument("--limit", type=int, default=None)
    dig.add_argument("--text", action="store_true",
                     help="Also include a ready-to-send plain-text body under 'body'.")

    sources = sub.add_parser("sources", help="The configured sources and categories, and per-source health.")
    sources.add_argument("--all", action="store_true", help="Include disabled sources.")

    items = sub.add_parser("items", help="What has been seen, newest first.")
    items.add_argument("--source", action="append", dest="sources")
    items.add_argument("--category", action="append", dest="categories")
    items.add_argument("--since", help="ISO date or timestamp; filters on first_seen_at.")
    items.add_argument("--limit", type=int, default=20)

    runs = sub.add_parser("runs", help="Recent scans -- the liveness and triage surface.")
    runs.add_argument("--limit", type=int, default=5)

    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except RadarError as exc:
        _emit(exc.payload())
        return int(exc.exit_code)


def _dispatch(args) -> int:
    config = load_config()
    conn = db.connect()
    now = clock.now(config.tzinfo())

    if args.command == "scan":
        return _cmd_scan(args, config, conn, now)
    if args.command == "digest":
        return _cmd_digest(args, config, conn, now)
    if args.command == "sources":
        return _cmd_sources(args, config, conn, now)
    if args.command == "items":
        names = None
        if args.sources or args.categories:
            names = [source.name for source in config.select(
                args.sources, args.categories, include_disabled=True)]
        rows = db.recent_items(conn, sources=names, since=args.since, limit=args.limit)
        _emit({
            "ok": True,
            "count": len(rows),
            "items": [{**row.to_dict(), "category": config.category_of(row.source)} for row in rows],
        })
        return int(ExitCode.OK)
    if args.command == "runs":
        _emit({"ok": True, "runs": db.recent_runs(conn, args.limit), **db.pending_count(conn)})
        return int(ExitCode.OK)
    return int(ExitCode.OK)


def _cmd_scan(args, config, conn, now) -> int:
    sources = config.select(args.sources, args.categories)
    if not sources:
        _emit({
            "ok": True,
            "status": "skipped",
            "reason": "no_enabled_sources",
            "message": f"{config.path} has no enabled sources matching the request; nothing to scan",
            **db.pending_count(conn),
        })
        return int(ExitCode.OK)

    needs_browser = any(source.render == "browser" for source in sources)
    with render.Browser(headless=settings.headless(), timeout=settings.http_timeout()) as browser:
        result = scan_run.scan(
            conn, config, sources, now,
            seed=args.seed, dry_run=args.dry_run, ignore_throttle=args.ignore_throttle,
            browser=browser if needs_browser else None,
        )
    _emit(result)
    return int(ExitCode.OK) if result["ok"] else int(ExitCode.ERR_FETCH)


def _cmd_digest(args, config, conn, now) -> int:
    payload = digest_rules.build(
        conn, config, now,
        commit=args.commit, categories=args.categories, limit=args.limit,
    )
    if args.text:
        payload["body"] = digest_rules.format_digest(payload)
    _emit(payload)
    return int(ExitCode.OK)


def _cmd_sources(args, config, conn, now) -> int:
    state = db.all_source_state(conn)
    _emit({
        "ok": True,
        "config": str(config.path),
        "db": str(settings.db_path()),
        "timezone": config.timezone_name,
        "now": now.isoformat(),
        "request_delay_seconds": config.request_delay_seconds,
        "detail_budget": config.detail_budget,
        "cluster_threshold": config.cluster_threshold,
        "categories": [category.to_dict() for category in config.categories],
        "exclude": list(config.exclude_keywords),
        "sources": [
            {
                **source.to_dict(),
                "seeded": bool(state.get(source.name, {}).get("seeded")),
                "last_ok_at": state.get(source.name, {}).get("last_ok_at"),
                "consecutive_failures": state.get(source.name, {}).get("consecutive_failures", 0),
                "last_error": state.get(source.name, {}).get("last_error"),
                "recent_yield": state.get(source.name, {}).get("recent_yield", 0),
                "next_eligible": _next_eligible(state.get(source.name), source, now),
            }
            for source in config.select(include_disabled=args.all)
        ],
        **db.pending_count(conn),
    })
    return int(ExitCode.OK)


def _next_eligible(state, source, now) -> str | None:
    ready = db.throttled_until(state, source.min_interval_minutes, now)
    return ready.isoformat() if ready else None


def main() -> None:
    sys.exit(run())
