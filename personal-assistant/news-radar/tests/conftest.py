"""Shared fixtures.

Two rules the whole suite keeps: **no network** and **no wall clock**. Feeds come
from ``fixtures/`` through :class:`FakeWeb`, and every function that needs the
time is handed it. A test that needs a different instant passes a different one
-- nothing is frozen globally, because nothing here reads the clock except the
CLI.

Adapted from education-radar/tests/conftest.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from news_radar import db
from news_radar.config import load_config
from news_radar.fetch import Response

FIXTURES = Path(__file__).parent / "fixtures"
HKT = ZoneInfo("Asia/Hong_Kong")

NOW = datetime(2026, 8, 11, 14, 0, tzinfo=HKT)
LATER = NOW + timedelta(hours=1)
MUCH_LATER = NOW + timedelta(hours=9)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


ALPHA_URL = "https://alpha.example.com/feed"
BETA_URL = "https://beta.example.org/feed"
GAMMA_URL = "https://gamma.example.net/feed"
OUTLET_URL = "https://outlet.example.com/latest"

ALPHA = {"name": "alpha", "category": "ai", "url": ALPHA_URL, "kind": "rss", "enabled": True}
BETA = {"name": "beta", "category": "ai", "url": BETA_URL, "kind": "rss", "enabled": True}
GAMMA = {"name": "gamma", "category": "world", "url": GAMMA_URL, "kind": "rss", "enabled": True}
OUTLET = {
    "name": "outlet", "category": "world", "url": OUTLET_URL,
    "kind": "html", "list_selector": "article.story",
    "fields": {"title": "h2 a", "link": "h2 a@href", "summary": "p.standfirst", "date": "time"},
    "enabled": True,
}


class FakeWeb:
    """A tiny web, and a record of what was asked for.

    Serves ``url -> body`` and honours conditional GET, so a test can assert
    that an unchanged feed is fetched but not re-parsed. ``fail`` marks a URL
    that raises, which is how per-source failure isolation is tested.
    """

    def __init__(self, pages: dict[str, str], etags: dict[str, str] | None = None):
        self.pages = dict(pages)
        self.etags = dict(etags or {})
        self.fail: dict[str, Exception] = {}
        self.requests: list[str] = []

    def get(self, url, *, etag=None, last_modified=None, timeout=None, retries=None) -> Response:
        self.requests.append(url)
        if url in self.fail:
            raise self.fail[url]
        if url not in self.pages:
            from news_radar.errors import FetchError

            raise FetchError(f"GET {url} -> HTTP 404 Not Found", url=url, status=404)
        current = self.etags.get(url)
        if current and etag == current:
            return Response(url=url, status=304, etag=current)
        return Response(url=url, status=200, text=self.pages[url], etag=current)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "news.db")
    yield connection
    connection.close()


@pytest.fixture
def web():
    return FakeWeb({
        ALPHA_URL: fixture("alpha.xml"),
        BETA_URL: fixture("beta.xml"),
        GAMMA_URL: fixture("gamma.xml"),
        OUTLET_URL: fixture("outlet.html"),
    })


def write_config(tmp_path: Path, **overrides) -> Path:
    """Build a config file from the defaults these tests share."""
    raw = {
        "timezone": "Asia/Hong_Kong",
        "request_delay_seconds": 0.0,
        "detail_budget": 10,
        "cluster_threshold": 0.7,
        "categories": [
            {"name": "ai", "label": "AI"},
            {"name": "world", "label": "World"},
        ],
        "exclude": ["sponsored"],
        "sources": [],
    }
    raw.update(overrides)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def make_config(tmp_path):
    def build(**overrides):
        return load_config(write_config(tmp_path, **overrides))

    return build
