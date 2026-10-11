"""Search planning and ingestion; Hermes owns discovery and editorial judgement."""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from . import cluster, db
from .errors import ConfigError, NotFoundError
from .extract import canonical_url
from .models import Candidate


def text(value, name: str, *, required: bool = True, limit: int = 4000) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ConfigError(f"{name} must be {'non-empty ' if required else ''}text, at most {limit} characters")
    return value.strip() or None


def article_url(value) -> str:
    value = text(value, "url", limit=2000)
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError("an absolute http(s) URL without credentials is required")
        if not parts.path.strip("/"):
            raise ValueError("use an article/thread URL, not a homepage")
        parts.port
    except ValueError as exc:
        raise ConfigError(f"invalid article URL: {exc}") from exc
    return canonical_url(value)


def plan(config, now: datetime, topic: str | None = None) -> dict:
    if topic is not None:
        topic = text(topic, "topic", limit=300)
    jobs, feed_jobs = [], []
    for source in config.select():
        if source.kind != "search":
            if topic is None:
                feed_jobs.append({"source": source.name, "category": source.category,
                                  "kind": source.kind, "url": source.url, "max_items": source.max_items})
            continue
        community = "{topic}" in source.query
        if community != (topic is not None):
            continue
        jobs.append({
            "source": source.name, "category": source.category,
            "query": source.query.format(today=now.strftime("%B %d, %Y"),
                                         month_year=now.strftime("%B %Y"), topic=topic or ""),
            "reference_url": source.url, "max_items": source.max_items,
        })
    return {"ok": True, "as_of": now.isoformat(), "stage": "community" if topic else "vendors",
            "window_start": (now - timedelta(days=config.lookback_days)).isoformat(),
            "lookback_days": config.lookback_days, "jobs": jobs, "count": len(jobs),
            "feed_jobs": feed_jobs, "feed_count": len(feed_jobs)}


def ingest(conn, config, payload: dict, now: datetime) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list) or not payload["results"]:
        raise ConfigError("input needs a non-empty results list")
    sources = {source.name: source for source in config.select() if source.kind == "search"}
    prepared, failed, seen_sources = [], [], set()
    for result in payload["results"]:
        if not isinstance(result, dict) or not isinstance(result.get("source"), str) or result["source"] not in sources:
            raise ConfigError("every result must name an enabled search source")
        source = sources[result["source"]]
        if source.name in seen_sources:
            raise ConfigError(f"duplicate source result: {source.name}")
        seen_sources.add(source.name)
        if "error" in result:
            if result.get("items"):
                raise ConfigError("a failed search cannot also contain items")
            failed.append({"source": source.name, "reason": "search_failed",
                           "message": text(result["error"], "error", limit=1000)})
            continue
        items = result.get("items")
        if not isinstance(items, list) or len(items) > source.max_items:
            raise ConfigError(f"{source.name}: items must be a list of at most {source.max_items}")
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                raise ConfigError("search items must be objects")
            candidates.append(Candidate(
                source=source.name, title=text(item.get("title"), "title", limit=500),
                url=article_url(item.get("url")),
                summary=text(item.get("snippet"), "snippet", required=False, limit=2000),
                date_text=text(item.get("published_text"), "published_text", required=False, limit=200),
            ))
        prepared.append((source, candidates))

    totals = {"sources_scanned": len(prepared), "items_seen": 0, "items_new": 0,
              "items_excluded": 0, "errors": len(failed)}
    inserted, duplicates = [], 0
    run_id = db.start_run(conn, now)
    with conn:
        for source, candidates in prepared:
            for candidate in candidates:
                totals["items_seen"] += 1
                if conn.execute("SELECT id FROM item WHERE source = ? AND url = ?",
                                (source.name, candidate.url)).fetchone():
                    duplicates += 1
                    continue
                if any(word.casefold() in candidate.text_for_filtering().casefold()
                       for word in config.exclude_keywords):
                    totals["items_excluded"] += 1
                    continue
                item_id = db.insert_item(conn, candidate, now, run_id=run_id, commit=False)
                inserted.append(item_id)
                totals["items_new"] += 1
    for source, candidates in prepared:
        db.ensure_source(conn, source.name, now)
        db.record_source_success(conn, source.name, now, etag=None, last_modified=None,
                                 yield_count=len(candidates), seeded=True)
    for failure in failed:
        db.ensure_source(conn, failure["source"], now)
        db.record_source_failure(conn, failure["source"], now, failure["message"])
    status = "partial" if failed and prepared else "error" if failed else "ok"
    db.finish_run(conn, run_id, now, status, totals)
    return {"ok": status != "error", "status": status, "run_id": run_id,
            "totals": totals, "duplicates": duplicates, "inserted_ids": inserted,
            "source_failures": failed}


def active_item(conn, config, item_id: int):
    if type(item_id) is not int or item_id < 1:
        raise ConfigError("item IDs must be positive integers")
    row = conn.execute("SELECT * FROM item WHERE id = ?", (item_id,)).fetchone()
    names = {s.name for s in config.select()}
    if row is None or row["source"] not in names:
        raise NotFoundError(f"item {item_id} is not from an enabled source")
    return row


def shortlist(conn, config, now: datetime, limit: int = 100) -> dict:
    if limit < 1 or limit > 500:
        raise ConfigError("limit must be between 1 and 500")
    names = [s.name for s in config.select()]
    pending = db.pending_items(conn, names)
    items = pending[:limit]
    sections = []
    for category in config.categories:
        members = [item for item in items if config.category_of(item.source) == category.name]
        stories = []
        for story in cluster.cluster(members, config.cluster_threshold):
            stories.append({**story.to_dict(), "vendors": list(dict.fromkeys(i.source for i in story.items)),
                            "snippets": [{"id": i.id, "text": (i.summary or "")[:1000]} for i in story.items]})
        if stories:
            sections.append({"category": category.name, "label": category.display(), "stories": stories})
    return {"ok": True, "as_of": now.isoformat(), "count": len(items), "pending_items": len(pending),
            "has_more": len(pending) > limit, "sections": sections,
            "legacy_pending_items": db.pending_count(conn)["pending_items"] - len(pending)}


def dismiss(conn, config, ids: list[int], reason: str, now: datetime) -> dict:
    reason = text(reason, "reason", limit=500)
    rows = [active_item(conn, config, item_id) for item_id in dict.fromkeys(ids)]
    if any(row["digested_at"] is not None for row in rows):
        raise ConfigError("only pending items can be dismissed")
    with conn:
        for row in rows:
            conn.execute("INSERT INTO review VALUES (?, 'dismissed', ?, ?)",
                         (row["id"], reason, now.isoformat()))
            conn.execute("UPDATE item SET digested_at = ? WHERE id = ?", (now.isoformat(), row["id"]))
    return {"ok": True, "dismissed": len(rows), "ids": [row["id"] for row in rows]}
