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
        news.store(conn, [item("Acme raises its guidance for 2027", "https://a.com/1")], NOW)
        assert news.pending_count(conn) == 1

    def test_the_same_url_is_never_stored_twice(self, conn):
        news.store(conn, [item("Acme raises its guidance for 2027", "https://a.com/1")], NOW)
        second = news.store(conn, [item("Acme raises its guidance for 2027", "https://a.com/1")], NOW)
        assert second == 0
        assert news.pending_count(conn) == 1

    def test_absorbed_items_are_stored_but_never_pending(self, conn):
        """The silent first poll. A back catalogue is not news."""
        news.store(conn, [item("Acme raises its guidance for 2027", "https://a.com/1")], NOW, absorb=True)
        assert news.pending_count(conn) == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM news").fetchone()["n"] == 1

    def test_absorption_does_not_suppress_the_next_genuine_item(self, conn):
        news.store(conn, [item("Acme cuts full-year outlook", "https://a.com/1")], NOW, absorb=True)
        news.store(conn, [item("Beta Corp acquires Gamma for $4bn", "https://a.com/2")], NOW)
        assert news.pending_count(conn) == 1

    def test_pending_is_scoped_by_ticker(self, conn):
        news.store(
            conn,
            [
                item("Nvidia raises its guidance for 2027", "https://a.com/1", ticker="NVDA"),
                item("AMD wins $2bn defence contract", "https://b.com/2", ticker="AMD"),
            ],
            NOW,
        )
        assert news.pending_count(conn, ["NVDA"]) == 1
        assert news.pending_count(conn) == 2


class TestMarkNotified:
    def test_stamping_clears_the_pending_count(self, conn):
        news.store(conn, [item("Acme raises its guidance for 2027", "https://a.com/1")], NOW)
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


class TestMaterialityAtIntake:
    """The classifier runs on the way in, not on the way out.

    Judging at intake fixes the ranking once and keeps the cron gate honest: a
    poll that pulled nothing but listicles must leave `pending_count` at zero,
    or the wrapper invokes the agent to say there is nothing to say.
    """

    def test_a_material_headline_is_pending(self, conn):
        news.store(conn, [item("Acme raises its guidance for 2027", "https://a.com/1")], NOW)
        assert news.pending_count(conn) == 1

    def test_a_listicle_is_suppressed_on_the_way_in(self, conn):
        news.store(conn, [item("3 AI Stocks to Buy Now", "https://a.com/1")], NOW)
        assert news.pending_count(conn) == 0

    def test_a_bare_price_move_is_suppressed(self, conn):
        # The setup detector describes price action with a pivot and a volume
        # ratio. A headline saying the same thing vaguely is not an alert.
        news.store(conn, [item("Nvidia stock rises as it bets on robots", "https://a.com/1")], NOW)
        assert news.pending_count(conn) == 0

    def test_suppressed_rows_are_still_stored_for_context(self, conn):
        news.store(conn, [item("3 AI Stocks to Buy Now", "https://a.com/1")], NOW)
        row = conn.execute("SELECT suppressed, event_class FROM news").fetchone()
        assert row["suppressed"] == 1
        assert row["event_class"] == "noise"

    def test_the_impact_verdict_is_recorded_on_the_row(self, conn):
        news.store(conn, [item("Acme raises its guidance for 2027", "https://a.com/1")], NOW)
        row = conn.execute("SELECT event_class, materiality, band FROM news").fetchone()
        assert row["event_class"] == "guidance"
        assert row["materiality"] > 0
        assert row["band"] in {"low", "medium", "high"}

    def test_a_held_ticker_scores_above_a_merely_watched_one(self, conn):
        news.store(conn, [item("Acme wins $2bn defence contract", "https://a.com/1")], NOW)
        news.store(
            conn,
            [item("Acme wins $2bn defence contract", "https://b.com/2", ticker="AMD")],
            NOW,
            held={"AMD"},
        )
        rows = {r["ticker"]: r["materiality"] for r in conn.execute("SELECT ticker, materiality FROM news")}
        assert rows["AMD"] > rows["NVDA"]

    def test_a_suppressed_row_never_reaches_pending_even_when_asked_by_ticker(self, conn):
        news.store(conn, [item("Better Buy: Nvidia vs AMD", "https://a.com/1")], NOW)
        assert news.pending(conn, tickers=["NVDA"]) == []


class TestPendingRanking:
    def test_pending_returns_highest_impact_first(self, conn):
        news.store(
            conn,
            [
                item("Analysts raise price target on Acme", "https://a.com/1"),
                item("Acme raises its guidance for 2027", "https://b.com/2"),
            ],
            NOW,
        )
        stories = news.pending(conn)
        assert [s.verdict.event_class for s in stories] == ["guidance", "analyst"]

    def test_the_floor_filters_out_the_merely_interesting(self, conn):
        news.store(
            conn,
            [
                item("Analysts raise price target on Acme", "https://a.com/1"),
                item("Acme raises its guidance for 2027", "https://b.com/2"),
            ],
            NOW,
        )
        assert len(news.pending(conn)) == 2
        assert [s.verdict.event_class for s in news.pending(conn, floor=50)] == ["guidance"]

    def test_corroboration_can_promote_a_story(self, conn):
        """Scored over the cluster, not over whichever copy landed first."""
        news.store(conn, [item("Acme wins a defence contract", "https://a.com/1")], NOW)
        alone = news.pending(conn)[0].verdict.score
        news.store(
            conn,
            [
                item("Acme wins a defence contract today", "https://b.com/2", source="Bloomberg"),
                item("Acme wins a defence contract now", "https://c.com/3", source="CNBC"),
            ],
            NOW,
        )
        stories = news.pending(conn)
        assert len(stories) == 1
        assert stories[0].verdict.score > alone


class TestPerTickerDedupe:
    def test_the_same_story_reaches_every_ticker_that_declared_the_peer(self, conn):
        """One AMD story is news for NVDA and for CBRS alike.

        A global UNIQUE on url_hash gave it to whichever entry was polled first.
        Observed live: NVDA lost all five of its AMD peer stories to CBRS.
        """
        url = "https://a.com/amd-launches"
        news.store(
            conn,
            [
                item("AMD launches a rival GPU chip", url, ticker="CBRS", peer="AMD"),
                item("AMD launches a rival GPU chip", url, ticker="NVDA", peer="AMD"),
            ],
            NOW,
        )
        assert news.pending_count(conn, ["CBRS"]) == 1
        assert news.pending_count(conn, ["NVDA"]) == 1

    def test_but_the_same_story_is_still_stored_once_per_ticker(self, conn):
        url = "https://a.com/amd-launches"
        news.store(conn, [item("AMD launches a rival GPU chip", url, ticker="NVDA")], NOW)
        again = news.store(conn, [item("AMD launches a rival GPU chip", url, ticker="NVDA")], NOW)
        assert again == 0


class TestUnclassifiedSuppression:
    def test_an_unrecognised_headline_never_reaches_a_report(self, conn):
        news.store(conn, [item("Acme opens its Guangzhou facility", "https://a.com/1")], NOW)
        assert news.pending_count(conn) == 0

    def test_but_it_is_stored_and_countable(self, conn):
        """The cost of suppressing unclassified is a real event lost silently.

        The row and its class survive so the loss is auditable: an unclassified
        count climbing run over run means a pattern is missing, not that the
        news went quiet.
        """
        news.store(conn, [item("Acme opens its Guangzhou facility", "https://a.com/1")], NOW)
        assert news.suppression_breakdown(conn) == {"unclassified": 1}

    def test_a_recognised_event_is_unaffected(self, conn):
        news.store(conn, [item("Acme raises its guidance for 2027", "https://a.com/1")], NOW)
        assert news.pending_count(conn) == 1
        assert news.suppression_breakdown(conn) == {}
