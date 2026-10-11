"""Validate, render, retain, and acknowledge Hermes-authored briefings."""
from __future__ import annotations

import html
import json
from datetime import datetime
from urllib.parse import urlsplit

from .errors import ConfigError, NotFoundError
from .research import active_item, article_url, text


def _ids(value) -> list[int]:
    if not isinstance(value, list) or not value or any(type(i) is not int or i < 1 for i in value):
        raise ConfigError("item_ids must be a non-empty list of positive integers")
    if len(value) != len(set(value)):
        raise ConfigError("item_ids must not repeat")
    return value


def _line(value, name: str, limit: int) -> str:
    value = text(value, name, limit=limit)
    if len(value.splitlines()) != 1:
        raise ConfigError(f"{name} must be a single line")
    return value


def validate(conn, config, payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"highlights", "stories", "community"}:
        raise ConfigError("briefing needs exactly highlights, stories, and community")
    if any(not isinstance(payload[key], list) for key in payload):
        raise ConfigError("highlights, stories, and community must be lists")
    if not payload["stories"] or len(payload["stories"]) > config.detail_budget:
        raise ConfigError(f"briefing needs 1 to {config.detail_budget} stories")
    if len(payload["highlights"]) > 3 or len(payload["community"]) > 3:
        raise ConfigError("at most 3 highlights and 3 community entries")
    used, used_urls, story_ids, sections = set(), set(), set(), {}
    for section in ("stories", "community"):
        entries = []
        for entry in payload[section]:
            keys = {"item_ids", "headline", "summary", "urls"} if section == "stories" else {"item_ids", "summary", "urls"}
            if not isinstance(entry, dict) or set(entry) != keys:
                raise ConfigError(f"{section}: expected fields {sorted(keys)}")
            ids = _ids(entry["item_ids"])
            if used.intersection(ids):
                raise ConfigError("an item can appear in only one briefing entry")
            rows = [active_item(conn, config, item_id) for item_id in ids]
            if any(row["digested_at"] is not None for row in rows):
                raise ConfigError("briefings must use pending items")
            community = [config.category_of(row["source"]) == "community" for row in rows]
            if any(flag != (section == "community") for flag in community):
                raise ConfigError("community items and vendor stories belong in their own sections")
            allowed, evidence_urls = set(), set()
            published_text = rows[0]["date_text"]
            for row in rows:
                evidence = conn.execute("SELECT * FROM evidence WHERE item_id = ?", (row["id"],)).fetchone()
                if evidence is None or not evidence["text"]:
                    raise ConfigError(f"item {row['id']} needs fetched evidence before summarization")
                if row["id"] == rows[0]["id"] and evidence["published_text"]:
                    published_text = evidence["published_text"]
                allowed.update((row["url"], evidence["url"]))
                evidence_urls.add(evidence["url"])
            urls = entry["urls"]
            if not isinstance(urls, list) or not 1 <= len(urls) <= 3:
                raise ConfigError("each entry needs 1 to 3 source URLs")
            urls = [article_url(url) for url in urls]
            if len(urls) != len(set(urls)) or not set(urls) <= allowed or not set(urls).intersection(evidence_urls):
                raise ConfigError("cite only distinct fetched evidence URLs for this entry")
            if used_urls.intersection(urls):
                raise ConfigError("a source URL can appear in only one briefing entry")
            used_urls.update(urls)
            normalized = {"item_ids": ids, "summary": _line(entry["summary"], "summary", 220), "urls": urls}
            if section == "stories":
                normalized.update(headline=_line(entry["headline"], "headline", 100),
                                  vendors=list(dict.fromkeys(row["source"] for row in rows)),
                                  published_text=published_text)
                if len(normalized["headline"]) + len(normalized["summary"]) > 280:
                    raise ConfigError("headline and summary together must not exceed 280 characters")
                story_ids.update(ids)
            entries.append(normalized)
            used.update(ids)
        sections[section] = entries
    highlights = []
    for highlight in payload["highlights"]:
        if not isinstance(highlight, dict) or set(highlight) != {"text", "item_ids"}:
            raise ConfigError("highlights need text and item_ids")
        ids = _ids(highlight["item_ids"])
        if not set(ids) <= story_ids:
            raise ConfigError("highlights must refer to included vendor stories")
        highlights.append({"text": _line(highlight["text"], "highlight", 160), "item_ids": ids})
    return {"highlights": highlights, **sections}


def render(content: dict) -> str:
    lines = ["AI daily briefing"]
    if content["highlights"]:
        lines.extend(["", "Highlights"])
        lines.extend(f"• {h['text']}" for h in content["highlights"])
    lines.extend(["", "Stories"])
    for story in content["stories"]:
        links = " · ".join(story["urls"])
        lines.append(f"• {story['headline']} — {story['summary']} · {links}")
    if content["community"]:
        lines.extend(["", "Community Pulse"])
        for entry in content["community"]:
            links = " · ".join(entry["urls"])
            lines.append(f"• {entry['summary']} · {links}")
    return "\n".join(lines)


def _html_entries(entries: list[dict]) -> str:
    rows = []
    for entry in entries:
        headline = (f'<strong>{html.escape(entry["headline"])}</strong> — '
                    if "headline" in entry else "")
        links = " ".join(
            f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">'
            f'{html.escape(urlsplit(url).netloc.removeprefix("www."))} ↗</a>'
            for url in entry["urls"]
        )
        rows.append(f'<li>{headline}{html.escape(entry["summary"])} '
                    f'<span class="sources">{links}</span></li>')
    return "".join(rows)


def html_document(content: dict) -> str:
    highlights = ""
    if content["highlights"]:
        bullets = "".join(f'<li>{html.escape(h["text"])}</li>' for h in content["highlights"])
        highlights = (f'<section class="highlights" aria-labelledby="highlights-title">'
                      f'<h2 id="highlights-title">Highlights</h2><ul>{bullets}</ul></section>')
    community = ""
    if content["community"]:
        community = ('<section class="community" aria-labelledby="community-title">'
                     '<h2 id="community-title">Community Pulse</h2>'
                     f'<ul class="entries">{_html_entries(content["community"])}</ul></section>')
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI daily briefing</title>
<style>
:root { color-scheme: light; --ink: #15283e; --muted: #52657b; --teal: #086657; }
* { box-sizing: border-box; }
body { margin: 0; background: #edf2f7; color: var(--ink);
       font: 16px/1.6 "Segoe UI", -apple-system, BlinkMacSystemFont, Arial, sans-serif; }
main { max-width: 920px; margin: 40px auto; padding: 0 24px; }
header { padding: 32px 36px; background: #13283f; color: #fff;
         border-radius: 18px 18px 0 0; border-top: 5px solid #47cfb2; }
.eyebrow { margin: 0 0 10px; color: #87e5d0; font-size: 12px;
           font-weight: 700; letter-spacing: .15em; text-transform: uppercase; }
h1 { margin: 0; font-size: clamp(28px, 5vw, 40px); line-height: 1.2; letter-spacing: -.03em; }
.subtitle { margin: 12px 0 0; color: #d0deed; font-size: 15px; }
section { padding: 26px 36px; background: #fff; border: 1px solid #dbe3ec; border-top: 0; }
h2 { margin: 0 0 14px; font-size: 13px; letter-spacing: .1em; text-transform: uppercase; }
ul { margin: 0; padding-left: 20px; }
li { padding-left: 4px; overflow-wrap: anywhere; }
li::marker { color: var(--teal); }
.highlights { background: #e4f5ef; border-left: 4px solid #168471; padding-left: 33px; }
.highlights h2 { color: var(--teal); }
.highlights li { margin: 8px 0; font-weight: 600; }
.entries li { padding-top: 14px; padding-bottom: 14px; border-bottom: 1px solid #e7edf3; }
.entries li:first-child { padding-top: 0; }
.entries li:last-child { padding-bottom: 0; border-bottom: 0; }
strong { font-weight: 650; }
.sources { display: inline; font-size: 12px; }
a { display: inline-block; margin: 2px 0 2px 5px; padding: 1px 7px;
    color: #155aa0; background: #eef4fb; border: 1px solid #d9e6f5;
    border-radius: 5px; text-decoration: none; font-weight: 600; }
a:hover { background: #dceafb; text-decoration: underline; }
a:focus-visible { outline: 2px solid #155aa0; outline-offset: 3px; }
.community { background: #f8f9fe; }
.community h2 { color: #59518c; }
.community li::marker { color: #59518c; }
footer { padding: 18px 36px; background: #fff; border: 1px solid #dbe3ec; border-top: 0;
         border-radius: 0 0 18px 18px; color: var(--muted); font-size: 12px; }
@media (max-width: 600px) {
  body { font-size: 15px; }
  main { margin: 16px auto; padding: 0 12px; }
  header, section { padding: 22px 20px; }
  .highlights { padding-left: 17px; }
  footer { padding: 16px 20px; }
}
@media print {
  body { background: #fff; }
  main { max-width: none; margin: 0; padding: 0; }
  header, .highlights { print-color-adjust: exact; }
  section, li { break-inside: avoid; }
}
</style>
</head>
<body>
<main>
<header><p class="eyebrow">News radar / AI research</p>
<h1>AI daily briefing</h1><p class="subtitle">Key developments and the sources behind them.</p></header>
""" + highlights + ('<section aria-labelledby="stories-title"><h2 id="stories-title">Stories</h2>'
                      f'<ul class="entries">{_html_entries(content["stories"])}</ul></section>') + community + """
<footer>Open a source for the full story.</footer>
</main>
</body>
</html>
"""


def save(conn, config, payload: dict, now: datetime) -> dict:
    content = validate(conn, config, payload)
    cursor = conn.execute("INSERT INTO briefing (created_at, content) VALUES (?, ?)",
                          (now.isoformat(), json.dumps(content, ensure_ascii=False)))
    conn.commit()
    return read(conn, cursor.lastrowid)


def read(conn, briefing_id: int) -> dict:
    row = conn.execute("SELECT * FROM briefing WHERE id = ?", (briefing_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"briefing {briefing_id} does not exist")
    content = json.loads(row["content"])
    return {"ok": True, "briefing_id": row["id"], "created_at": row["created_at"],
            "committed_at": row["committed_at"], "content": content, "body": render(content),
            "totals": {"stories": len(content["stories"]), "community": len(content["community"]),
                       "items": sum(len(e["item_ids"]) for key in ("stories", "community") for e in content[key])}}


def commit(conn, config, briefing_id: int, now: datetime) -> dict:
    saved = read(conn, briefing_id)
    if saved["committed_at"] is not None:
        return {"ok": True, "briefing_id": briefing_id, "committed": 0, "already_committed": True}
    ids = [item_id for section in ("stories", "community") for entry in saved["content"][section]
           for item_id in entry["item_ids"]]
    rows = [active_item(conn, config, item_id) for item_id in ids]
    if any(row["digested_at"] is not None for row in rows):
        raise ConfigError("briefing is stale: an included item has already been reported or dismissed")
    with conn:
        for item_id in ids:
            conn.execute("UPDATE item SET digested_at = ? WHERE id = ?", (now.isoformat(), item_id))
            conn.execute("INSERT INTO review VALUES (?, 'reported', ?, ?)",
                         (item_id, f"briefing {briefing_id}", now.isoformat()))
        conn.execute("UPDATE briefing SET committed_at = ? WHERE id = ?", (now.isoformat(), briefing_id))
    return {"ok": True, "briefing_id": briefing_id, "committed": len(ids), "already_committed": False}
