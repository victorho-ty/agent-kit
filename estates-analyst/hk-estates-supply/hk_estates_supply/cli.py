"""The command surface. One JSON object per command, on stdout.

Every command prints exactly one JSON object and exits with a code from
:class:`hk_estates_supply.errors.ExitCode`. The agent parses that object and
branches on the exit code; it never reads stderr and never pattern-matches a
sentence.

The one deliberate exception is ``pending --count``, which prints a bare integer
so a shell can test it without a JSON parser. That is the gate the cron entry
uses, and the reason the daily check costs no tokens:

    hk-supply check >/dev/null && \\
      [ "$(hk-supply pending --count)" -gt 0 ] && hermes-run hk-estates-supply-report

Everything before the last clause runs on a timer. Only the last clause wakes the
agent, and only in the quarter something was actually published.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import clock, history, report, settings, state
from .errors import ExitCode, SupplyError
from .render import COLUMNS


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return int(ExitCode.OK)


def _positive(text: str) -> int:
    """A row count that can actually be drawn.

    ``--quarters 0`` otherwise reaches the renderer as an empty table and comes
    back as ERR_RENDER, which sends the reader looking at fonts and directories
    for what is a typo in the command line.
    """
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


# ------------------------------------------------------------------- commands


def cmd_check(args) -> int:
    return _emit(report.check(download=not args.no_download))


def cmd_report(args) -> int:
    return _emit(
        report.build(
            quarter=args.quarter,
            commit=args.commit,
            limit=args.quarters,
            out_dir=Path(args.out_dir) if args.out_dir else None,
        )
    )


def cmd_pending(args) -> int:
    rows = history.read()
    # Seeds if this is the first thing ever run, so a fresh install reports zero
    # pending rather than every quarter in the file. `check` does the same.
    state.ensure_seeded(rows, clock.now())
    waiting = state.pending(rows)
    if args.count:
        print(len(waiting))
        return int(ExitCode.OK)
    return _emit({
        "ok": True,
        "pending": len(waiting),
        "pending_quarters": waiting,
        "latest_in_history": history.latest(rows).quarter,
        "reported": state.reported_quarters(),
    })


def cmd_history(args) -> int:
    rows = history.read()
    latest = history.latest(rows)
    return _emit({
        "ok": True,
        "path": str(settings.history_file()),
        "quarters": len(rows),
        "latest": latest.quarter,
        "columns": [
            {"key": key, "zh": chinese, "en": english} for key, chinese, english in COLUMNS
        ],
        "rows": history.table(rows, args.limit),
    })


def cmd_runs(args) -> int:
    rows = history.read()
    latest = history.latest(rows)
    return _emit({
        "ok": True,
        "path": str(settings.runs_file()),
        "runs": state.recent_runs(args.limit),
        "consecutive_failures": state.consecutive_failures(),
        "latest_in_history": latest.quarter,
        "next_expected": history.next_quarter(latest.quarter),
        "overdue": report.is_overdue(latest.quarter, clock.today()),
    })


def cmd_source(args) -> int:
    """What the index page says right now, without touching the history."""
    from . import fetch

    publication = fetch.latest_publication()
    rows = history.read()
    return _emit({
        "ok": True,
        **publication.as_dict(),
        "in_history": history.has_quarter(rows, publication.quarter),
        "latest_in_history": history.latest(rows).quarter,
    })


# --------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hk-supply",
        description="Hong Kong private residential primary-market supply monitor",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="daily: has a new quarter been published? writes the row if so, says nothing else",
    )
    check.add_argument(
        "--no-download", action="store_true",
        help="report what is published without downloading or parsing the PDF",
    )
    check.set_defaults(func=cmd_check)

    rep = sub.add_parser("report", help="render the images and return the report payload")
    rep.add_argument("--quarter", help="e.g. 2026/Jun (default: the newest in the history)")
    rep.add_argument("--commit", action="store_true",
                     help="stamp the quarter as delivered, so it stops being pending")
    rep.add_argument("--quarters", type=_positive, default=None,
                     help=f"rows in the table (default {settings.DEFAULT_TABLE_QUARTERS})")
    rep.add_argument("--out-dir", help="where to write the PNGs (default: the profile state dir)")
    rep.set_defaults(func=cmd_report)

    pend = sub.add_parser("pending", help="quarters recorded but never reported")
    pend.add_argument("--count", action="store_true",
                      help="print a bare integer instead of JSON, for the cron gate")
    pend.set_defaults(func=cmd_pending)

    hist = sub.add_parser("history", help="the recorded quarters, newest first, with QoQ")
    hist.add_argument("--limit", type=_positive, default=12)
    hist.set_defaults(func=cmd_history)

    runs = sub.add_parser("runs", help="the daily check's own log -- the liveness record")
    runs.add_argument("--limit", type=_positive, default=10)
    runs.set_defaults(func=cmd_runs)

    src = sub.add_parser("source", help="what the index page is publishing right now")
    src.set_defaults(func=cmd_source)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Every payload carries Chinese -- column headings, the page's own date
    # wording, the summary lines. A console that defaults to cp1252 would raise
    # UnicodeEncodeError while printing the answer, which looks exactly like the
    # command having failed.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SupplyError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, default=str))
        return int(exc.exit_code)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
