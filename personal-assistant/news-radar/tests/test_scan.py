"""A whole run: seeding, dedupe, throttling, exclusion, failure isolation.

Behaviour inherited from education-radar is retested here rather than assumed:
these are copies, and a copy that quietly loses the seeding rule would be found
by nobody.
"""

from __future__ import annotations

import pytest

from news_radar import db, scan
from news_radar.errors import FetchError

from .conftest import (ALPHA, ALPHA_URL, BETA, GAMMA, LATER, MUCH_LATER, NOW,
                       OUTLET, OUTLET_URL, fixture)


def run(conn, config, web, now=NOW, **kwargs):
    return scan.scan(conn, config, config.select(), now,
                     fetcher=web.get, sleeper=lambda _: None, **kwargs)


@pytest.fixture
def two_ai(make_config):
    return make_config(sources=[ALPHA, BETA])


# --------------------------------------------------------------------------- cold start


def test_the_first_scan_of_a_source_reports_nothing(conn, two_ai, web):
    """Adding a source must not put its whole back catalogue in the next digest."""
    result = run(conn, two_ai, web)

    assert result["status"] == "ok"
    assert result["totals"]["items_new"] == 4      # 2 from alpha (1 excluded), 2 from beta
    assert all(source["seeding"] for source in result["sources"])
    assert result["pending_items"] == 0
    assert db.pending_items(conn) == []


def test_what_appears_after_seeding_is_pending(conn, two_ai, web):
    run(conn, two_ai, web)
    web.pages[ALPHA_URL] = fixture("alpha.xml").replace(
        "<item>",
        "<item><title>A brand new story</title>"
        "<link>https://alpha.example.com/2026/08/brand-new</link>"
        "<description>Fresh.</description></item><item>", 1)

    result = run(conn, two_ai, web, now=LATER)

    assert result["totals"]["items_new"] == 1
    assert [item.title for item in db.pending_items(conn)] == ["A brand new story"]


def test_a_rescan_finds_nothing_new(conn, two_ai, web):
    run(conn, two_ai, web)
    assert run(conn, two_ai, web, now=LATER)["totals"]["items_new"] == 0


# --------------------------------------------------------------------------- exclusion


def test_sponsored_items_are_dropped_at_the_door(conn, two_ai, web):
    """The only thing that rejects an item. There is no include list."""
    result = run(conn, two_ai, web)

    assert result["totals"]["items_excluded"] == 1
    titles = [item.title for item in db.recent_items(conn, limit=20)]
    assert not any("sponsored" in title.lower() for title in titles)


def test_entities_are_decoded_before_storage(conn, two_ai, web):
    """Feeds double-escape. Left alone it reaches the reader as mojibake, and it
    changes the tokens the clusterer compares."""
    run(conn, two_ai, web)
    titles = [item.title for item in db.recent_items(conn, limit=20)]

    # &amp;#8217; -> &#8217; -> U+2019, the real typographic apostrophe.
    assert "Mark Zuckerberg’s big bet on AI" in titles
    assert not any("&#8217;" in title for title in titles)


# --------------------------------------------------------------------------- throttle


def test_a_source_inside_its_floor_is_skipped_not_fetched(conn, make_config, web):
    config = make_config(sources=[{**ALPHA, "min_interval_minutes": 120}, BETA])
    run(conn, config, web)
    web.requests.clear()

    result = run(conn, config, web, now=LATER)      # one hour later, floor is two

    alpha = next(s for s in result["sources"] if s["source"] == "alpha")
    assert alpha["status"] == "throttled"
    assert ALPHA_URL not in web.requests


def test_a_throttled_source_does_not_stop_the_others(conn, make_config, web):
    """The failure that would make one slow blog silence the whole scan."""
    config = make_config(sources=[{**ALPHA, "min_interval_minutes": 120}, BETA])
    run(conn, config, web)

    result = run(conn, config, web, now=LATER)

    assert result["status"] == "ok"
    beta = next(s for s in result["sources"] if s["source"] == "beta")
    assert beta["status"] == "ok"
    assert beta["items_seen"] == 2       # it really was fetched, not skipped alongside alpha


def test_the_floor_lets_go_once_it_has_passed(conn, make_config, web):
    config = make_config(sources=[{**ALPHA, "min_interval_minutes": 120}])
    run(conn, config, web)

    result = run(conn, config, web, now=MUCH_LATER)   # nine hours later

    assert result["sources"][0]["status"] != "throttled"


def test_ignore_throttle_is_the_manual_override(conn, make_config, web):
    config = make_config(sources=[{**ALPHA, "min_interval_minutes": 120}])
    run(conn, config, web)

    result = run(conn, config, web, now=LATER, ignore_throttle=True)

    assert result["sources"][0]["status"] != "throttled"


def test_no_floor_means_every_run(conn, two_ai, web):
    run(conn, two_ai, web)
    web.requests.clear()

    result = run(conn, two_ai, web, now=LATER)

    assert not any(s["status"] == "throttled" for s in result["sources"])
    assert len(web.requests) == 2       # both fetched again, one hour later


# --------------------------------------------------------------------------- dedupe


@pytest.mark.parametrize("variant", [
    "OpenAI  releases   GPT-X",
    "OPENAI RELEASES GPT-X",
])
def test_the_same_item_reworded_in_place_is_not_new(conn, two_ai, web, variant):
    run(conn, two_ai, web)
    web.pages[ALPHA_URL] = fixture("alpha.xml").replace("OpenAI releases GPT-X", variant)

    assert run(conn, two_ai, web, now=LATER)["totals"]["items_new"] == 0


def test_the_same_story_from_two_sources_is_two_items(conn, two_ai, web):
    """Within-source identity only. Recognising them as one story is the
    digest's job, and doing it here would mean a rewording on one outlet
    suppressed the story everywhere."""
    run(conn, two_ai, web, seed=False)
    stored = [item.title for item in db.recent_items(conn, limit=20)]

    assert "OpenAI releases GPT-X" in stored
    assert "OpenAI Releases GPT-X, Its Biggest Model Yet" in stored


# --------------------------------------------------------------------------- breakage


def test_one_dead_source_does_not_cost_us_the_others(conn, make_config, web):
    config = make_config(sources=[{**ALPHA, "name": "dead", "url": "https://gone.example/feed"}, BETA])

    result = run(conn, config, web)

    assert result["status"] == "partial"
    assert result["source_failures"][0]["source"] == "dead"
    assert result["totals"]["items_new"] == 2       # beta still ran
    assert db.site_state(conn, "dead")["consecutive_failures"] == 1


def test_a_feed_that_goes_empty_is_reported_not_read_as_quiet(conn, two_ai, web):
    run(conn, two_ai, web)
    web.pages[ALPHA_URL] = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'

    result = run(conn, two_ai, web, now=LATER)

    assert result["status"] == "partial"
    assert result["source_failures"][0]["reason"] == "zero_yield"


def test_a_source_that_never_yielded_is_not_reported_as_broken(conn, make_config, web):
    web.pages["https://empty.example/feed"] = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    config = make_config(sources=[{**ALPHA, "name": "empty", "url": "https://empty.example/feed"}])

    assert run(conn, config, web)["status"] == "ok"


def test_a_run_row_is_written_even_when_everything_fails(conn, make_config, web):
    config = make_config(sources=[{**ALPHA, "name": "dead", "url": "https://gone.example/feed"}])

    assert run(conn, config, web)["status"] == "error"
    assert db.recent_runs(conn)[0]["status"] == "error"


# --------------------------------------------------------------------------- conditional GET


def test_an_unchanged_feed_is_not_re_parsed(conn, two_ai, web):
    """What makes scanning continuously affordable."""
    web.etags[ALPHA_URL] = 'W/"v1"'
    run(conn, two_ai, web)

    result = run(conn, two_ai, web, now=LATER)

    assert next(s for s in result["sources"] if s["source"] == "alpha")["status"] == "unchanged"


def test_an_unchanged_feed_still_counts_as_healthy(conn, two_ai, web):
    web.etags[ALPHA_URL] = 'W/"v1"'
    run(conn, two_ai, web)
    run(conn, two_ai, web, now=LATER)

    state = db.site_state(conn, "alpha")
    assert state["last_ok_at"] == LATER.isoformat()
    assert state["consecutive_failures"] == 0
    assert state["recent_yield"] == 3           # not clobbered by the empty scan


# --------------------------------------------------------------------------- html and dry run


def test_the_html_path_still_works(conn, make_config, web):
    config = make_config(sources=[OUTLET])

    run(conn, config, web)
    stored = {item.title: item for item in db.recent_items(conn, limit=20)}

    assert "Central bank holds rates" in stored
    # relative link resolved, tracking parameter stripped
    assert stored["Central bank holds rates"].url == \
        "https://outlet.example.com/latest/rates-decision.html"


def test_a_dry_run_writes_nothing(conn, two_ai, web):
    result = run(conn, two_ai, web, dry_run=True)

    assert result["dry_run"] is True
    assert db.recent_items(conn, limit=10) == []
    assert db.recent_runs(conn) == []
    assert db.site_state(conn, "alpha") is None


def test_requests_are_paced(conn, make_config, web):
    waits = []
    config = make_config(sources=[ALPHA, BETA, GAMMA], request_delay_seconds=2.0)

    scan.scan(conn, config, config.select(), NOW, fetcher=web.get, sleeper=waits.append)

    assert waits == [2.0, 2.0]      # between sources, not before the first
