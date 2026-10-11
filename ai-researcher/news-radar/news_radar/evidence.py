"""Bounded, cached article evidence for stories selected by Hermes."""
from __future__ import annotations

from datetime import datetime

from . import fetch
from .errors import ConfigError, FetchError, RadarError
from .extract import soup, _clean
from .research import active_item, article_url

TEXT_LIMIT = 12000


def parse(text: str) -> dict:
    document = soup(text)
    if document.find(["html", "article", "main", "body"]) is None:
        raise FetchError("response does not contain an HTML article page")
    title = document.select_one('meta[property="og:title"]')
    headline = _clean(title.get("content")) if title else None
    if not headline and document.title:
        headline = _clean(document.title.get_text(" ", strip=True))
    published = document.select_one('meta[property="article:published_time"]')
    published_text = _clean(published.get("content")) if published else None
    if not published_text:
        date = document.select_one("time[datetime]")
        published_text = _clean(date.get("datetime")) if date else None
    for node in document.select("script, style, noscript, nav, header, footer, aside, form, svg"):
        node.decompose()
    root = document.find("article") or document.find("main") or document.body or document
    body = _clean(root.get_text(" ", strip=True)) or ""
    if len(body) < 100:
        raise FetchError("page contains too little readable evidence; inspect for login or bot blocking")
    return {"title": headline, "published_text": published_text, "text": body[:TEXT_LIMIT],
            "content_root": root.name, "truncated": len(body) > TEXT_LIMIT}


def collect(conn, config, ids: list[int], now: datetime, *, fetcher=None, sleeper=None,
            refresh: bool = False) -> dict:
    import time
    fetcher = fetcher or fetch.get
    sleeper = sleeper or time.sleep
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > config.detail_budget:
        raise ConfigError(f"request between 1 and {config.detail_budget} item IDs")
    rows = [active_item(conn, config, item_id) for item_id in ids]
    results, failures = [], []
    fetched = False
    for row in rows:
        cached = conn.execute("SELECT * FROM evidence WHERE item_id = ?", (row["id"],)).fetchone()
        if cached is not None and not refresh:
            results.append({**dict(cached), "cached": True})
            continue
        try:
            if fetched:
                sleeper(config.request_delay_seconds)
            fetched = True
            response = fetcher(row["url"])
            if response.status != 200:
                raise FetchError(f"article returned HTTP {response.status}")
            resolved = article_url(response.url)
            data = parse(response.text)
        except RadarError as exc:
            failures.append({"id": row["id"], "url": row["url"], "error": exc.message})
            continue
        record = {"item_id": row["id"], "url": resolved, "title": data["title"],
                  "published_text": data["published_text"], "text": data["text"],
                  "fetched_at": now.isoformat()}
        conn.execute("""INSERT INTO evidence VALUES (:item_id, :url, :title, :published_text, :text, :fetched_at)
                        ON CONFLICT(item_id) DO UPDATE SET url=excluded.url, title=excluded.title,
                        published_text=excluded.published_text, text=excluded.text, fetched_at=excluded.fetched_at""",
                     record)
        conn.commit()
        results.append({**record, "cached": False, "content_root": data["content_root"],
                        "truncated": data["truncated"]})
    return {"ok": bool(results), "status": "partial" if results and failures else "error" if failures else "ok",
            "evidence": results, "failures": failures, "count": len(results)}
