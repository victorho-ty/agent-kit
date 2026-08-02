"""JSON-in / JSON-out command line. Every subcommand prints one JSON object."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import categories, config, db, ingest, queries, report


def _month_default() -> str:
    return datetime.now(config.timezone()).strftime("%Y-%m")


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="expense_tracker", description="Household expense tracker tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create the database and seed the keyword mapping.")

    add = sub.add_parser("add", help="Parse and store one Telegram message.")
    add.add_argument("--member", required=True, help="Sender name, handle or id.")
    add.add_argument("--text", required=True, help="Raw message text, e.g. 'haircut $300; dinner $50'.")
    add.add_argument("--timestamp", help="ISO8601 or unix epoch of the inbound message. Defaults to now.")
    add.add_argument("--message-id", help="Telegram message id; makes the call idempotent.")
    add.add_argument("--currency", help=f"Defaults to {config.DEFAULT_CURRENCY}.")

    learn = sub.add_parser("learn", help="Persist keyword -> category decisions and backfill past rows.")
    learn.add_argument("--map", required=True, help='JSON object, e.g. \'{"haircut": "Beauty"}\'.')
    learn.add_argument("--source", default="llm", choices=["llm", "user"])

    query = sub.add_parser("query", help="Totals for a month, optionally for one member.")
    query.add_argument("--month", help="YYYY-MM. Defaults to the current month.")
    query.add_argument("--member")
    query.add_argument("--top-days", type=int, default=0, help="Also return the N highest-spend days.")

    year = sub.add_parser("year", help="Monthly totals across a calendar year.")
    year.add_argument("--year", help="YYYY. Defaults to the current year.")
    year.add_argument("--member")

    listing = sub.add_parser("list", help="Individual expense rows, newest first.")
    listing.add_argument("--month")
    listing.add_argument("--member")
    listing.add_argument("--limit", type=int, default=50)

    image = sub.add_parser("report", help="Render the monthly PNG report.")
    image.add_argument("--month", help="YYYY-MM. Defaults to the current month.")
    image.add_argument("--member")
    image.add_argument("--out", help="Output path. Defaults to the report directory.")

    sub.add_parser("categories", help="Valid categories and the current keyword mapping.")
    sub.add_parser("unmapped", help="Stored items still waiting for a category.")

    alias = sub.add_parser("alias", help="Map a Telegram handle or id to a display name.")
    alias.add_argument("--alias", required=True)
    alias.add_argument("--member", required=True)

    delete = sub.add_parser("delete", help="Remove one expense row by id.")
    delete.add_argument("--id", type=int, required=True)

    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = db.connect()

    if args.command == "init":
        _emit({"ok": True, "db": str(config.db_path()), "categories": categories.CATEGORIES})

    elif args.command == "add":
        _emit(
            ingest.ingest_message(
                conn,
                member=args.member,
                text=args.text,
                timestamp=args.timestamp,
                message_id=args.message_id,
                currency=args.currency,
            )
        )

    elif args.command == "learn":
        try:
            pairs = json.loads(args.map)
        except json.JSONDecodeError as exc:
            _emit({"ok": False, "error": f"--map is not valid JSON: {exc}"})
            return 1
        if not isinstance(pairs, dict):
            _emit({"ok": False, "error": "--map must be a JSON object of keyword -> category"})
            return 1
        result = categories.learn(conn, pairs, source=args.source)
        result["recategorized"] = db.recategorize(conn)
        result["ok"] = not result["rejected"]
        if result["rejected"]:
            result["valid_categories"] = categories.CATEGORIES
        _emit(result)

    elif args.command == "query":
        month = args.month or _month_default()
        payload = queries.month_summary(conn, month, args.member)
        if args.top_days:
            payload["top_days"] = queries.top_days(conn, month, args.member, limit=args.top_days)
        _emit(payload)

    elif args.command == "year":
        year = args.year or datetime.now(config.timezone()).strftime("%Y")
        months = queries.year_months(conn, year, args.member)
        _emit(
            {
                "year": year,
                "member": args.member,
                "months": months,
                "total": round(sum(m["total"] for m in months), 2),
            }
        )

    elif args.command == "list":
        _emit(
            {
                "month": args.month,
                "member": args.member,
                "expenses": queries.list_expenses(conn, args.month, args.member, args.limit),
            }
        )

    elif args.command == "report":
        month = args.month or _month_default()
        path = report.build_report(conn, month, args.member, args.out)
        summary = queries.month_summary(conn, month, args.member)
        _emit({"ok": True, "image_path": str(path), "month": month, "member": args.member, "total": summary["total"]})

    elif args.command == "categories":
        _emit({"categories": categories.CATEGORIES, "mapping": categories.load_mapping(conn)})

    elif args.command == "unmapped":
        _emit({"unmapped": db.unmapped(conn), "valid_categories": categories.CATEGORIES})

    elif args.command == "alias":
        db.set_alias(conn, args.alias, args.member)
        _emit({"ok": True, "alias": args.alias, "member": args.member})

    elif args.command == "delete":
        removed = db.delete_expense(conn, args.id)
        _emit({"ok": removed, "id": args.id})
        return 0 if removed else 1

    return 0


def main() -> None:
    sys.exit(run())
