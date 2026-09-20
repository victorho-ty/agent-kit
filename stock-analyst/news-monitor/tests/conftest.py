"""Fixtures: a temporary database, the real taxonomy, and a fetcher that lies.

Nothing in this suite touches the network. ``fetcher`` builds a stand-in for
:func:`news_monitor.fetch.get` from a dict of url to fixture, which is what lets
a whole check -- conditional GET, seeding, dedupe, the ledger -- run at a fixed
instant against documents captured on disk.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from news_monitor import db as db_module
from news_monitor.config import load_taxonomy
from news_monitor.errors import FetchError
from news_monitor.fetch import Response

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Asia/Hong_Kong")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 9, 19, 9, 0, tzinfo=TZ)


@pytest.fixture
def taxonomy():
    return load_taxonomy()


@pytest.fixture
def conn(tmp_path):
    connection = db_module.connect(tmp_path / "news_monitor.db")
    yield connection
    connection.close()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def fetcher():
    """Build a fake ``fetch.get`` from {url: fixture name or Response or Exception}."""

    def build(routes: dict, *, calls: list | None = None):
        def fake_get(url, *, etag=None, last_modified=None, timeout=None, retries=None):
            if calls is not None:
                calls.append({"url": url, "etag": etag, "last_modified": last_modified})
            target = routes.get(url)
            if target is None:
                raise FetchError(f"GET {url} -> HTTP 404", url=url, status=404)
            if isinstance(target, Exception):
                raise target
            if isinstance(target, Response):
                return target
            return Response(
                url=url,
                status=200,
                text=fixture_text(target),
                etag=f'W/"{target}"',
                last_modified="Thu, 18 Sep 2026 18:02:00 GMT",
            )

        return fake_get

    return build
