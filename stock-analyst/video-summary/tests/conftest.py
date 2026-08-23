"""Shared fixtures. Nothing in this suite touches the network or YouTube.

The three injection points -- ``fetcher``, ``resolver`` and ``transcriber`` --
exist for exactly this reason: a check is a pure function of a feed document, a
redirect and a caption track, all three of which are supplied here.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_summary import db as db_module  # noqa: E402
from video_summary.config import load_config  # noqa: E402
from video_summary.fetch import Response  # noqa: E402
from video_summary.transcript import TranscriptResult  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Asia/Hong_Kong")


@pytest.fixture
def now():
    return datetime(2026, 8, 23, 14, 0, tzinfo=TZ)


@pytest.fixture
def feed_document():
    return (FIXTURES / "channel_feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def config_factory(tmp_path):
    def make(**overrides):
        payload = {
            "timezone": "Asia/Hong_Kong",
            "request_delay_seconds": 0,
            "max_per_check": 5,
            "detect_shorts": False,
            "exclude": ["sponsored"],
            "feeds": [
                {
                    "name": "rates-desk",
                    "url": "https://www.youtube.com/feeds/videos.xml"
                           "?channel_id=UCnexoc6tvesvcCEzZhmI-Ag",
                    "note": "rates and the long end",
                }
            ],
        }
        payload.update(overrides)
        path = tmp_path / "feeds.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_config(path)

    return make


@pytest.fixture
def config(config_factory):
    return config_factory()


@pytest.fixture
def conn(tmp_path):
    connection = db_module.connect(tmp_path / "video_summary.db")
    yield connection
    connection.close()


@pytest.fixture
def fetcher(feed_document):
    """A feed that always answers 200 with the fixture."""

    def get(url, **_kwargs):
        return Response(url=url, status=200, text=feed_document, etag='"v1"', last_modified=None)

    return get


@pytest.fixture
def transcriber(tmp_path):
    """Captions for everything, written where a real fetch would write them."""

    def fetch(video_id, *, languages, title=None, url=None):
        path = tmp_path / "transcripts" / f"{video_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = f"transcript of {video_id}"
        path.write_text(text, encoding="utf-8")
        return TranscriptResult(
            status="ok", path=str(path), chars=len(text), language="en", generated=False
        )

    return fetch


@pytest.fixture
def no_transcriber():
    """A channel with captions disabled."""

    def fetch(video_id, **_kwargs):
        return TranscriptResult(status="unavailable", error="TranscriptsDisabled: nope")

    return fetch


@pytest.fixture
def resolver():
    """Everything is an ordinary video unless its id says otherwise."""

    def resolve(url):
        return url if "cccccccccc3" in url else url.replace("/shorts/", "/watch?v=")

    return resolve
