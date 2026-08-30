"""One pass over the configured estates: fetch, judge, store, say nothing.

This is the part that runs on a timer, and it is deliberately mute. It writes
what it found and returns a count; it never formats a message and never decides
that something is interesting. The cron entry is a plain command for that
reason, and on the many days when nothing new transacted it costs no tokens at
all -- the agent is only woken when ``pending`` is above zero.

**An estate's first check is silent by design.** Centanet serves the newest
hundred records, which on a mature block is a year of history. Announcing all of
it because the tracker happened to be installed today would bury the one deal
that mattered, so the first check absorbs everything as already-reported and
becomes the baseline the trend is computed from. Everything after it is news.
"""

from __future__ import annotations

import time

from . import clock, db, extract, fetch, match, nuxt, settings
from .errors import TrackerError


def _check_estate(conn, entry, config, now, run_id, *, dry_run: bool) -> dict:
    """One estate. Never raises: a failure is a field in the returned dict."""
    state = db.ensure_estate(conn, entry.name, now) if not dry_run else (
        db.estate_state(conn, entry.name) or {"seeded": 0, "recent_yield": 0}
    )
    seeding = not state.get("seeded")

    outcome = {
        "estate": entry.name,
        "display": entry.display,
        "seeding": seeding,
        "parsed": 0,
        "added": 0,
        "matched": 0,
        "already_known": 0,
    }

    try:
        html = fetch.page(entry.url, size=config.fetch_size)
        payload = nuxt.decode(html)
        extraction = extract.extract(payload, entry.name)
    except TrackerError as exc:
        if not dry_run:
            db.record_check_failure(conn, entry.name, now, exc.error)
        outcome.update({
            "ok": False,
            "error": exc.error,
            "message": exc.message,
            "consecutive_failures": (state.get("consecutive_failures") or 0) + 1,
        })
        return outcome

    known = db.known_ids(conn, entry.name)
    added = matched = 0
    samples: list[dict] = []

    for transaction in extraction.records:
        if transaction.tx_id in known:
            continue
        result = match.judge(transaction, entry)
        if dry_run:
            added += 1
            matched += 1 if result.matched else 0
            if len(samples) < 10:
                samples.append({**transaction.to_dict(), "match": result.to_dict()})
            continue
        row_id = db.insert_transaction(
            conn, transaction, result, now,
            run_id=run_id,
            # Seeding stamps everything as delivered. This is the only place a
            # transaction is ever born already reported.
            reported=seeding,
        )
        if row_id is not None:
            added += 1
            if result.matched and not seeding:
                matched += 1

    outcome.update({
        "ok": True,
        "parsed": len(extraction.records),
        "published_count": extraction.published_count,
        "added": added,
        "matched": matched,
        "already_known": len(extraction.records) - added,
        **extraction.to_dict(),
    })
    if dry_run:
        outcome["candidates"] = samples

    # The page loaded and parsed but yielded nothing where it used to yield
    # something. Almost always a changed payload shape rather than a quiet
    # month, and the failure that would otherwise read as "no new transactions"
    # forever.
    previous_yield = state.get("recent_yield") or 0
    if not extraction.records and previous_yield > 0:
        outcome["zero_yield"] = True
        outcome["previous_yield"] = previous_yield

    if not dry_run:
        db.record_check_success(
            conn, entry.name, now,
            parsed=len(extraction.records),
            published_count=extraction.published_count,
            seeded=True if seeding else None,
        )
    return outcome


def check(config, names: list[str] | None = None, *, dry_run: bool = False, conn=None) -> dict:
    """Check every enabled estate, or the ones named.

    ``dry_run`` fetches, parses and judges without writing anything -- the way
    to see what a newly added entry's criteria actually catch before letting it
    seed.
    """
    now = clock.now()
    entries = config.select(names, include_disabled=bool(names))
    owned = conn is None
    conn = conn or db.connect()
    run_id = None if dry_run else db.start_run(conn, now)

    results: list[dict] = []
    try:
        for index, entry in enumerate(entries):
            if index:
                time.sleep(config.request_delay_seconds or settings.request_delay())
            results.append(_check_estate(conn, entry, config, now, run_id, dry_run=dry_run))

        failures = [row for row in results if not row.get("ok")]
        if not results:
            status = "error"
        elif len(failures) == len(results):
            status = "error"
        elif failures:
            status = "partial"
        else:
            status = "ok"

        counts = {
            "estates_checked": len(results) - len(failures),
            "seen": sum(row.get("parsed", 0) for row in results),
            "added": sum(row.get("added", 0) for row in results),
            "matched": sum(row.get("matched", 0) for row in results),
            "errors": len(failures),
        }
        if not dry_run:
            db.finish_run(
                conn, run_id, clock.now(), status, counts,
                detail="; ".join(
                    f"{row['estate']}: {row.get('message', row.get('error'))}" for row in failures
                ) or None,
            )

        warnings: list[str] = []
        for row in results:
            warnings.extend(f"{row['estate']}: {text}" for text in row.get("warnings", ()))
            if row.get("zero_yield"):
                warnings.append(
                    f"{row['estate']}: the page parsed but produced no transactions, "
                    f"where the last good check produced {row['previous_yield']} -- "
                    "the payload shape has probably changed"
                )

        return {
            "ok": status != "error",
            "status": status,
            "run_id": run_id,
            "checked_at": now.isoformat(),
            "dry_run": dry_run,
            **counts,
            "pending": 0 if dry_run else db.pending_count(conn),
            "estates": results,
            "estate_failures": [
                {
                    "estate": row["estate"], "error": row.get("error"),
                    "message": row.get("message"),
                    "consecutive_failures": row.get("consecutive_failures"),
                }
                for row in failures
            ],
            "warnings": warnings,
        }
    finally:
        if owned:
            conn.close()
