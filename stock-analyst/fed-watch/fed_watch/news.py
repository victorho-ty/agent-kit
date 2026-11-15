"""CNBC Economy headlines published during a probability move."""

from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from http.client import HTTPException
from urllib.request import Request, urlopen
from xml.etree import ElementTree

FEED_URLS = (
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
)
TIMEOUT = 5
MAX_FEED_BYTES = 512 * 1024


def recent(baseline_taken_at: str, taken_at: str) -> dict:
    start = datetime.fromisoformat(baseline_taken_at)
    end = datetime.fromisoformat(taken_at)
    for url in FEED_URLS:
        request = Request(url, headers={"Accept": "application/rss+xml, application/xml"})
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                root = ElementTree.fromstring(response.read(MAX_FEED_BYTES))
            channel = root.find("channel")
            if channel is None:
                raise ElementTree.ParseError("missing RSS channel")
        except (OSError, HTTPException, ElementTree.ParseError, LookupError) as exc:
            error = str(exc)
            continue

        items = {}
        for item in channel.findall("item"):
            link = item.findtext("link", "").strip().partition("?")[0]
            title = " ".join(unescape(item.findtext("title", "")).split())
            if not title or not link.startswith("https://www.cnbc.com/"):
                continue
            try:
                published = parsedate_to_datetime(item.findtext("pubDate", ""))
            except ValueError:
                continue
            if published.tzinfo is None or not start < published <= end:
                continue
            summary = " ".join(unescape(item.findtext("description", "")).split())
            items.setdefault(link, {
                "title": title,
                "url": link,
                "published_at": published.isoformat(),
                "summary": summary[:600],
            })
        return {"status": "ok", "feed_url": url, "items": list(items.values())}

    return {"status": "unavailable", "error": error}
