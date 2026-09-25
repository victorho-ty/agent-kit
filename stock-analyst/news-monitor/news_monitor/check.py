"""One run: poll every enabled feed, store what is new, hand back what is unseen.

The shape of a run is fixed and short:

1. Fetch each enabled feed conditionally. A 304 costs nothing and is the normal
   answer for most feeds most of the time.
2. Insert entries whose fingerprint is not already in the database. The
   fingerprint is the article url, so two wires carrying one story insert once.
3. Return everything still pending, oldest first, up to a cap.

**A feed's first successful check absorbs its back catalogue silently.** Forty
stories that were already published when a feed was added are not news, and
sending them would teach the reader that this tool reports the past. They are
inserted pre-stamped, the feed is marked seeded, and the run reports how many
were absorbed so nobody mistakes the silence for a broken feed.

**A per-feed failure never aborts the run.** One publisher behind a CDN having
a bad minute must not cost the other twenty their headlines, so failures are
collected and the run finishes ``partial``.
"""

from __future__ import annotations

import time as _time
from datetime import datetime

from . import db, fetch, settings
from . import feed as feed_parser
from .classify import classify
from .config import Taxonomy
from .errors import FetchError


def check(
    conn,
    taxonomy: Taxonomy,
    feeds: list,
    now: datetime,
    *,
    seed: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    delay: float | None = None,
    fetcher=None,
) -> dict:
    """Poll ``feeds`` and return the payload the agent acts on."""
    fetcher = fetcher or fetch.get
    delay = settings.request_delay() if delay is None else delay
    limit = settings.max_per_check() if limit is None else limit

    run_id = None if dry_run else db.start_run(conn, now)
    reports: list[dict] = []
    failures: list[dict] = []
    seeded_feeds: list[dict] = []
    entries_seen = items_new = items_excluded = 0

    for index, feed in enumerate(feeds):
        if index and delay:
            _time.sleep(delay)

        report = _check_one(
            conn, taxonomy, feed, now,
            run_id=run_id, seed=seed, dry_run=dry_run, fetcher=fetcher,
        )
        reports.append(report)
        entries_seen += report.get("entries", 0)
        # Rows inserted, not rows to report: on a cold start every one of them
        # is stored pre-stamped, and a run row saying 0 would read as a feed
        # that returned nothing.
        items_new += report.get("new", 0) + report.get("absorbed", 0)
        items_excluded += report.get("excluded", 0)
        if report["status"] == "error":
            failures.append({"feed": feed.name, "error": report["error"]})
        if report.get("absorbed"):
            seeded_feeds.append({"feed": feed.name, "absorbed": report["absorbed"]})

    items = (
        []
        if dry_run
        else [item.to_dict() for item in db.pending_items(conn, limit=limit)]
    )

    status = "partial" if failures else "ok"
    if not dry_run:
        db.finish_run(
            conn, run_id, now, status,
            {
                "feeds_checked": len(feeds),
                "entries_seen": entries_seen,
                "items_new": items_new,
                "items_returned": len(items),
                "errors": len(failures),
            },
        )

    return {
        "ok": True,
        "status": status,
        "run_id": run_id,
        "checked_at": now.isoformat(),
        "dry_run": dry_run,
        "feeds": reports,
        "feed_failures": failures,
        "seeded_feeds": seeded_feeds,
        # Entries in this run's documents that matched the exclude list. Not
        # stored, so the same story is counted again on every run that
        # re-fetches its feed -- a 304 does not re-count it.
        "excluded": items_excluded,
        "items": items,
        **db.pending_count(conn),
    }


def _check_one(conn, taxonomy, feed, now, *, run_id, seed, dry_run, fetcher) -> dict:
    try:
        response = fetcher(
            feed.url,
            etag=None if (seed or dry_run) else feed.etag,
            last_modified=None if (seed or dry_run) else feed.last_modified,
        )
    except FetchError as exc:
        if not dry_run:
            db.record_feed_failure(conn, feed.name, now, exc.message)
        return {"feed": feed.name, "status": "error", "error": exc.message,
                "consecutive_failures": feed.consecutive_failures + 1}

    if response.not_modified:
        if not dry_run:
            db.record_feed_success(
                conn, feed.name, now,
                etag=feed.etag, last_modified=feed.last_modified, yield_count=0,
            )
        return {"feed": feed.name, "status": "unchanged", "entries": 0, "new": 0}

    try:
        entries = feed_parser.parse(
            response.text,
            feed.name,
            base_url=response.url,
            max_items=settings.max_items(),
            summary_cap=settings.summary_char_cap(),
        )
    except FetchError as exc:
        if not dry_run:
            db.record_feed_failure(conn, feed.name, now, exc.message)
        return {"feed": feed.name, "status": "error", "error": exc.message,
                "consecutive_failures": feed.consecutive_failures + 1}

    if dry_run:
        return {
            "feed": feed.name,
            "status": "ok",
            "entries": len(entries),
            "new": 0,
            "excluded": sum(1 for entry in entries if _excluded_by(entry, taxonomy)),
            "sample": [
                {
                    "title": entry.title,
                    "url": entry.url,
                    "published_text": entry.published_text,
                    "excluded_by": _excluded_by(entry, taxonomy),
                }
                for entry in entries[:5]
            ],
        }

    absorbing = seed or not feed.seeded
    new = excluded = 0
    for entry in entries:
        # Dropped before the insert: no row, no ledger entry, no way to be
        # returned. The house pattern from video-summary.
        if _excluded_by(entry, taxonomy):
            excluded += 1
            continue
        sectors, signals = classify(entry.title, entry.summary, taxonomy)
        inserted = db.insert_item(
            conn, entry,
            category=feed.category, sectors=sectors, signals=signals,
            now=now, run_id=run_id, reported=absorbing,
        )
        if inserted is not None:
            new += 1

    db.record_feed_success(
        conn, feed.name, now,
        etag=response.etag, last_modified=response.last_modified,
        yield_count=len(entries), seeded=True,
    )

    report = {
        "feed": feed.name,
        "status": _yield_status(feed, entries),
        "entries": len(entries),
        "new": 0 if absorbing else new,
        "excluded": excluded,
    }
    if absorbing:
        report["absorbed"] = new
    return report


def _excluded_by(entry, taxonomy: Taxonomy) -> str | None:
    return taxonomy.excluded_by(f"{entry.title}\n{entry.summary or ''}")


def _yield_status(feed, entries) -> str:
    """``ok``, or ``zero_yield`` for a feed that used to produce and now does not.

    This is the failure worth reporting. The document parsed, the fetch worked,
    and nothing came out -- a section that was retired, a url that changed
    meaning, a paywall that started serving an empty shell. Left alone it says
    "nothing new" forever and looks exactly like a quiet week.
    """
    if not entries and feed.recent_yield > 0:
        return "zero_yield"
    return "ok"

