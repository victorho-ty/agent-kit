"""The command surface. One JSON object per command, on stdout.

Every command prints exactly one JSON object and exits with a code from
:class:`fed_watch.errors.ExitCode`. The agent parses that object and branches on
the exit code; it never reads stderr and never pattern-matches a sentence.

The one deliberate exception is ``check-changes --quiet``, which prints a bare
``0`` or ``1`` so a shell can gate on it without a JSON parser:

    [ "$(fedctl check-changes --quiet)" -eq 1 ] && hermes-run fed-watch-alert

Everything above that line runs on a timer and costs nothing. Only the last part
wakes the agent, and only when something actually moved.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import changes as changes_module
from . import charts, clock, db, settings, snapshot
from .config import fomc
from .errors import ExitCode, FedWatchError, InsufficientDataError


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return int(ExitCode.OK)


def _charts_for(history: list[dict], limit: int) -> tuple[list[dict], list[dict]]:
    """One chart per meeting, plus the meetings too short to plot."""
    charts.sweep()
    shaped = changes_module.series(history[-limit:])
    rendered, skipped = [], []
    for meeting_date in sorted(shaped):
        entry = shaped[meeting_date]
        try:
            rendered.append(charts.history(meeting_date, entry["timestamps"], entry["series"]))
        except FedWatchError as exc:
            skipped.append({"meeting_date": meeting_date, "reason": exc.message})
    return rendered, skipped


# --------------------------------------------------------------------- commands


def cmd_snap(args) -> int:
    """Read the market now and write one row of history."""
    with db.connect() as conn:
        return _emit(snapshot.take(conn, meetings_tracked=args.meetings))


def cmd_check_changes(args) -> int:
    """Snap, then report what moved since the last time anything was reported."""
    now = clock.now()
    threshold = args.threshold if args.threshold is not None else settings.change_threshold()

    with db.connect() as conn:
        baseline = db.latest_reported(conn)

        if args.no_fetch:
            row = conn.execute(
                "SELECT * FROM snapshots ORDER BY taken_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise InsufficientDataError(
                    "no stored snapshot to compare; run `fedctl snap` first"
                )
            current = db.load_snapshot(conn, row["id"])
            taken = current["taken_at"]
        else:
            fresh = snapshot.take(conn, now=now, meetings_tracked=args.meetings)
            current = db.load_snapshot(conn, fresh["snapshot_id"])
            taken = fresh["taken_at"]

        report = changes_module.diff(baseline, current, threshold)

        if args.quiet:
            print(1 if report["changed"] else 0)
            if report["changed"]:
                db.mark_reported(conn, current["id"], now)
            return int(ExitCode.OK)

        payload = {
            "ok": True,
            "taken_at": taken,
            "fetched": not args.no_fetch,
            "price_source": current["price_source"],
            "attribution": snapshot.ATTRIBUTION,
            "policy": {
                "effr": current["effr"],
                "as_of": current["effr_as_of"],
                "target_range": f"{current['target_low']:.2f}-{current['target_high']:.2f}%",
            },
            "changes": report,
            "current": [snapshot.stored_meeting_payload(m) for m in current["meetings"]],
        }

        if not report["changed"]:
            payload["quiet"] = True
            return _emit(payload)

        # Stamped only now. The baseline must not move on a quiet poll, or a
        # slow drift is never measured against where it started.
        db.mark_reported(conn, current["id"], now)
        history = db.reported_history(conn, args.limit)
        payload["charts"], payload["charts_skipped"] = _charts_for(history, args.limit)
        payload["reported_changes_plotted"] = len(history)
        return _emit(payload)


def cmd_history(args) -> int:
    """On demand: the last N reported changes, summarised and charted."""
    with db.connect() as conn:
        history = db.reported_history(conn, args.limit)
        if not history:
            raise InsufficientDataError(
                "no reported changes stored yet; `fedctl check-changes` records them",
                stored_snapshots=db.count_snapshots(conn),
            )

        latest = history[-1]
        payload = {
            "ok": True,
            "now": clock.now().isoformat(),
            "requested": args.limit,
            "reported_changes": len(history),
            "from": history[0]["taken_at"],
            "to": latest["taken_at"],
            "price_source": latest["price_source"],
            "attribution": snapshot.ATTRIBUTION,
            "policy": {
                "effr": latest["effr"],
                "as_of": latest["effr_as_of"],
                "target_range": f"{latest['target_low']:.2f}-{latest['target_high']:.2f}%",
            },
            "latest": [snapshot.stored_meeting_payload(m) for m in latest["meetings"]],
        }
        if len(history) > 1:
            payload["net_change"] = changes_module.diff(history[0], latest, 0.0)
        payload["charts"], payload["charts_skipped"] = _charts_for(history, args.limit)
        return _emit(payload)


def cmd_meetings(args) -> int:
    """The tracked calendar, and which contract answers each meeting."""
    calendar = fomc.load()
    today = clock.today()
    upcoming = fomc.upcoming(calendar, today, args.count)

    def month_has_meeting(year: int, month: int) -> bool:
        return fomc.month_has_meeting(calendar, year, month)

    from . import probabilities

    def tracked(ordinal: int, meeting) -> dict:
        method, contract = probabilities.plan(meeting, month_has_meeting)
        return {
            "meeting_date": meeting.isoformat(),
            "ordinal": ordinal,
            "method": method,
            "contract": contract,
            # A clear following month is read directly, so nothing is amplified.
            "amplification": (
                round(probabilities.amplification(meeting), 2)
                if method == "blend_inversion"
                else 1.0
            ),
        }

    ahead = [meeting for meeting in calendar if meeting >= today]
    payload = {
        "ok": True,
        "today": today.isoformat(),
        "known_through": calendar[-1].isoformat(),
        "meetings_ahead": len(ahead),
        "tracked": [tracked(ordinal, meeting) for ordinal, meeting in enumerate(upcoming, 1)],
    }
    if len(ahead) < fomc.REFRESH_WARNING_THRESHOLD:
        payload["calendar_warning"] = (
            f"only {len(ahead)} FOMC date(s) left in fed_watch/config/fomc.json; "
            "add next year's from federalreserve.gov"
        )
    return _emit(payload)


# ----------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fedctl",
        description=(
            "FOMC target-rate probabilities from 30-Day Fed Funds futures. "
            "Every command prints one JSON object."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snap", help="read the market now and store one row of history")
    snap.add_argument(
        "--meetings",
        type=int,
        default=snapshot.MEETINGS_TRACKED,
        help="how many upcoming meetings to price (default 2)",
    )
    snap.set_defaults(func=cmd_snap)

    check = sub.add_parser(
        "check-changes",
        help="snap, then report what moved since the last reported change",
    )
    check.add_argument(
        "--threshold",
        type=float,
        help="percentage points a probability must move to count (default 4.0)",
    )
    check.add_argument(
        "--limit",
        type=int,
        default=settings.DEFAULT_CHANGE_WINDOW,
        help="how many reported changes the charts cover (default 10)",
    )
    check.add_argument(
        "--meetings", type=int, default=snapshot.MEETINGS_TRACKED
    )
    check.add_argument(
        "--no-fetch",
        action="store_true",
        help="diff the stored history without fetching; for replay and outages",
    )
    check.add_argument(
        "--quiet",
        action="store_true",
        help="print a bare 1 or 0 for a shell gate, and nothing else",
    )
    check.set_defaults(func=cmd_check_changes)

    history = sub.add_parser(
        "history", help="on demand: the last N reported changes, summarised and charted"
    )
    history.add_argument(
        "--limit",
        type=int,
        default=settings.DEFAULT_CHANGE_WINDOW,
        help="how many reported changes to cover (default 10)",
    )
    history.set_defaults(func=cmd_history)

    meetings = sub.add_parser("meetings", help="the calendar, and the contract behind each date")
    meetings.add_argument("--count", type=int, default=snapshot.MEETINGS_TRACKED)
    meetings.set_defaults(func=cmd_meetings)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FedWatchError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, default=str))
        return int(exc.exit_code)
    except BrokenPipeError:  # pragma: no cover - a closed stdout is not an error
        return int(ExitCode.OK)
    except Exception as exc:  # pragma: no cover - last resort, still valid JSON
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "ERR_UNEXPECTED",
                    "exit_code": int(ExitCode.ERR_CONFIG),
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
        )
        return int(ExitCode.ERR_CONFIG)


if __name__ == "__main__":
    sys.exit(main())
