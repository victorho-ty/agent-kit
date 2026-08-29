"""JSON-in / JSON-out command line.

Every subcommand prints exactly one indented JSON object on stdout and exits 0
on success. A failure prints ``{"ok": false, "error": "ERR_...", ...}`` and exits
with the code from :class:`video_summary.errors.ExitCode`, so the agent branches
on a number and a closed enum rather than on a sentence.

``check`` is the cron entry and the only command that produces something to
send. ``mark`` is the other half of it: the ledger is stamped *after* a video
has actually reached Telegram, one video at a time, because a send can fail
halfway through a batch and a video that was never sent must come round again.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import check as check_run, clock, db, settings, transcript as transcript_tool
from .config import load_config
from .errors import ExitCode, NotFoundError, VideoSummaryError


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-summary",
        description="Watch YouTube channel feeds and hand new videos over with their transcripts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="Fetch every enabled feed, store what is new, fetch transcripts, "
             "and return the videos that should be sent.",
    )
    check.add_argument("--feed", action="append", dest="feeds", help="Repeatable. Defaults to all enabled.")
    check.add_argument("--limit", type=int, default=None,
                       help="Videos to hand over this run. Defaults to max_per_check.")
    check.add_argument("--dry-run", action="store_true",
                       help="Show what a feed would yield. Writes nothing, sends nothing.")
    check.add_argument("--seed", action="store_true",
                       help="Absorb what is published now without reporting it. Implied by a first check.")
    check.add_argument("--no-transcript", action="store_true",
                       help="Skip transcript fetching this run. For triaging a feed.")
    check.add_argument("--ignore-throttle", action="store_true",
                       help="Fetch even a feed inside its min_interval_minutes. For testing a feed.")

    feeds = sub.add_parser("feeds", aliases=["list"],
                           help="The configured feeds -- name, url and health.")
    feeds.add_argument("--all", action="store_true", help="Include disabled feeds.")

    mark = sub.add_parser("mark", help="Stamp videos as summarised, after they have been sent.")
    mark.add_argument("--video", action="append", dest="videos",
                      help="Repeatable. A YouTube video id or this database's row id.")
    mark.add_argument("--all", action="store_true",
                      help="Every pending video. Use only when the whole batch went out.")

    videos = sub.add_parser("videos", help="What has been seen, newest first.")
    videos.add_argument("--feed", action="append", dest="feeds")
    videos.add_argument("--pending", action="store_true", help="Only videos not yet summarised.")
    videos.add_argument("--summarised", action="store_true", help="Only videos already sent.")
    videos.add_argument("--since", help="ISO date or timestamp; filters on first_seen_at.")
    videos.add_argument("--limit", type=int, default=20)

    transcript = sub.add_parser("transcript", help="Where one video's transcript is, or fetch it now.")
    transcript.add_argument("--video", required=True, help="A YouTube video id or a row id.")
    transcript.add_argument("--refresh", action="store_true",
                            help="Fetch it again now. Fails loudly if there is none.")
    transcript.add_argument("--text", action="store_true",
                            help="Include the transcript text in the payload. Large -- prefer the path.")
    transcript.add_argument("--max-chars", type=int, default=20000,
                            help="With --text, how much to include. Default 20000.")

    runs = sub.add_parser("runs", help="Recent checks -- the liveness and triage surface.")
    runs.add_argument("--limit", type=int, default=5)

    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except VideoSummaryError as exc:
        _emit(exc.payload())
        return int(exc.exit_code)


def _dispatch(args) -> int:
    config = load_config()
    conn = db.connect()
    now = clock.now(config.tzinfo())

    if args.command == "check":
        return _cmd_check(args, config, conn, now)
    if args.command in ("feeds", "list"):
        return _cmd_feeds(args, config, conn, now)
    if args.command == "mark":
        return _cmd_mark(args, config, conn, now)
    if args.command == "videos":
        return _cmd_videos(args, config, conn)
    if args.command == "transcript":
        return _cmd_transcript(args, config, conn)
    if args.command == "runs":
        _emit({"ok": True, "runs": db.recent_runs(conn, args.limit), **db.pending_count(conn)})
        return int(ExitCode.OK)
    return int(ExitCode.OK)


def _cmd_check(args, config, conn, now) -> int:
    feeds = config.select(args.feeds)
    if not feeds:
        _emit({
            "ok": True,
            "status": "skipped",
            "reason": "no_enabled_feeds",
            "message": f"{config.path} has no enabled feeds matching the request; nothing to check",
            "videos": [],
            **db.pending_count(conn),
        })
        return int(ExitCode.OK)

    result = check_run.check(
        conn, config, feeds, now,
        seed=args.seed,
        dry_run=args.dry_run,
        ignore_throttle=args.ignore_throttle,
        with_transcripts=not args.no_transcript,
        limit=args.limit,
    )
    _emit(result)
    return int(ExitCode.OK) if result["ok"] else int(ExitCode.ERR_FETCH)


def _cmd_feeds(args, config, conn, now) -> int:
    """The configured feeds. Requirement one of the skill: name and url, plainly."""
    state = db.all_feed_state(conn)
    counts = db.feed_counts(conn)
    _emit({
        "ok": True,
        "config": str(config.path),
        "db": str(settings.db_path()),
        "transcript_dir": str(settings.transcript_dir()),
        "timezone": config.timezone_name,
        "now": now.isoformat(),
        "max_per_check": config.max_per_check,
        "summary_char_cap": config.summary_char_cap,
        "transcript_languages": list(config.transcript_languages),
        "transcript_grace_minutes": config.transcript_grace_minutes,
        "exclude": list(config.exclude_keywords),
        "feeds": [
            {
                **feed.to_dict(),
                "seeded": bool(state.get(feed.name, {}).get("seeded")),
                "last_ok_at": state.get(feed.name, {}).get("last_ok_at"),
                "consecutive_failures": state.get(feed.name, {}).get("consecutive_failures", 0),
                "last_error": state.get(feed.name, {}).get("last_error"),
                "recent_yield": state.get(feed.name, {}).get("recent_yield", 0),
                "videos_seen": counts.get(feed.name, {}).get("videos", 0),
                "videos_pending": counts.get(feed.name, {}).get("pending", 0),
                "latest_seen_at": counts.get(feed.name, {}).get("latest_seen_at"),
                "next_eligible": _next_eligible(state.get(feed.name), feed, now),
            }
            for feed in config.select(include_disabled=args.all)
        ],
        **db.pending_count(conn),
    })
    return int(ExitCode.OK)


def _cmd_mark(args, config, conn, now) -> int:
    if not args.videos and not args.all:
        raise NotFoundError("mark needs --video <id> (repeatable) or --all")

    if args.all:
        wanted = [video.video_id for video in db.pending_videos(conn)]
    else:
        wanted = []
        for reference in args.videos:
            video = db.resolve_video(conn, reference)
            if video is None:
                raise NotFoundError(f"no video matching {reference!r}", reference=reference)
            wanted.append(video.video_id)

    stamped = db.mark_summarised(conn, wanted, now)
    _emit({
        "ok": True,
        "marked": stamped,
        # Asked for but already stamped. Not an error: a repeated mark after a
        # retried send is exactly right, and saying so beats failing.
        "already_marked": [video_id for video_id in wanted if video_id not in stamped],
        **db.pending_count(conn),
    })
    return int(ExitCode.OK)


def _cmd_videos(args, config, conn) -> int:
    names = None
    if args.feeds:
        names = [feed.name for feed in config.select(args.feeds, include_disabled=True)]
    state = "pending" if args.pending else "summarised" if args.summarised else None
    rows = db.recent_videos(conn, feeds=names, since=args.since, state=state, limit=args.limit)
    _emit({
        "ok": True,
        "count": len(rows),
        "videos": [row.to_dict() for row in rows],
        **db.pending_count(conn),
    })
    return int(ExitCode.OK)


def _cmd_transcript(args, config, conn) -> int:
    video = db.resolve_video(conn, args.video)
    if video is None:
        raise NotFoundError(f"no video matching {args.video!r}", reference=args.video)

    if args.refresh:
        result = transcript_tool.fetch_or_raise(
            video.video_id,
            languages=config.transcript_languages,
            title=video.title,
            url=video.url,
        )
        db.record_transcript(conn, video.video_id, result)
        video = db.resolve_video(conn, video.video_id)

    payload = {"ok": True, **video.to_dict()}
    if args.text:
        payload["text"] = _read_transcript(video, args.max_chars)
    _emit(payload)
    return int(ExitCode.OK)


def _read_transcript(video, max_chars: int) -> str | None:
    if not video.transcript_path:
        return None
    try:
        with open(video.transcript_path, encoding="utf-8") as handle:
            return handle.read(max_chars)
    except OSError:
        return None


def _next_eligible(state, feed, now) -> str | None:
    ready = db.throttled_until(state, feed.min_interval_minutes, now)
    return ready.isoformat() if ready else None


def main() -> None:
    sys.exit(run())
