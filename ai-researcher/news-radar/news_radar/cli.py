"""JSON-in / JSON-out command line.

Every subcommand prints exactly one indented JSON object on stdout and exits 0
on success. A failure prints ``{"ok": false, "error": "ERR_...", ...}`` and exits
with the code from :class:`news_radar.errors.ExitCode`, so the agent branches on
a number and a closed enum rather than on a sentence.

Hermes executes the staged search and writing workflow; Python retains evidence
and delivery state. Feed scan/digest commands remain for explicit feed configs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import briefing, clock, db, digest as digest_rules, evidence, research, render
from . import scan as scan_run, settings
from .config import load_config
from .errors import ConfigError, ExitCode, RadarError


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news-radar",
        description="Discover AI vendor news, shortlist stories, collect evidence, and prepare an AI briefing.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_dir = sub.add_parser("run-dir", help="Create a fresh timestamped directory for this invocation.")
    run_dir.add_argument("--root", required=True, type=Path, help="Profile artifact runs directory.")

    plan = sub.add_parser("research", help="Return vendor search jobs for Hermes; makes no network calls.")
    plan.add_argument("--topic", help="Return HN/Reddit jobs for a selected story instead.")

    ingest = sub.add_parser("ingest", help="Validate and store actual search results.")
    ingest.add_argument("--input", required=True, help="JSON file, or - for stdin.")

    shortlist = sub.add_parser("shortlist", help="Compact pending stories from current enabled sources.")
    shortlist.add_argument("--limit", type=int, default=100)

    article = sub.add_parser("evidence", help="Fetch and cache readable article evidence for selected IDs.")
    article.add_argument("--id", action="append", type=int, required=True, dest="ids")
    article.add_argument("--refresh", action="store_true")

    draft = sub.add_parser("briefing", help="Validate and retain a draft, or retrieve a saved briefing.")
    draft_input = draft.add_mutually_exclusive_group(required=True)
    draft_input.add_argument("--input", help="Hermes-authored JSON file, or - for stdin.")
    draft_input.add_argument("--id", type=int, dest="briefing_id")
    draft.add_argument("--html", type=Path, required=True, help="Required HTML export path; refuses to overwrite.")

    commit = sub.add_parser("commit", help="Acknowledge successful delivery of a saved briefing.")
    commit.add_argument("--briefing", required=True, type=int, dest="briefing_id")

    dismiss = sub.add_parser("dismiss", help="Retain an editorial rejection and clear it from the queue.")
    dismiss.add_argument("--id", action="append", type=int, required=True, dest="ids")
    dismiss.add_argument("--reason", required=True)

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
        _emit({**exc.payload(), "body": exc.message})
        return int(exc.exit_code)


def _dispatch(args) -> int:
    config = load_config()
    now = clock.now(config.tzinfo())
    if args.command == "run-dir":
        return _cmd_run_dir(args, now)
    conn = db.connect()

    if args.command in {"research", "ingest", "shortlist", "evidence", "briefing", "commit", "dismiss"}:
        try:
            return _cmd_staged(args, config, conn, now)
        finally:
            conn.close()
    if args.command == "digest" and any(s.kind == "search" for s in config.select()):
        conn.close()
        raise ConfigError("search workflow: use shortlist, evidence, briefing, then commit after delivery")

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


def _cmd_run_dir(args, now) -> int:
    root = args.root.expanduser().resolve()
    run_dir = root / now.strftime("%Y%m%dT%H%M%S")
    try:
        root.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir()
    except FileExistsError as exc:
        raise ConfigError(f"run directory already exists: {run_dir}; retry in a later second, never reuse it") from exc
    except OSError as exc:
        raise ConfigError(f"could not create run directory: {exc}") from exc
    _emit({"ok": True, "run_dir": str(run_dir),
           "results_path": str(run_dir / "results.json"),
           "draft_path": str(run_dir / "draft.json"),
           "html_path": str(run_dir / "briefing.html")})
    return int(ExitCode.OK)


def _load_json(path: str) -> dict:
    try:
        if path == "-":
            raw = sys.stdin.read(250_001)
        else:
            with open(path, encoding="utf-8") as handle:
                raw = handle.read(250_001)
        if len(raw) > 250_000:
            raise ConfigError("JSON input exceeds 250000 characters")
        return json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read JSON input: {exc}") from exc


def _cmd_staged(args, config, conn, now) -> int:
    if args.command == "research":
        result = research.plan(config, now, args.topic)
    elif args.command == "ingest":
        result = research.ingest(conn, config, _load_json(args.input), now)
    elif args.command == "shortlist":
        result = research.shortlist(conn, config, now, args.limit)
    elif args.command == "evidence":
        result = evidence.collect(conn, config, args.ids, now, refresh=args.refresh)
    elif args.command == "briefing":
        if args.html.exists():
            raise ConfigError("HTML export path already exists; choose a new path")
        result = (briefing.save(conn, config, _load_json(args.input), now) if args.input
                  else briefing.read(conn, args.briefing_id))
        try:
            with args.html.open("x", encoding="utf-8") as handle:
                handle.write(briefing.html_document(result["content"], now))
        except OSError as exc:
            raise ConfigError(f"could not export HTML: {exc}; draft {result['briefing_id']} is retained") from exc
        # Only Highlights are channel text; detailed stories stay in the attachment.
        result = {key: result[key] for key in
                  ("ok", "briefing_id", "created_at", "committed_at", "totals", "body")}
        result["html_path"] = str(args.html.resolve())
    elif args.command == "commit":
        result = briefing.commit(conn, config, args.briefing_id, now)
    else:
        result = research.dismiss(conn, config, args.ids, args.reason, now)
    _emit(result)
    return int(ExitCode.OK) if result["ok"] else int(ExitCode.ERR_FETCH)


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

    if any(source.kind == "search" for source in sources):
        raise ConfigError("search workflow: run research, use Hermes web search, then ingest")
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
        "lookback_days": config.lookback_days,
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
