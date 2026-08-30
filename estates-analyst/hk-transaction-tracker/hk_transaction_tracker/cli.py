"""The command surface. One JSON object per command, on stdout.

Every command prints exactly one JSON object and exits with a code from
:class:`hk_transaction_tracker.errors.ExitCode`. The agent parses that object
and branches on the exit code; it never reads stderr and never pattern-matches
a sentence.

The one deliberate exception is ``pending --count``, which prints a bare integer
so a shell can test it without a JSON parser. That is the gate the cron entry
uses, and the reason the daily check costs no tokens::

    hk-tx check >/dev/null
    [ "$(hk-tx pending --count)" -gt 0 ] && hermes cron run <report-job>

Everything before the last line runs on a timer. Only the last line wakes the
agent, and only on a day something actually transacted.

Nothing here updates or deletes a stored transaction. ``check`` appends,
``report --commit`` stamps the delivery ledger, and every other command is a
read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import check as check_module
from . import clock, db, report, settings, trend
from .config import load_config
from .errors import ExitCode, NotFoundError, TrackerError
from .models import DEAL_TYPES


def _utf8_output() -> None:
    """Make stdout and stderr carry Chinese, whatever the console's codepage is.

    Every payload here contains 屋苑 names, 間隔 labels and 呎價 headings, and
    ``ensure_ascii=False`` keeps them readable rather than escaping them into
    ``\\u6cd3``. On a console still defaulting to cp1252 -- a Windows box, a bare
    cron shell -- that print raises UnicodeEncodeError and the command dies with
    an empty stdout and a traceback, which reads to the agent as the command
    having crashed rather than as the terminal being unable to show the answer.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            # Already wrapped, or not a real stream. The payload may lose its
            # Chinese on such a terminal, but nothing here should die of it.
            pass


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return int(ExitCode.OK)


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


# ------------------------------------------------------------------- commands


def cmd_check(args) -> int:
    config = load_config()
    return _emit(check_module.check(config, args.estate, dry_run=args.dry_run))


def cmd_pending(args) -> int:
    conn = db.connect()
    try:
        rows = db.pending(conn)
        if args.count:
            print(len(rows))
            return int(ExitCode.OK)
        by_side: dict = {}
        for row in rows:
            key = (row["deal_type"], row["estate"])
            by_side[key] = by_side.get(key, 0) + 1
        return _emit({
            "ok": True,
            "pending": len(rows),
            "by_bucket": [
                {"deal_type": deal, "estate": estate, "count": count}
                for (deal, estate), count in sorted(by_side.items())
            ],
            "oldest": rows[-1]["ins_date"] if rows else None,
            "newest": rows[0]["ins_date"] if rows else None,
        })
    finally:
        conn.close()


def cmd_report(args) -> int:
    config = load_config()
    return _emit(report.build(
        config,
        commit=args.commit,
        limit=args.limit,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        draw=not args.no_images,
    ))


def cmd_history(args) -> int:
    config = load_config()
    return _emit(report.history(
        config, args.estate, args.deal,
        months=args.months,
        limit=args.limit,
        draw=args.chart,
        out_dir=Path(args.out_dir) if args.out_dir else None,
    ))


def cmd_trend(args) -> int:
    config = load_config()
    conn = db.connect()
    today = clock.today()
    try:
        held = db.buckets(conn)
        wanted = [
            row for row in held
            if (not args.estate or row["estate"] in args.estate)
            and (not args.deal or row["deal_type"] == args.deal)
        ]
        if not wanted:
            raise NotFoundError(
                "nothing recorded for that estate or deal type yet",
                recorded=[f"{row['estate']}/{row['deal_type']}" for row in held],
            )
        trends = []
        for row in wanted:
            entry = config.entry(row["estate"])
            trends.append(trend.bucket_trend(
                conn, row["estate"], row["deal_type"], today,
                window_days=config.trend_window_days,
                min_samples=config.trend_min_samples,
                label=entry.display if entry else row["estate"],
            ))
        return _emit({
            "ok": True,
            "as_of": today.isoformat(),
            "trends": trends,
            "summary_lines": [trend.summarise(item) for item in trends],
        })
    finally:
        conn.close()


def cmd_transactions(args) -> int:
    conn = db.connect()
    try:
        rows = db.query(
            conn,
            estate=args.estate,
            deal_type=args.deal,
            since=args.since,
            until=args.until,
            bedrooms=args.bedrooms,
            matched=None if args.all else True,
            limit=args.limit,
        )
        return _emit({
            "ok": True,
            "count": len(rows),
            "filters": {
                "estate": args.estate, "deal_type": args.deal,
                "since": args.since, "until": args.until,
                "bedrooms": args.bedrooms, "matched_only": not args.all,
            },
            "transactions": [report.row_payload(row) for row in rows],
        })
    finally:
        conn.close()


def cmd_estates(args) -> int:
    config = load_config()
    conn = db.connect()
    try:
        state = db.all_estate_state(conn)
        held: dict = {}
        for row in db.buckets(conn):
            held.setdefault(row["estate"], []).append({
                key: row[key] for key in ("deal_type", "total", "priced", "earliest", "latest")
            })
        return _emit({
            "ok": True,
            "config_path": str(config.path),
            "db_path": str(settings.db_path()),
            "timezone": config.timezone_name,
            "fetch_size": config.fetch_size,
            "trend": {
                "window_days": config.trend_window_days,
                "min_samples": config.trend_min_samples,
                "chart_months": config.chart_months,
                "chart_min_points": config.chart_min_points,
            },
            "estates": [
                {
                    **entry.to_dict(),
                    "state": state.get(entry.name),
                    "archive": held.get(entry.name, []),
                }
                for entry in config.estates
            ],
        })
    finally:
        conn.close()


def cmd_runs(args) -> int:
    conn = db.connect()
    try:
        return _emit({
            "ok": True,
            "db_path": str(settings.db_path()),
            "runs": db.recent_runs(conn, args.limit),
            "consecutive_failures": db.consecutive_failures(conn),
            "pending": db.pending_count(conn),
        })
    finally:
        conn.close()


# ---------------------------------------------------------------- the parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hk-tx",
        description="Track Centanet 成交 for configured Hong Kong estates.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check", help="fetch every configured estate and record what is new (writes; says nothing)"
    )
    check_parser.add_argument(
        "--estate", action="append",
        help="only this estate, by config name; repeatable. Includes disabled entries.",
    )
    check_parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch and judge without writing -- see what a new entry's criteria catch",
    )
    check_parser.set_defaults(func=cmd_check)

    pending_parser = subparsers.add_parser(
        "pending", help="how many matched transactions are waiting to be reported"
    )
    pending_parser.add_argument(
        "--count", action="store_true", help="print a bare integer, for the cron gate"
    )
    pending_parser.set_defaults(func=cmd_pending)

    report_parser = subparsers.add_parser(
        "report", help="the grouped summary of everything not yet reported, with images"
    )
    report_parser.add_argument(
        "--commit", action="store_true",
        help="stamp the transactions as reported before returning them",
    )
    report_parser.add_argument("--limit", type=_positive, help="at most this many transactions")
    report_parser.add_argument("--out-dir", help="write the images here instead of the state dir")
    report_parser.add_argument(
        "--no-images", action="store_true", help="text only; skip matplotlib entirely"
    )
    report_parser.set_defaults(func=cmd_report)

    history_parser = subparsers.add_parser(
        "history", help="past numbers for one estate on one side of the market"
    )
    history_parser.add_argument("--estate", required=True, help="the config name")
    history_parser.add_argument("--deal", required=True, choices=DEAL_TYPES)
    history_parser.add_argument("--months", type=_positive, help="months of monthly medians")
    history_parser.add_argument(
        "--limit", type=_positive, default=20, help="recent transactions to list (default 20)"
    )
    history_parser.add_argument("--chart", action="store_true", help="also draw the line chart")
    history_parser.add_argument("--out-dir", help="write the chart here instead of the state dir")
    history_parser.set_defaults(func=cmd_history)

    trend_parser = subparsers.add_parser(
        "trend", help="呎價(實) direction and change for every recorded bucket"
    )
    trend_parser.add_argument("--estate", action="append", help="limit to this estate; repeatable")
    trend_parser.add_argument("--deal", choices=DEAL_TYPES)
    trend_parser.set_defaults(func=cmd_trend)

    transactions_parser = subparsers.add_parser(
        "transactions", help="the archive, filtered (read-only)"
    )
    transactions_parser.add_argument("--estate")
    transactions_parser.add_argument("--deal", choices=DEAL_TYPES)
    transactions_parser.add_argument("--since", help="ISO date, on 成交日期")
    transactions_parser.add_argument("--until", help="ISO date, on 成交日期")
    transactions_parser.add_argument("--bedrooms", type=int)
    transactions_parser.add_argument(
        "--all", action="store_true",
        help="include transactions that did not meet the entry's criteria",
    )
    transactions_parser.add_argument("--limit", type=_positive, default=30)
    transactions_parser.set_defaults(func=cmd_transactions)

    estates_parser = subparsers.add_parser(
        "estates", help="validate the config and show each entry's criteria and health"
    )
    estates_parser.set_defaults(func=cmd_estates)

    runs_parser = subparsers.add_parser("runs", help="recent checks -- the liveness surface")
    runs_parser.add_argument("--limit", type=_positive, default=5)
    runs_parser.set_defaults(func=cmd_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except TrackerError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, default=str))
        return int(exc.exit_code)


if __name__ == "__main__":
    sys.exit(main())
