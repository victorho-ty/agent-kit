"""The contract the agent actually depends on: one JSON object, and an exit code.

These drive ``run(argv)`` rather than the modules underneath, because what
breaks an agent is not a wrong dataclass -- it is a payload that stopped
carrying a key, or an error that started arriving as a traceback.
"""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from news_monitor import db, gate, scope
from news_monitor.cli import run
from news_monitor.errors import ExitCode
from news_monitor.fetch import Response
from news_monitor.models import Entry

from .conftest import fixture_text

ACME = "https://wire.example.com/rss"


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch, now):
    monkeypatch.setattr("news_monitor.clock.now", lambda tz: now.astimezone(tz))
    monkeypatch.setenv("NEWS_MONITOR_DB", str(tmp_path / "news_monitor.db"))
    monkeypatch.setenv("NEWS_MONITOR_DELAY", "0")


@pytest.fixture
def offline(monkeypatch):
    """Route every fetch either bundle makes at a fixture."""

    def route(mapping):
        def fake_get(url, **kwargs):
            if url not in mapping:
                from news_monitor.errors import FetchError

                raise FetchError(f"GET {url} -> HTTP 404", url=url, status=404)
            return Response(url=url, status=200, text=fixture_text(mapping[url]))

        monkeypatch.setattr("news_monitor.fetch.get", fake_get)
        monkeypatch.setattr(gate, "_default_fetcher", fake_get)

    return route


def _run(capsys, argv) -> tuple[int, dict]:
    code = run(argv)
    return code, json.loads(capsys.readouterr().out)


def test_feeds_lists_the_seeded_sources(capsys):
    code, payload = _run(capsys, ["feeds"])

    assert code == int(ExitCode.OK)
    assert payload["ok"] is True
    names = [feed["name"] for feed in payload["feeds"]]
    assert "federal-reserve" in names
    assert all(feed["enabled"] for feed in payload["feeds"])
    assert all(feed["origin"] == "seed" for feed in payload["feeds"])


def test_add_stores_a_passing_candidate_enabled(capsys, offline):
    offline({ACME: "rss20.xml"})
    code, payload = _run(capsys, ["add", "--url", ACME, "--category", "markets"])

    assert code == int(ExitCode.OK)
    assert payload["enabled"] is True
    assert payload["gate"]["verdict"] == "pass"
    assert payload["name"] == "acme-wire-markets"
    assert payload["stored"] is True

    _, listed = _run(capsys, ["feeds"])
    added = next(feed for feed in listed["feeds"] if feed["name"] == "acme-wire-markets")
    assert added["origin"] == "added"


def test_add_stores_an_off_topic_candidate_disabled_with_its_reason(capsys, offline):
    offline({"https://delta.example.org/rss": "hobby.xml"})
    code, payload = _run(capsys, ["add", "--url", "https://delta.example.org/rss"])

    assert code == int(ExitCode.OK)
    assert payload["enabled"] is False
    assert payload["gate"]["verdict"] == "off_topic"
    # Stored rather than dropped: the operator asked for it, and one `enable`
    # overrides the gate.
    assert payload["stored"] is True


def test_add_refuses_a_url_that_is_not_a_feed(capsys, offline):
    offline({"https://paywall.example.com/feed": "notafeed.html"})
    code, payload = _run(capsys, ["add", "--url", "https://paywall.example.com/feed"])

    assert code == int(ExitCode.ERR_CANDIDATE)
    assert payload["ok"] is False
    assert payload["error"] == "ERR_CANDIDATE"
    assert payload["detail"]["reason"] == "not_a_feed"


def test_add_says_already_tracked_rather_than_inserting_twice(capsys, offline):
    offline({ACME: "rss20.xml"})
    _run(capsys, ["add", "--url", ACME])
    code, payload = _run(capsys, ["add", "--url", ACME])

    assert code == int(ExitCode.ERR_CANDIDATE)
    assert payload["detail"]["reason"] == "already_tracked"
    assert payload["detail"]["feed"] == "acme-wire-markets"


def test_add_dry_run_reports_the_verdict_without_writing(capsys, offline):
    offline({ACME: "rss20.xml"})
    _, payload = _run(capsys, ["add", "--url", ACME, "--dry-run"])
    assert payload["stored"] is False

    _, listed = _run(capsys, ["feeds"])
    assert ACME not in [feed["url"] for feed in listed["feeds"]]


def test_enable_overrides_the_gate_and_is_idempotent(capsys, offline):
    offline({"https://delta.example.org/rss": "hobby.xml"})
    _run(capsys, ["add", "--url", "https://delta.example.org/rss", "--name", "garden"])

    _, first = _run(capsys, ["enable", "--feed", "garden"])
    _, second = _run(capsys, ["enable", "--feed", "garden"])

    assert first["enabled"] is True and first["changed"] is True
    assert second["enabled"] is True and second["changed"] is False


def test_disable_on_an_unknown_feed_is_a_typed_error(capsys):
    code, payload = _run(capsys, ["disable", "--feed", "no-such-feed"])
    assert code == int(ExitCode.ERR_NOT_FOUND)
    assert payload["error"] == "ERR_NOT_FOUND"


def test_check_then_mark_drains_the_ledger(capsys, offline, monkeypatch):
    offline({ACME: "rss20.xml"})
    _run(capsys, ["add", "--url", ACME, "--name", "acme"])

    # First check absorbs the back catalogue; the feed then grows by one.
    _run(capsys, ["check", "--feed", "acme"])
    grown = (
        '<rss version="2.0"><channel><title>Acme Wire Markets</title>'
        "<item><title>OPEC agrees an output cut</title>"
        "<link>https://wire.example.com/markets/opec</link>"
        "<description>The group agreed to reduce crude production.</description></item>"
        "</channel></rss>"
    )
    monkeypatch.setattr(
        "news_monitor.fetch.get",
        lambda url, **kwargs: Response(url=url, status=200, text=grown),
    )

    _, checked = _run(capsys, ["check", "--feed", "acme"])
    assert [item["title"] for item in checked["items"]] == ["OPEC agrees an output cut"]
    # Sources are added on the operator's request only; check never prompts a search.
    assert "discovery" not in checked
    assert checked["items"][0]["sector_hints"] == ["energy"]
    assert checked["items"][0]["signals"] == ["supply-shock"]
    assert checked["items"][0]["url"] == "https://wire.example.com/markets/opec"

    item_id = checked["items"][0]["id"]
    _, marked = _run(capsys, ["mark", "--item", str(item_id)])
    assert marked["marked"] == [item_id]
    assert marked["pending_items"] == 0

    _, again = _run(capsys, ["mark", "--item", str(item_id)])
    assert again["already_marked"] == [item_id]

    _, after = _run(capsys, ["check", "--feed", "acme"])
    assert after["items"] == []


def _included_item(conn, now, name):
    item_id = db.insert_item(
        conn, Entry("wire", name, f"https://example.org/{name}", name),
        category="markets", sectors=[], signals=[], now=now,
    )
    db.record_scope(conn, item_id, scope.Decision("include", 0.8, "jev-test").to_dict())
    return str(item_id)


def test_deliver_marks_only_after_send_and_retries_without_resending(capsys, monkeypatch, now):
    conn = db.connect()
    try:
        sent_id = _included_item(conn, now, "included")
        other_id = _included_item(conn, now, "other")
        monkeypatch.setenv("NEWS_MONITOR_DELIVERY_TARGET", "telegram:123")
        digest = "Rates move https://example.org/included"
        send_results = iter([1, 0])
        calls = []

        def send(argv, *, input, **kwargs):
            assert db.resolve_item(conn, sent_id).reported_at is None
            calls.append((argv, input))
            return subprocess.CompletedProcess(argv, next(send_results))

        monkeypatch.setattr("news_monitor.cli.subprocess.run", send)
        args = ["deliver", "--item", sent_id]
        monkeypatch.setattr("sys.stdin", io.StringIO(digest))
        code, failed = _run(capsys, args)
        assert code == int(ExitCode.ERR_DELIVERY)
        assert failed["error"] == "ERR_DELIVERY"
        assert db.pending_count(conn)["pending_items"] == 2

        monkeypatch.setattr("sys.stdin", io.StringIO(digest))
        code, delivered = _run(capsys, args)
        assert code == int(ExitCode.OK)
        assert delivered["marked"] == [int(sent_id)]
        assert [item.id for item in db.pending_items(conn, classified=True)] == [int(other_id)]

        monkeypatch.setattr("sys.stdin", io.StringIO(digest))
        _, retried = _run(capsys, args)
        assert retried["already_marked"] == [int(sent_id)]
        assert len(calls) == 2
        argv, message = calls[0]
        assert argv == ["hermes", "-p", "stock-analyst", "send", "--to", "telegram:123",
                        "--file", "-", "--quiet"]
        assert message == digest.encode("utf-8")
    finally:
        conn.close()


def test_discard_is_a_distinct_idempotent_disposition(capsys, now):
    conn = db.connect()
    try:
        item_id = _included_item(conn, now, "filler")
        _, discarded = _run(capsys, ["discard", "--item", item_id])
        assert discarded["discarded"] == [int(item_id)]
        assert discarded["pending_items"] == 0

        _, retried = _run(capsys, ["discard", "--item", item_id])
        assert retried["already_discarded"] == [int(item_id)]
        _, marked = _run(capsys, ["mark", "--item", item_id])
        assert marked["marked"] == []
        assert marked["already_discarded"] == [int(item_id)]
        _, pending = _run(capsys, ["items", "--pending"])
        assert pending["items"] == []
        _, history = _run(capsys, ["items"])
        assert history["items"][0]["discarded_at"] is not None
        assert history["items"][0]["reported_at"] is None
    finally:
        conn.close()


def test_check_with_no_enabled_feeds_is_a_clean_skip(capsys, offline):
    _, listed = _run(capsys, ["feeds"])
    for feed in listed["feeds"]:
        _run(capsys, ["disable", "--feed", feed["name"]])

    code, payload = _run(capsys, ["check"])
    assert code == int(ExitCode.OK)
    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_enabled_feeds"
    assert payload["items"] == []


def test_items_filters_by_state(capsys, offline):
    offline({ACME: "rss20.xml"})
    _run(capsys, ["add", "--url", ACME, "--name", "acme"])
    _run(capsys, ["check", "--feed", "acme"])

    _, reported = _run(capsys, ["items", "--feed", "acme", "--reported"])
    _, pending = _run(capsys, ["items", "--feed", "acme", "--pending"])

    # The cold start absorbed everything, so all three are reported and none pend.
    assert reported["count"] == 3
    assert pending["count"] == 0


def test_an_unopenable_database_is_a_typed_error_not_a_traceback(capsys, monkeypatch, tmp_path):
    """ERR_DB is documented, so it has to be reachable."""
    wall = tmp_path / "wall"
    wall.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("NEWS_MONITOR_DB", str(wall / "news_monitor.db"))

    code, payload = _run(capsys, ["feeds"])
    assert code == int(ExitCode.ERR_DB)
    assert payload["error"] == "ERR_DB"


def test_runs_is_the_liveness_surface(capsys, offline):
    offline({ACME: "rss20.xml"})
    _run(capsys, ["add", "--url", ACME, "--name", "acme"])
    _run(capsys, ["check", "--feed", "acme"])

    _, payload = _run(capsys, ["runs", "--limit", "3"])
    assert payload["runs"][0]["feeds_checked"] == 1
    assert payload["runs"][0]["status"] == "ok"
