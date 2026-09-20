"""One scan: fetch, extract, remember. It never reports anything.

Adapted from education-radar/education_radar/scan.py. The window check and the
audience matcher are gone; a per-source throttle and an exclude filter take
their place. Seeding, the zero-yield guard, per-source isolation, the detail
budget and the pacing are unchanged, and so are the reasons for them.

The shape of a run:

1. **Per source, in isolation.** One unreachable feed must never cost us the
   other ten, so every source's failure is caught, recorded against that source,
   and the run finishes ``partial``.
2. **Throttle first.** A source inside its own ``min_interval_minutes`` is
   reported ``throttled`` and skipped. This is a floor on how often one source
   may be fetched, not a schedule -- the cron entry is the schedule.
3. **Conditional GET next.** A ``304`` ends that source's work immediately. This
   is what makes scanning continuously affordable.
4. **New items only.** An item already in the table is not re-stored, which is
   what keeps an hourly scan of a forty-entry feed down to one request.
5. **Cold start seeds, it does not shout.** A source's first successful scan
   stores its whole back catalogue already stamped as digested. Without this,
   adding a source would put its entire archive into the next digest.

Nothing here decides what to *say*. That is `digest.py`, and it runs on its own
schedule against the ledger this leaves behind.
"""

from __future__ import annotations

import dataclasses
import time as _time
from datetime import datetime

from . import db, extract, fetch
from .errors import BrowserError, RadarError


def _excluded(candidate, keywords) -> str | None:
    """The one thing that drops an item outright: feed furniture.

    Sponsored posts and newsletter signups are not news and never become news,
    so they are dropped at the door rather than carried to the digest. There is
    no *include* list anywhere in this skill -- the human already said what a
    source is about by giving it a category.
    """
    if not keywords:
        return None
    text = db.normalize(candidate.text_for_filtering())
    for keyword in keywords:
        if keyword and db.normalize(keyword) in text:
            return keyword
    return None


def _detail_for(candidate, source, transport) -> str | None:
    if candidate.url == source.url:
        return None
    try:
        response = transport(candidate.url)
        if response.not_modified or not response.text:
            return None
        return extract.detail_text(response.text, source.detail_selector)
    except RadarError:
        # A listing page that will not load is not a source failure, and the
        # item is still perfectly reportable from its feed entry.
        return None


def scan(
    conn,
    config,
    sources,
    now: datetime,
    *,
    seed: bool = False,
    dry_run: bool = False,
    ignore_throttle: bool = False,
    browser=None,
    fetcher=None,
    sleeper=_time.sleep,
) -> dict:
    """Scan ``sources`` once and return the run payload.

    ``fetcher`` and ``sleeper`` are injected so the tests can run a whole scan
    against captured pages, at a fixed instant, without a network or a wait.
    """
    fetcher = fetcher or fetch.get
    delay = config.request_delay_seconds

    run_id = None if dry_run else db.start_run(conn, now)
    results: list[dict] = []
    failures: list[dict] = []
    totals = {"sources_scanned": 0, "items_seen": 0, "items_new": 0,
              "items_excluded": 0, "errors": 0}
    first_request = True

    for source in sources:
        state = (db.site_state(conn, source.name) if dry_run
                 else db.ensure_source(conn, source.name, now)) or {}
        seeding = seed or not state.get("seeded")
        entry = {"source": source.name, "category": source.category, "status": "ok",
                 "seeding": seeding, "items_seen": 0, "items_new": 0, "excluded": 0,
                 "candidates": []}

        ready_at = None if ignore_throttle else db.throttled_until(
            state, source.min_interval_minutes, now)
        if ready_at is not None:
            entry["status"] = "throttled"
            entry["next_eligible"] = ready_at.isoformat()
            results.append(entry)
            continue

        def transport(url: str, *, etag=None, last_modified=None):
            if source.render == "browser":
                if browser is None:
                    raise BrowserError(f"source {source.name!r} needs a browser but none was started")
                return browser.get(url)
            return fetcher(url, etag=etag, last_modified=last_modified)

        try:
            if not first_request:
                sleeper(delay)
            first_request = False
            response = transport(
                source.url,
                etag=None if seeding else state.get("etag"),
                last_modified=None if seeding else state.get("last_modified"),
            )
        except RadarError as exc:
            entry["status"] = "error"
            entry["error"] = exc.message
            totals["errors"] += 1
            failures.append({"source": source.name, "reason": "fetch_failed", "message": exc.message})
            if not dry_run:
                db.record_source_failure(conn, source.name, now, exc.message)
            results.append(entry)
            continue

        totals["sources_scanned"] += 1

        if response.not_modified:
            entry["status"] = "unchanged"
            if not dry_run:
                db.record_source_success(
                    conn, source.name, now,
                    etag=state.get("etag"), last_modified=state.get("last_modified"),
                    yield_count=0,
                )
            results.append(entry)
            continue

        try:
            candidates = extract.extract(source, response.text, response.url or source.url)
        except RadarError as exc:
            entry["status"] = "error"
            entry["error"] = exc.message
            totals["errors"] += 1
            failures.append({"source": source.name, "reason": "extract_failed", "message": exc.message})
            if not dry_run:
                db.record_source_failure(conn, source.name, now, exc.message)
            results.append(entry)
            continue

        entry["items_seen"] = len(candidates)
        totals["items_seen"] += len(candidates)

        # A feed that used to carry items and now carries none is not quiet --
        # for an html source it has almost certainly been redesigned out from
        # under the selectors, and for a feed it has probably moved.
        if not candidates and state.get("recent_yield", 0) > 0:
            entry["status"] = "zero_yield"
            failures.append({
                "source": source.name,
                "reason": "zero_yield",
                "message": f"page parsed but produced no items; it returned "
                           f"{state['recent_yield']} on the last successful scan -- "
                           f"check the feed url or the selectors against the live page",
            })

        budget = config.detail_budget
        for candidate in candidates:
            key = db.item_key(candidate.source, candidate.url, candidate.title)
            if db.find_item(conn, candidate.source, key) is not None:
                continue

            hit = _excluded(candidate, config.exclude_keywords)
            if hit:
                entry["excluded"] += 1
                totals["items_excluded"] += 1
                continue

            if source.follow_detail and budget > 0:
                budget -= 1
                sleeper(delay)
                candidate = dataclasses.replace(
                    candidate, detail_text=_detail_for(candidate, source, transport))

            entry["items_new"] += 1
            totals["items_new"] += 1
            entry["candidates"].append({
                "title": candidate.title,
                "url": candidate.url,
                "summary": candidate.summary,
                "date_text": candidate.date_text,
                "source_domain": db.domain_of(candidate.url),
                "would_digest": not seeding,
            })
            if not dry_run:
                db.insert_item(conn, candidate, now, run_id=run_id, digested=seeding)

        if not dry_run:
            db.record_source_success(
                conn, source.name, now,
                etag=response.etag, last_modified=response.last_modified,
                yield_count=len(candidates),
                seeded=True,
            )
        results.append(entry)

    status = "error" if totals["errors"] and totals["sources_scanned"] == 0 else (
        "partial" if failures else "ok"
    )
    if not dry_run:
        db.finish_run(conn, run_id, now, status, totals,
                      "; ".join(f"{item['source']}: {item['reason']}" for item in failures) or None)

    return {
        "ok": status != "error",
        "status": status,
        "run_id": run_id,
        "dry_run": dry_run,
        "now": now.isoformat(),
        "sources": results,
        "source_failures": failures,
        "totals": totals,
        **db.pending_count(conn),
    }
