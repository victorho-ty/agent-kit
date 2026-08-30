"""``quotesctl`` -- JSON in, JSON out. The only way the agent touches the store.

Every subcommand prints exactly one indented JSON object on stdout and exits 0
on success. A failure prints ``{"ok": false, "code": "ERR_...", ...}`` and exits
with the code from :class:`quotes_drill.errors.ExitCode`, so the agent branches
on a number and a closed enum rather than on a sentence.

`next` and `record` are the two halves of a drill, and they are deliberately
separate: `next` writes nothing, so a drill nobody answers -- a cron that fired
while the operator was asleep -- leaves the entry exactly as due as it was.
`record` is what costs an entry its turn, and it happens after the answer has
been judged.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import clock, db, schedule, settings, stats as stats_mod, store
from .config import styles as styles_config
from .errors import ExitCode, QuotesDrillError, UsageError


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quotesctl",
        description="Store quotes, vocabulary and phrases, and pick what to drill next.",
    )
    parser.add_argument("--db", help=f"Database path. Default {settings.DEFAULT_DB_PATH}.")
    parser.add_argument(
        "--now", help="ISO timestamp to use as the current time. For tests and replay."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Store one quote, word or phrase.")
    add.add_argument("--text", required=True)
    add.add_argument("--category", required=True, help="The agent's label: Food, Joke, Empathy...")
    add.add_argument("--kind", default="quote", choices=store.KINDS)
    add.add_argument("--source", help="Who said it, or where it came from.")
    add.add_argument("--note", help="Register, trap, or why it is worth owning.")

    bulk = sub.add_parser(
        "import",
        help="Store many at once from a JSON array. Rejects the whole batch on any bad item.",
    )
    bulk.add_argument("--file", required=True, help="Path to the JSON file, or - for stdin.")

    nxt = sub.add_parser("next", help="What to drill now. Writes nothing.")
    nxt.add_argument("--count", type=int, default=1)
    nxt.add_argument("--category", help="Restrict the queue to one category.")
    nxt.add_argument("--no-style", action="store_true", help="Skip the style assignment.")

    record = sub.add_parser("record", help="Log one judged answer and advance the entry.")
    record.add_argument("--entry", type=int, required=True)
    record.add_argument("--score", type=int, required=True, help="0 to 5, see references/judging.md.")
    record.add_argument("--transcript", help="What the speaker actually said.")
    record.add_argument("--feedback", help="The coaching line, as it was spoken back.")
    record.add_argument("--error-kind", choices=store.ERROR_KINDS)
    record.add_argument("--style", help="The style the drill asked for.")

    listing = sub.add_parser("list", help="Entries in queue order.")
    listing.add_argument("--category")
    listing.add_argument("--status", default="active", choices=(*store.STATUSES, "any"))
    listing.add_argument("--limit", type=int, default=20)

    show = sub.add_parser("show", help="One entry and its recent attempts.")
    show.add_argument("--entry", type=int, required=True)
    show.add_argument("--attempts", type=int, default=5)

    edit = sub.add_parser("edit", help="Fix an entry's material, or retire it.")
    edit.add_argument("--entry", type=int, required=True)
    edit.add_argument("--text")
    edit.add_argument("--category")
    edit.add_argument("--kind", choices=store.KINDS)
    edit.add_argument("--source")
    edit.add_argument("--note")
    edit.add_argument("--status", choices=store.STATUSES)

    stats = sub.add_parser("stats", help="Counts, day streak, and what keeps going wrong.")
    stats.add_argument("--weakest", type=int, default=5)

    styles = sub.add_parser("styles", help="The configured speaking styles.")
    styles.add_argument("--category")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except QuotesDrillError as exc:
        _emit(exc.payload())
        return int(exc.exit_code)


def _open(args):
    return db.connect(args.db or settings.db_path())


def _now(args) -> datetime:
    tz = settings.timezone()
    return clock.parse(args.now, tz) if args.now else clock.now(tz)


def cmd_add(args) -> int:
    conn = _open(args)
    entry, created = store.add_entry(
        conn,
        _now(args),
        text=args.text,
        category=args.category,
        kind=args.kind,
        source=args.source,
        note=args.note,
    )
    _emit({"ok": True, "created": created, "entry": entry.as_dict()})
    return int(ExitCode.OK)


def cmd_import(args) -> int:
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError(f"input is not valid JSON: {exc}")

    items = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise UsageError('expected a non-empty JSON array, or {"entries": [...]}')

    # The batch is validated in full before anything is written: a
    # half-imported batch is worse than a rejected one, because nobody knows
    # which half landed.
    cleaned = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise UsageError(f"entry {index} is not an object", {"index": index})
        if not item.get("text") or not item.get("category"):
            raise UsageError(f"entry {index} needs text and category", {"index": index})
        kind = item.get("kind", "quote")
        if kind not in store.KINDS:
            raise UsageError(
                f"entry {index} has kind {kind!r}; expected one of {', '.join(store.KINDS)}",
                {"index": index},
            )
        cleaned.append(
            {
                "text": item["text"],
                "category": item["category"],
                "kind": kind,
                "source": item.get("source"),
                "note": item.get("note"),
            }
        )

    conn = _open(args)
    now = _now(args)
    added = duplicates = 0
    rows = []
    for item in cleaned:
        entry, created = store.add_entry(conn, now, **item)
        added += created
        duplicates += not created
        rows.append({"id": entry.id, "text": entry.text, "created": created})

    _emit({"ok": True, "added": added, "duplicates": duplicates, "entries": rows})
    return int(ExitCode.OK)


def cmd_next(args) -> int:
    conn = _open(args)
    now = _now(args)
    picks, pool = schedule.pick(
        conn,
        now,
        count=max(1, args.count),
        category=args.category,
        cooldown_hours=settings.cooldown_hours(),
    )
    styleset = None if args.no_style else styles_config.load(settings.styles_path())

    items = []
    for picked in picks:
        entry = picked.entry
        recent = store.attempts_for(conn, entry.id, limit=1)
        items.append(
            {
                "entry": entry.as_dict(),
                "reason": picked.reason,
                "due": picked.due,
                "style": (
                    styleset.for_category(entry.category, entry.times_tested)
                    if styleset
                    else None
                ),
                "last_attempt": recent[0].as_dict() if recent else None,
            }
        )

    _emit(
        {
            "ok": True,
            "asked_at": clock.to_iso(now),
            "count": len(items),
            "pool": pool,
            "items": items,
        }
    )
    return int(ExitCode.OK)


def cmd_record(args) -> int:
    conn = _open(args)
    entry, interval_days = store.record_attempt(
        conn,
        _now(args),
        entry_id=args.entry,
        score=args.score,
        transcript=args.transcript,
        feedback=args.feedback,
        error_kind=args.error_kind,
        style=args.style,
    )
    _emit(
        {
            "ok": True,
            "entry": entry.as_dict(),
            "interval_days": interval_days,
            "next_due_at": entry.next_due_at,
        }
    )
    return int(ExitCode.OK)


def cmd_list(args) -> int:
    conn = _open(args)
    entries = store.list_entries(
        conn,
        category=args.category,
        status=None if args.status == "any" else args.status,
        limit=args.limit,
    )
    _emit(
        {
            "ok": True,
            "count": len(entries),
            "categories": store.categories(conn),
            "entries": [entry.as_dict() for entry in entries],
        }
    )
    return int(ExitCode.OK)


def cmd_show(args) -> int:
    conn = _open(args)
    entry = store.get_entry(conn, args.entry)
    attempts = store.attempts_for(conn, entry.id, limit=args.attempts)
    _emit(
        {
            "ok": True,
            "entry": entry.as_dict(),
            "attempts": [attempt.as_dict() for attempt in attempts],
        }
    )
    return int(ExitCode.OK)


def cmd_edit(args) -> int:
    conn = _open(args)
    entry = store.edit_entry(
        conn,
        _now(args),
        args.entry,
        text=args.text,
        category=args.category,
        kind=args.kind,
        source=args.source,
        note=args.note,
        status=args.status,
    )
    _emit({"ok": True, "entry": entry.as_dict()})
    return int(ExitCode.OK)


def cmd_stats(args) -> int:
    conn = _open(args)
    _emit({"ok": True, **stats_mod.collect(conn, _now(args), weakest=args.weakest)})
    return int(ExitCode.OK)


def cmd_styles(args) -> int:
    styleset = styles_config.load(settings.styles_path())
    if args.category:
        key = args.category.strip().casefold()
        _emit(
            {
                "ok": True,
                "category": args.category,
                "configured": key in styleset.categories,
                "styles": styleset.categories.get(key) or styleset.default,
            }
        )
    else:
        _emit({"ok": True, **styleset.as_dict()})
    return int(ExitCode.OK)


HANDLERS = {
    "add": cmd_add,
    "import": cmd_import,
    "next": cmd_next,
    "record": cmd_record,
    "list": cmd_list,
    "show": cmd_show,
    "edit": cmd_edit,
    "stats": cmd_stats,
    "styles": cmd_styles,
}
