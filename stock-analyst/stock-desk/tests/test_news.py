"""URL identity, headline clustering, and the silent first poll."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from stock_desk import news
from stock_desk.db import SCHEMA
from stock_desk.models import NewsItem

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    yield connection
    connection.close()


def item(title: str, url: str, ticker: str = "NVDA", source: str = "Reuters", peer=None):
    return NewsItem(
        ticker=ticker,
        title=title,
        url=url,
        source=source,
        published_at=None,
        peer_of=peer,
        url_hash=news.url_hash(url),
    )


class TestCanonicalUrl:
    def test_strips_tracking_parameters(self):
        assert news.canonical_url(
            "https://x.com/a?utm_source=news&id=7"
        ) == "https://x.com/a?id=7"

    def test_keeps_meaningful_query(self):
        assert news.canonical_url("https://x.com/a?id=7") == "https://x.com/a?id=7"

    def test_drops_the_fragment_and_trailing_slash(self):
        assert news.canonical_url("https://x.com/a/#top") == "https://x.com/a"

    def test_a_fresh_utm_tag_does_not_look_like_a_new_article(self):
        """The exact failure this guards: the same story arrives with a new
        campaign tag on every poll and defeats the dedupe entirely."""
        first = news.url_hash("https://x.com/a?utm_campaign=mon")
        second = news.url_hash("https://x.com/a?utm_campaign=tue")
        assert first == second

    def test_different_articles_still_differ(self):
        assert news.url_hash("https://x.com/a") != news.url_hash("https://x.com/b")

    def test_domain_strips_www(self):
        assert news.domain_of("https://www.reuters.com/x") == "reuters.com"


class TestClustering:
    def test_a_longer_headline_containing_a_shorter_one_is_one_story(self):
        stories = news.cluster(
            [
                item("Nvidia beats estimates", "https://a.com/1"),
                item("Nvidia beats estimates, guides higher", "https://b.com/2"),
            ],
            0.6,
        )
        assert len(stories) == 1
        assert len(stories[0].items) == 2

    def test_unrelated_headlines_stay_separate(self):
        stories = news.cluster(
            [
                item("Nvidia announces new accelerator", "https://a.com/1"),
                item("Boeing delays another delivery", "https://b.com/2"),
            ],
            0.6,
        )
        assert len(stories) == 2

    def test_financial_boilerplate_does_not_merge_two_companies(self):
        """`stock` and `shares` appear in every headline ever written. Left in the
        signature they make unrelated stories look similar."""
        stories = news.cluster(
            [
                item("Alpha stock shares rise", "https://a.com/1"),
                item("Beta stock shares fall", "https://b.com/2"),
            ],
            0.6,
        )
        assert len(stories) == 2

    def test_clustering_never_crosses_tickers(self):
        stories = news.cluster(
            [
                item("Chip demand surges", "https://a.com/1", ticker="NVDA"),
                item("Chip demand surges", "https://b.com/2", ticker="AMD"),
            ],
            0.6,
        )
        assert len(stories) == 2

    def test_sources_are_collected_across_the_cluster(self):
        stories = news.cluster(
            [
                item("Nvidia beats estimates", "https://a.com/1", source="Reuters"),
                item("Nvidia beats estimates today", "https://b.com/2", source="Bloomberg"),
            ],
            0.6,
        )
        assert set(stories[0].sources) == {"Reuters", "Bloomberg"}

    def test_thin_headlines_are_not_merged(self):
        stories = news.cluster(
            [item("It begins", "https://a.com/1"), item("It ends", "https://b.com/2")], 0.6
        )
        assert len(stories) == 2

    def test_outlet_suffix_is_stripped(self):
        assert news._strip_outlet_suffix("Nvidia beats - Reuters", "Reuters") == "Nvidia beats"

    def test_outlet_suffix_left_alone_when_it_does_not_match(self):
        assert news._strip_outlet_suffix("Nvidia beats", "Reuters") == "Nvidia beats"


class TestStore:
    def test_new_items_are_pending(self, conn):
        news.store(conn, [item("A headline here", "https://a.com/1")], NOW)
        assert news.pending_count(conn) == 1

    def test_the_same_url_is_never_stored_twice(self, conn):
        news.store(conn, [item("A headline here", "https://a.com/1")], NOW)
        second = news.store(conn, [item("A headline here", "https://a.com/1")], NOW)
        assert second == 0
        assert news.pending_count(conn) == 1

    def test_absorbed_items_are_stored_but_never_pending(self, conn):
        """The silent first poll. A back catalogue is not news."""
        news.store(conn, [item("A headline here", "https://a.com/1")], NOW, absorb=True)
        assert news.pending_count(conn) == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM news").fetchone()["n"] == 1

    def test_absorption_does_not_suppress_the_next_genuine_item(self, conn):
        news.store(conn, [item("Old news here", "https://a.com/1")], NOW, absorb=True)
        news.store(conn, [item("Something new happened", "https://a.com/2")], NOW)
        assert news.pending_count(conn) == 1

    def test_pending_is_scoped_by_ticker(self, conn):
        news.store(
            conn,
            [
                item("Nvidia does a thing", "https://a.com/1", ticker="NVDA"),
                item("AMD does another thing", "https://b.com/2", ticker="AMD"),
            ],
            NOW,
        )
        assert news.pending_count(conn, ["NVDA"]) == 1
        assert news.pending_count(conn) == 2


class TestMarkNotified:
    def test_stamping_clears_the_pending_count(self, conn):
        news.store(conn, [item("A headline here", "https://a.com/1")], NOW)
        news.mark_notified(conn, news.pending(conn), NOW)
        assert news.pending_count(conn) == 0

    def test_the_whole_cluster_is_stamped_not_just_the_primary(self, conn):
        """Four outlets carried one story and it was reported once. Stamping only
        the primary surfaces the same event again under a different byline."""
        news.store(
            conn,
            [
                item("Nvidia beats estimates", "https://a.com/1", source="Reuters"),
                item("Nvidia beats estimates again", "https://b.com/2", source="Bloomberg"),
            ],
            NOW,
        )
        stories = news.pending(conn)
        assert len(stories) == 1
        assert news.mark_notified(conn, stories, NOW) == 2
        assert news.pending_count(conn) == 0


class TestQueryBuilding:
    def test_uses_the_company_name_when_known(self):
        assert news.query_for("NVDA", "NVIDIA") == '"NVIDIA" stock'

    def test_falls_back_to_the_bare_symbol(self):
        assert news.query_for("NVDA") == '"NVDA" stock'

    def test_a_suffixed_symbol_does_not_become_a_quoted_phrase(self):
        """`"0700.HK" stock` matches nothing a journalist ever typed."""
        assert news.query_for("0700.HK") == "0700.HK stock"
