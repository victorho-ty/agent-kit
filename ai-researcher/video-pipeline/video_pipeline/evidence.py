from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .core import PipelineError, require, stamp, text


def canonical_url(value: str) -> str:
    parts = urlsplit(text(value, "source URL"))
    require(parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password,
            "source URL must be HTTP(S), without credentials")
    query = [(key, val) for key, val in parse_qsl(parts.query) if not key.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def fetch_article(url: str) -> dict:
    url = canonical_url(url)
    request = Request(url, headers={"User-Agent": "agent-kit-video-pipeline/0.1"})
    with urlopen(request, timeout=30) as response:
        require("html" in response.headers.get("Content-Type", ""), "source did not return HTML; import extracted evidence instead")
        body = response.read(2_000_001)
        require(len(body) <= 2_000_000, "article exceeds 2 MB limit")
        resolved = canonical_url(response.url)
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    for node in soup.select("script,style,nav,header,footer,aside,form,noscript,svg"):
        node.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    content = " ".join(root.stripped_strings)
    require(len(content) >= 100, "too little article evidence; fetch another source")
    return {"url": resolved, "title": title, "text": content[:16000], "fetched_at": stamp()}


def normalize_sources(payload) -> list[dict]:
    require(isinstance(payload, list) and 1 <= len(payload) <= 8, "evidence must contain 1-8 source objects")
    result, seen = [], set()
    for source in payload:
        require(isinstance(source, dict), "source must be an object")
        url = canonical_url(source.get("url"))
        if url in seen:
            raise PipelineError("duplicate source URL")
        seen.add(url)
        content = text(source.get("text"), "article evidence", 16000)
        require(len(content) >= 100, "each source needs >=100 characters of article evidence")
        result.append({"id": f"S{len(result) + 1}", "url": url,
                       "title": text(source.get("title"), "source title", 500), "text": content,
                       "fetched_at": source.get("fetched_at", stamp())})
    return result
