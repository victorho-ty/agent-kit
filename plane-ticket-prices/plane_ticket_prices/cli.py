"""Command-line interface for the plane-ticket-prices tools.

Every command prints exactly one JSON object on stdout. The skill parses it --
it never guesses at prices and never parses free text.

Commands:

    collect   -- crawl Google Flights for today's prices and store them
    report    -- render the daily PNG report(s)
    latest    -- cheapest price per grouping from the most recent run
    trend     -- the daily series for one scope (agent Q&A)
    runs      -- run history (freshness check / triage)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db
from .config.scope import load_scopes


def _tz() -> ZoneInfo:
    return ZoneInfo(__import__("os").environ.get("TICKET_PRICES_TZ", "Asia/Hong_Kong"))


def _today() -> str:
    return datetime.now(_tz()).date().isoformat()


def _default_scope_file() -> Path:
    override = __import__("os").environ.get("TICKET_PRICES_SCOPE_FILE")
    if override:
        return Path(override).expanduser()
    return Path(__file__).parent / "config" / "scope.json"


def _default_report_dir() -> Path:
    override = __import__("os").environ.get("TICKET_PRICES_REPORT_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "reports"


def _scopes(names: list[str]) -> list:
    return load_scopes(_default_scope_file(), names=names or None)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

def cmd_collect(args: argparse.Namespace) -> int:
    scopes = _scopes(args.scope)
    run_date = _today()
    dry_run = bool(args.dry_run)

    if dry_run:
        plan = []
        for scope in scopes:
            pairs = scope.date_pairs()
            plan.append({
                "scope": scope.name,
                "run_date": run_date,
                "pairs_planned": len(pairs),
                "pairs": [f"{d.isoformat()} -> {r.isoformat()}" for d, r in pairs],
                "filters": {"max_stops": scope.max_stops, "seat": scope.seat,
                            "currency": scope.currency, "adults": scope.adults},
            })
        _emit({"dry_run": True, "scopes": plan})
        return 0

    # Imported lazily so --dry-run works without Playwright/Chromium.
    from .crawler import Crawler

    conn = db.connect()
    results = []
    overall_status = "ok"

    with Crawler(headless=not bool(args.headful)) as crawler:
        for scope in scopes:
            pairs = scope.date_pairs()
            run_id = db.start_run(conn, scope.name, run_date, len(pairs))
            searches_used = 0
            rows_written = 0
            succeeded = 0
            failed = 0
            detail: dict = {"pair_failures": []}

            for depart, returnd in pairs:
                if args.max_searches and searches_used >= args.max_searches:
                    detail["budget_exhausted"] = True
                    detail["stopped_at"] = f"{depart.isoformat()} -> {returnd.isoformat()}"
                    failed += 1
                    continue
                try:
                    outcome = crawler.collect_pair(scope, depart, returnd, run_date)
                except Exception as exc:  # noqa: BLE001 -- record, keep going
                    outcome = {"pairs_ok": False, "cells": [], "itineraries": [],
                               "detail": f"exception: {exc}"}

                searches_used += int(outcome.get("searches", 1))
                if outcome["pairs_ok"]:
                    for cell in outcome["cells"]:
                        rows_written += db.upsert_cell(conn, cell)
                    for itinerary in outcome["itineraries"]:
                        db.upsert_itinerary(conn, itinerary)
                    succeeded += 1
                else:
                    failed += 1
                    detail["pair_failures"].append({
                        "pair": f"{depart.isoformat()} -> {returnd.isoformat()}",
                        "reason": outcome.get("detail"),
                    })

            status = "ok"
            if failed and succeeded:
                status = "partial"
            elif failed and not succeeded:
                status = "blocked" if not detail.get("budget_exhausted") else "partial"
            if detail.get("budget_exhausted"):
                status = "partial"

            db.finish_run(conn, run_id, status=status, pairs_succeeded=succeeded,
                          pairs_failed=failed, searches_used=searches_used,
                          rows_written=rows_written, detail=detail)
            if status != "ok":
                overall_status = "partial" if status == "partial" else "error"

            results.append({
                "scope": scope.name,
                "status": status,
                "run_date": run_date,
                "pairs_planned": len(pairs),
                "pairs_succeeded": succeeded,
                "pairs_failed": failed,
                "searches_used": searches_used,
                "rows_written": rows_written,
                "detail": detail,
            })

    _emit({"run_date": run_date, "status": overall_status, "scopes": results})
    return 0 if overall_status == "ok" else 1


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def cmd_report(args: argparse.Namespace) -> int:
    from .report import build_report_data, render_report

    conn = db.connect()
    scopes = _scopes(args.scope)
    out_dir = Path(args.out).expanduser() if args.out else _default_report_dir()
    images = []
    summary = {}

    for scope in scopes:
        dates = db.run_dates(conn, scope.name)
        if not dates:
            summary[scope.name] = {"error": "no data yet"}
            continue
        run_date = dates[-1]
        data = build_report_data(conn, scope.name, run_date, wow_days=args.wow_days)
        out_path = out_dir / f"{scope.name}_{run_date}.png"
        render_report(data, out_path, top_lines=args.top)
        images.append(str(out_path))
        summary[scope.name] = {
            "run_date": run_date,
            "images": [str(out_path)],
            "cheapest": data["rankings"][:5],
            "biggest_drops": [
                r for r in data["wow"] if r["delta"] is not None and r["delta"] < 0
            ][:5],
            "biggest_rises": [
                r for r in data["wow"] if r["delta"] is not None and r["delta"] > 0
            ][:5],
        }

    _emit({"images": images, "scopes": summary})
    return 0


# ---------------------------------------------------------------------------
# latest / trend / runs
# ---------------------------------------------------------------------------

def cmd_latest(args: argparse.Namespace) -> int:
    conn = db.connect()
    out = {}
    for scope in _scopes(args.scope):
        dates = db.run_dates(conn, scope.name)
        if not dates:
            out[scope.name] = {"error": "no data"}
            continue
        run_date = dates[-1]
        cells = db.latest_cells(conn, scope.name, run_date)
        out[scope.name] = {
            "run_date": run_date,
            "cheapest": [
                {k: row[k] for k in ("airline", "depart_date", "return_date",
                                     "dep_bucket", "ret_bucket", "min_price", "currency")}
                for row in sorted(cells, key=lambda r: r["min_price"])[: args.top]
            ],
            "cells": len(cells),
        }
    _emit(out)
    return 0


def cmd_trend(args: argparse.Namespace) -> int:
    conn = db.connect()
    out = {}
    for scope in _scopes(args.scope):
        series = db.cell_series(conn, scope.name, since=args.since)
        rows = []
        for row in series:
            rows.append({k: row[k] for k in ("run_date", "airline", "depart_date",
                                             "return_date", "dep_bucket", "ret_bucket",
                                             "min_price", "currency")})
        out[scope.name] = {"rows": rows, "count": len(rows)}
    _emit(out)
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    conn = db.connect()
    rows = conn.execute(
        "SELECT scope, run_date, started_at, finished_at, status, pairs_planned,"
        " pairs_succeeded, pairs_failed, searches_used, rows_written, detail"
        " FROM runs ORDER BY id DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    _emit({"runs": [dict(r) for r in rows]})
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plane-ticket-tracker",
        description="Google Flights round-trip price tracking tools.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="crawl today's prices and store them")
    p_collect.add_argument("--scope", action="append", default=[], help="scope name (repeatable)")
    p_collect.add_argument("--dry-run", action="store_true", help="print the plan, no browser/DB")
    p_collect.add_argument("--max-searches", type=int, default=None,
                           help="hard cap on page loads across the run")
    p_collect.add_argument("--headful", action="store_true", help="show the browser (debugging)")

    p_report = sub.add_parser("report", help="render the daily PNG report")
    p_report.add_argument("--scope", action="append", default=[])
    p_report.add_argument("--out", default=None, help="output directory (default: reports/)")
    p_report.add_argument("--wow-days", type=int, default=7, help="week-over-week window (default 7)")
    p_report.add_argument("--top", type=int, default=8, help="lines in the trend panel")

    p_latest = sub.add_parser("latest", help="cheapest per grouping, most recent run")
    p_latest.add_argument("--scope", action="append", default=[])
    p_latest.add_argument("--top", type=int, default=10)

    p_trend = sub.add_parser("trend", help="daily series for agent questions")
    p_trend.add_argument("--scope", action="append", default=[])
    p_trend.add_argument("--since", default=None, help="YYYY-MM-DD, inclusive")

    p_runs = sub.add_parser("runs", help="run history (freshness check)")
    p_runs.add_argument("--limit", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        handler = {"collect": cmd_collect, "report": cmd_report,
                   "latest": cmd_latest, "trend": cmd_trend, "runs": cmd_runs}[args.command]
        return handler(args)
    except Exception as exc:  # noqa: BLE001 -- JSON error surface for the agent
        _emit({"error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
