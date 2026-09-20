"""A whole run: seeding, dedupe across feeds, the ledger, and what a failure costs."""

from __future__ import annotations

from datetime import timedelta

from news_monitor import check as check_run
from news_monitor import db
from news_monitor.errors import FetchError
from news_monitor.fetch import Response

ACME = "https://wire.example.com/rss"
BETA = "https://beta.example.org/feed.atom"


def _feeds(conn, now, *specs):
    """Put feeds in the database and read them back as rows."""
    for name, url, category in specs:
        db.add_feed(
            conn, name=name, url=url, category=category, note=None,
            enabled=True, origin="seed", gate_verdict="seeded", gate_detail=None, now=now,
        )
    return db.feeds(conn)


def test_first_check_absorbs_the_back_catalogue_silently(conn, taxonomy, now, fetcher):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))
    result = check_run.check(
        conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"})
    )

    assert result["items"] == []
    assert result["seeded_feeds"] == [{"feed": "acme", "absorbed": 3}]
    assert result["pending_items"] == 0
    # Stored, though. They are history, not news, and must never arrive twice.
    assert len(db.recent_items(conn, limit=10)) == 3


def test_second_check_reports_only_what_is_actually_new(conn, taxonomy, now, fetcher):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))
    check_run.check(conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"}))

    grown = (
        '<rss version="2.0"><channel><title>Acme Wire Markets</title>'
        "<item><title>Treasury announces record 10-year auction size</title>"
        "<link>https://wire.example.com/markets/auction</link>"
        "<description>The Treasury lifted auction sizes across the curve.</description></item>"
        "<item><title>CPI rises 0.2% in August as shelter inflation cools</title>"
        "<link>https://wire.example.com/markets/cpi-august</link></item>"
        "</channel></rss>"
    )
    later = now + timedelta(hours=1)
    result = check_run.check(
        conn, taxonomy, db.feeds(conn), later, delay=0,
        fetcher=lambda url, **kw: Response(url=url, status=200, text=grown),
    )

    assert [item["title"] for item in result["items"]] == [
        "Treasury announces record 10-year auction size"
    ]
    assert result["items"][0]["sector_hints"] == ["macro-rates"]


def test_one_story_on_two_wires_is_handed_over_once(conn, taxonomy, now, fetcher):
    """Both feeds carry rss20.xml; the canonical url is the identity."""
    feeds = _feeds(conn, now, ("dj", ACME, "markets"), ("mw", BETA, "markets"))
    # Seed both so the second check reports rather than absorbs.
    routes = {ACME: "rss20.xml", BETA: "rss20.xml"}
    check_run.check(conn, taxonomy, feeds, now, delay=0, fetcher=fetcher(routes))

    assert len(db.recent_items(conn, limit=20)) == 3
    assert {item.feed for item in db.recent_items(conn, limit=20)} == {"dj"}


def test_a_304_costs_nothing_and_sends_the_validators_back(conn, taxonomy, now, fetcher):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))
    check_run.check(conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"}))

    calls: list = []
    later = now + timedelta(hours=1)
    result = check_run.check(
        conn, taxonomy, db.feeds(conn), later, delay=0,
        fetcher=fetcher({ACME: Response(url=ACME, status=304)}, calls=calls),
    )

    assert result["feeds"][0]["status"] == "unchanged"
    assert calls[0]["etag"] == 'W/"rss20.xml"'


def test_one_broken_feed_does_not_cost_the_others_their_headlines(conn, taxonomy, now, fetcher):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"), ("dead", "https://gone.example/x", "markets"))
    result = check_run.check(
        conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"})
    )

    assert result["status"] == "partial"
    assert [failure["feed"] for failure in result["feed_failures"]] == ["dead"]
    assert result["seeded_feeds"] == [{"feed": "acme", "absorbed": 3}]
    assert db.find_feed(conn, "dead").consecutive_failures == 1


def test_a_feed_that_stops_producing_is_reported_as_zero_yield(conn, taxonomy, now, fetcher):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))
    check_run.check(conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"}))

    empty = '<rss version="2.0"><channel><title>Acme Wire Markets</title></channel></rss>'
    result = check_run.check(
        conn, taxonomy, db.feeds(conn), now + timedelta(hours=1), delay=0,
        fetcher=lambda url, **kw: Response(url=url, status=200, text=empty),
    )

    assert result["feeds"][0]["status"] == "zero_yield"


def test_the_ledger_is_stamped_by_mark_not_by_check(conn, taxonomy, now, fetcher):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))
    check_run.check(conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"}))

    grown = (
        '<rss version="2.0"><channel><title>Acme Wire Markets</title>'
        "<item><title>Gold hits record as the dollar index slips</title>"
        "<link>https://wire.example.com/markets/gold</link></item>"
        "</channel></rss>"
    )
    later = now + timedelta(hours=1)
    first = check_run.check(
        conn, taxonomy, db.feeds(conn), later, delay=0,
        fetcher=lambda url, **kw: Response(url=url, status=200, text=grown),
    )
    # Not marked: the same item comes round again, which is what makes a crashed
    # run cost nothing.
    again = check_run.check(
        conn, taxonomy, db.feeds(conn), later, delay=0,
        fetcher=lambda url, **kw: Response(url=url, status=200, text=grown),
    )
    assert [item["id"] for item in again["items"]] == [item["id"] for item in first["items"]]

    db.mark_reported(conn, [first["items"][0]["id"]], later)
    after = check_run.check(
        conn, taxonomy, db.feeds(conn), later, delay=0,
        fetcher=lambda url, **kw: Response(url=url, status=200, text=grown),
    )
    assert after["items"] == []


def test_dry_run_writes_nothing_and_shows_what_a_feed_carries(conn, taxonomy, now, fetcher):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))
    result = check_run.check(
        conn, taxonomy, feeds, now, delay=0, dry_run=True, fetcher=fetcher({ACME: "rss20.xml"})
    )

    assert result["dry_run"] is True
    assert result["run_id"] is None
    assert len(result["feeds"][0]["sample"]) == 3
    assert db.recent_items(conn, limit=10) == []
    assert db.recent_runs(conn) == []


def test_discovery_is_due_every_run_by_default_and_ships_the_tracked_list(
    conn, taxonomy, now, fetcher
):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))
    result = check_run.check(
        conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"})
    )

    assert result["discovery"]["due"] is True
    assert result["discovery"]["tracked_urls"] == [ACME]


def test_a_discovery_interval_is_respected_even_when_a_sweep_finds_nothing(
    conn, taxonomy, now, fetcher, monkeypatch
):
    monkeypatch.setenv("NEWS_MONITOR_DISCOVERY_HOURS", "24")
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))

    first = check_run.check(conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"}))
    soon = check_run.check(
        conn, taxonomy, db.feeds(conn), now + timedelta(hours=1), delay=0,
        fetcher=fetcher({ACME: Response(url=ACME, status=304)}),
    )
    tomorrow = check_run.check(
        conn, taxonomy, db.feeds(conn), now + timedelta(hours=25), delay=0,
        fetcher=fetcher({ACME: Response(url=ACME, status=304)}),
    )

    assert first["discovery"]["due"] is True
    assert soon["discovery"]["due"] is False
    assert tomorrow["discovery"]["due"] is True


def test_a_run_row_records_what_happened(conn, taxonomy, now, fetcher):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"), ("dead", "https://gone.example/x", "markets"))
    check_run.check(conn, taxonomy, feeds, now, delay=0, fetcher=fetcher({ACME: "rss20.xml"}))

    run = db.recent_runs(conn)[0]
    assert run["status"] == "partial"
    assert run["feeds_checked"] == 2
    assert run["entries_seen"] == 3
    assert run["errors"] == 1
    # Rows inserted, even though a cold start reports none of them.
    assert run["items_new"] == 3
    assert run["items_returned"] == 0


def test_a_named_disabled_feed_is_still_checked_when_asked_for(conn, now):
    db.add_feed(
        conn, name="paused", url=ACME, category="markets", note=None,
        enabled=False, origin="discovered", gate_verdict="off_topic", gate_detail=None, now=now,
    )
    assert db.select_feeds(conn, None, include_disabled=False) == []
    assert [feed.name for feed in db.select_feeds(conn, ["paused"])] == ["paused"]


def test_a_fetch_error_is_recorded_and_not_raised(conn, taxonomy, now):
    feeds = _feeds(conn, now, ("acme", ACME, "markets"))

    def always_fails(url, **kwargs):
        raise FetchError(f"GET {url} -> HTTP 503", url=url, status=503)

    result = check_run.check(conn, taxonomy, feeds, now, delay=0, fetcher=always_fails)
    assert result["feeds"][0]["status"] == "error"
    assert "503" in result["feeds"][0]["error"]
