"""One check: fetch the feeds, store what is new, get the words, hand over.

Adapted from news-radar/news_radar/scan.py, and it merges what were two
commands there into one. news-radar separated collecting from reporting because
its digest had to cluster items that arrive from different outlets at different
times. Nothing here clusters: a video is one video, and there is nothing to wait
for once its transcript exists. So there is one cron entry, and ``check``
returns the videos it wants sent.

The shape of a run:

1. **Per feed, in isolation.** One unreachable channel must never cost us the
   other nine, so every feed's failure is caught, recorded against that feed,
   and the run finishes ``partial``.
2. **Throttle first.** A feed inside its own ``min_interval_minutes`` is
   reported ``throttled`` and skipped. That is a floor on how often one feed may
   be fetched, not a schedule -- the cron entry is the schedule.
3. **Conditional GET next.** A ``304`` ends that feed's work immediately, which
   is what makes checking every two hours cost YouTube almost nothing.
4. **New videos only**, keyed on YouTube's own id, so a video carried by two
   configured feeds is stored -- and sent -- once.
5. **Cold start seeds, it does not shout.** A feed's first successful check
   stores its whole back catalogue already stamped. Without this, adding a
   channel would put fifteen old videos into the next message.
6. **Transcripts, then release.** A video with no captions *yet* is held back
   for ``transcript_grace_minutes`` rather than sent without a summary. After
   that it goes out anyway, saying why it has none.

Nothing here decides what to *say*. The payload is facts and a file path; the
sentence is the agent's.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timedelta

from . import db, feed as feed_parser, fetch, transcript as transcript_tool
from .errors import FetchError, VideoSummaryError


def _excluded(entry, keywords) -> str | None:
    """The one thing that drops a video outright.

    There is no *include* list anywhere in this skill: the operator already said
    what a channel is about by subscribing to it.
    """
    if not keywords:
        return None
    text = db.normalize(entry.text_for_filtering())
    for keyword in keywords:
        if keyword and db.normalize(keyword) in text:
            return keyword
    return None


def _age_minutes(first_seen_at: str, now: datetime) -> float:
    try:
        seen = datetime.fromisoformat(first_seen_at)
    except (TypeError, ValueError):
        return 0.0
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=now.tzinfo)
    return (now - seen).total_seconds() / 60.0


def _collect_feed(
    conn,
    config,
    feed,
    now,
    *,
    run_id,
    seed,
    dry_run,
    ignore_throttle,
    fetcher,
    resolver,
) -> dict:
    """Everything one feed contributes to a run. Raises only :class:`FetchError`."""
    state = db.ensure_feed(conn, feed.name, now) if not dry_run else db.feed_state(conn, feed.name)

    if not ignore_throttle:
        ready = db.throttled_until(state, feed.min_interval_minutes, now)
        if ready:
            return {
                "feed": feed.name,
                "status": "throttled",
                "next_eligible": ready.isoformat(),
                "entries_seen": 0,
                "videos_new": 0,
                "excluded": 0,
                "candidates": [],
            }

    response = fetcher(
        feed.url,
        etag=(state or {}).get("etag"),
        last_modified=(state or {}).get("last_modified"),
    )
    if response.not_modified:
        if not dry_run:
            db.record_feed_success(
                conn, feed.name, now,
                etag=response.etag, last_modified=response.last_modified, yield_count=0,
            )
        return {
            "feed": feed.name, "status": "unchanged", "entries_seen": 0,
            "videos_new": 0, "excluded": 0, "candidates": [],
        }

    entries = feed_parser.parse(response.text, feed.name, max_items=feed.max_items)

    # A moved or emptied feed looks exactly like a quiet month: both yield
    # nothing. `recent_yield` is what tells them apart.
    previous_yield = (state or {}).get("recent_yield", 0)
    if not entries and previous_yield:
        if not dry_run:
            db.record_feed_success(
                conn, feed.name, now,
                etag=response.etag, last_modified=response.last_modified, yield_count=0,
            )
        return {
            "feed": feed.name, "status": "zero_yield", "entries_seen": 0,
            "videos_new": 0, "excluded": 0, "candidates": [],
            "message": f"{feed.name} parsed but returned no entries; it returned "
                       f"{previous_yield} last time. Check the url.",
        }

    seeding = seed or not (state or {}).get("seeded")

    new_count, excluded_count, candidates = 0, 0, []
    # Oldest first, so ids ascend in publication order and a backlog drains the
    # way a person would expect to read it.
    for entry in reversed(entries):
        dropped = _excluded(entry, config.exclude_keywords)
        if dropped:
            excluded_count += 1
            continue
        if db.find_video(conn, entry.video_id):
            continue

        if dry_run:
            candidates.append({
                "video_id": entry.video_id,
                "title": entry.title,
                "url": entry.url,
                "thumbnail_url": entry.thumbnail_url,
                "published_text": entry.published_text,
                "would_send": not seeding,
            })
            new_count += 1
            continue

        kind = "unknown"
        if config.detect_shorts and not seeding:
            kind = feed_parser.kind_of(entry.video_id, resolver)
        db.insert_video(conn, entry, now, run_id=run_id, kind=kind, summarised=seeding)
        if not feed.transcript:
            db.set_transcript_status(conn, entry.video_id, "skipped")
        new_count += 1

    if not dry_run:
        db.record_feed_success(
            conn, feed.name, now,
            etag=response.etag, last_modified=response.last_modified,
            yield_count=len(entries), seeded=True,
        )

    return {
        "feed": feed.name,
        "status": "ok",
        "seeding": seeding,
        "entries_seen": len(entries),
        "videos_new": new_count,
        "excluded": excluded_count,
        "candidates": candidates,
    }


def _fill_transcripts(conn, config, now, *, budget: int, transcriber) -> dict:
    """Try the transcript for every pending video that could still get one."""
    ok = failed = attempted = 0
    for video in db.pending_videos(conn):
        if attempted >= budget:
            break
        if video.transcript_status in ("ok", "skipped"):
            continue
        if video.transcript_attempts >= config.max_transcript_attempts:
            continue
        attempted += 1
        result = transcriber(
            video.video_id,
            languages=config.transcript_languages,
            title=video.title,
            url=video.url,
        )
        db.record_transcript(conn, video.video_id, result)
        if result.ok:
            ok += 1
        else:
            failed += 1
    return {"transcripts_ok": ok, "transcripts_failed": failed, "transcripts_attempted": attempted}


def _release(conn, config, now, *, limit: int) -> tuple[list, int]:
    """Which pending videos go out now, and how many are still waiting.

    A video is released when there is something to summarise, when there never
    will be, or when we have waited long enough to stop pretending there might
    be. The grace period exists because YouTube generates captions *after* an
    upload: without it, every video posted in the last few minutes would be sent
    as a bare headline, which is the one thing this skill is meant to improve on.
    """
    ready, held = [], 0
    for video in db.pending_videos(conn):
        releasable = (
            video.transcript_status in ("ok", "skipped")
            or video.transcript_attempts >= config.max_transcript_attempts
            or _age_minutes(video.first_seen_at, now) >= config.transcript_grace_minutes
        )
        if not releasable:
            held += 1
            continue
        if len(ready) < limit:
            ready.append(video)
    return ready, held


def check(
    conn,
    config,
    feeds,
    now: datetime,
    *,
    seed: bool = False,
    dry_run: bool = False,
    ignore_throttle: bool = False,
    with_transcripts: bool = True,
    limit: int | None = None,
    fetcher=None,
    resolver=None,
    transcriber=None,
    sleeper=_time.sleep,
) -> dict:
    """Run one check and return the payload the agent acts on."""
    fetcher = fetcher or fetch.get
    resolver = resolver or fetch.resolved_url
    transcriber = transcriber or transcript_tool.fetch
    limit = config.max_per_check if limit is None else limit

    run_id = None if dry_run else db.start_run(conn, now)
    results, failures = [], []

    for index, feed in enumerate(feeds):
        if index and config.request_delay_seconds:
            sleeper(config.request_delay_seconds)
        try:
            results.append(_collect_feed(
                conn, config, feed, now,
                run_id=run_id, seed=seed, dry_run=dry_run, ignore_throttle=ignore_throttle,
                fetcher=fetcher, resolver=resolver,
            ))
        except VideoSummaryError as exc:
            reason = "fetch_failed" if isinstance(exc, FetchError) else "parse_failed"
            failures.append({"feed": feed.name, "reason": reason, "message": exc.message})
            results.append({
                "feed": feed.name, "status": "error", "entries_seen": 0,
                "videos_new": 0, "excluded": 0, "candidates": [],
            })
            if not dry_run:
                db.record_feed_failure(conn, feed.name, now, exc.message)

    zero_yield = [row["feed"] for row in results if row["status"] == "zero_yield"]
    failures.extend(
        {"feed": name, "reason": "zero_yield",
         "message": next(row.get("message", "") for row in results if row["feed"] == name)}
        for name in zero_yield
    )

    counts = {
        "feeds_checked": len(results),
        "entries_seen": sum(row["entries_seen"] for row in results),
        "videos_new": sum(row["videos_new"] for row in results),
        "videos_excluded": sum(row["excluded"] for row in results),
        "errors": len(failures),
    }

    transcript_counts = {"transcripts_ok": 0, "transcripts_failed": 0, "transcripts_attempted": 0}
    videos, held = [], 0
    if not dry_run:
        if with_transcripts:
            # Twice the send budget: enough to cover the ones going out now and
            # get a head start on the ones that will next time, without turning
            # a backlog into a burst of requests.
            transcript_counts = _fill_transcripts(
                conn, config, now, budget=max(limit * 2, 2), transcriber=transcriber
            )
        videos, held = _release(conn, config, now, limit=limit)

    counts.update({k: v for k, v in transcript_counts.items() if k != "transcripts_attempted"})

    if failures and len(failures) >= len(results):
        status = "error"
    elif failures:
        status = "partial"
    else:
        status = "ok"

    if not dry_run:
        db.finish_run(conn, run_id, now, status, counts)

    payload = {
        "ok": status != "error",
        "status": status,
        "run_id": run_id,
        "dry_run": dry_run,
        "now": now.isoformat(),
        "summary_char_cap": config.summary_char_cap,
        "feeds": results,
        "feed_failures": failures,
        "totals": {**counts, "transcripts_attempted": transcript_counts["transcripts_attempted"]},
        "videos": [_for_agent(video, config) for video in videos],
        "held_for_transcript": held,
        **db.pending_count(conn),
    }
    return payload


def _for_agent(video, config) -> dict:
    """One video as the agent receives it: facts, a link, and a transcript path.

    ``feed_note`` is the operator's own words about what the channel covers,
    carried through from the config so the model has the context a subscriber
    would have.
    """
    payload = video.to_dict()
    feed = config.feed(video.feed)
    payload["feed_note"] = feed.note if feed else None
    return payload
