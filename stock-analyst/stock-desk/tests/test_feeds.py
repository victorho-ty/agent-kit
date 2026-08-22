"""Normalising two vendors, and deciding what is actually about us.

The payload fixtures here are trimmed copies of live responses. The fields kept
are the ones :mod:`stock_desk.feeds` reads; the several kilobytes of thumbnail
resolutions each real item also carries are the reason none of this is allowed
near model context.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stock_desk import feeds
from stock_desk.models import TickerConfig

NVDA = feeds.aliases("NVDA", "NVIDIA Corporation")


def yahoo_story(title, url="https://finance.yahoo.com/news/x.html", kind="STORY", provider="Reuters"):
    return {
        "id": "abc",
        "content": {
            "contentType": kind,
            "title": title,
            "summary": "Some summary text.",
            "pubDate": "2026-08-21T14:47:00Z",
            "provider": {"displayName": provider},
            "canonicalUrl": {"url": url},
        },
    }


def av_story(title, tickers, url="https://example.com/a", source="Reuters"):
    """``tickers`` is ``[(symbol, relevance, sentiment)]`` in any order."""
    return {
        "title": title,
        "url": url,
        "source": source,
        "summary": "Some summary text.",
        "time_published": "20260821T144700",
        "ticker_sentiment": [
            {
                "ticker": sym,
                "relevance_score": f"{rel:.6f}",
                "ticker_sentiment_score": f"{sen:.6f}",
                "ticker_sentiment_label": "Bullish" if sen > 0.15 else "Neutral",
            }
            for sym, rel, sen in tickers
        ],
    }


class TestAliases:
    def test_symbol_and_name_and_short_name(self):
        assert feeds.aliases("NVDA", "NVIDIA Corporation") == ("nvda", "nvidia corporation", "nvidia")

    def test_legal_suffixes_are_stripped(self):
        assert "rigetti computing" in feeds.aliases("RGTI", "Rigetti Computing Inc")
        assert "d-wave quantum" in feeds.aliases("QBTS", "D-Wave Quantum Inc")

    def test_a_short_symbol_is_not_an_alias(self):
        # `T`, `ALL` and `KEY` are real tickers; a two-letter alias matches
        # inside ordinary words and would file half the feed under the wrong name.
        assert "t" not in feeds.aliases("T")
        assert feeds.aliases("T") == ()

    def test_a_numeric_hk_symbol_is_found_by_name_not_by_number(self):
        # "0700" matches a year, a price or a time as readily as it matches
        # Tencent, and no journalist writes it either way.
        assert feeds.aliases("0700.HK") == ()
        assert feeds.aliases("0700.HK", "Tencent Holdings") == ("tencent holdings", "tencent")
        assert feeds.mentions("Tencent lifts its dividend", "0700.HK",
                              feeds.aliases("0700.HK", "Tencent Holdings"))
        assert not feeds.mentions("Revenue rose to 0700 million", "0700.HK",
                                  feeds.aliases("0700.HK", "Tencent Holdings"))

    def test_no_name_still_gives_the_symbol(self):
        assert feeds.aliases("NVDA") == ("nvda",)


class TestMentions:
    def test_matches_the_company_name(self):
        assert feeds.mentions("Nvidia in talks with Rebellions", "NVDA", NVDA)

    def test_matches_the_bare_symbol(self):
        assert feeds.mentions("NVDA hits a new high", "NVDA", NVDA)

    def test_does_not_match_a_different_company(self):
        assert not feeds.mentions("IREN Clears Its Biggest AI Test With Microsoft", "NVDA", NVDA)

    def test_a_short_symbol_still_matches_its_explicit_forms(self):
        assert feeds.mentions("AT&T ($T) raises its dividend", "T", feeds.aliases("T"))
        assert feeds.mentions("AT&T (T) raises its dividend", "T", feeds.aliases("T"))

    def test_a_short_symbol_does_not_match_a_bare_letter(self):
        assert not feeds.mentions("The quick brown fox", "T", feeds.aliases("T"))

    def test_substrings_do_not_count(self):
        assert not feeds.mentions("Investors eye nvidian prospects", "NVDA", ("nvidia",))


class TestTimestamps:
    def test_parses_the_yahoo_shape(self):
        assert feeds._iso("2026-08-21T14:47:00Z").startswith("2026-08-21T14:47:00")

    def test_parses_the_alphavantage_shape(self):
        assert feeds._iso("20260821T144700").startswith("2026-08-21T14:47:00")

    def test_an_unparseable_stamp_is_none_not_now(self):
        # Defaulting to now would sort the story to the top of every report.
        assert feeds._iso("last Tuesday") is None
        assert feeds._iso(None) is None

    def test_av_time_from_round_trips(self):
        moment = datetime(2026, 8, 21, 14, 47, tzinfo=timezone.utc)
        assert feeds.av_time_from(moment) == "20260821T1447"


class TestYahooNormalisation:
    def test_an_on_topic_story_is_kept(self):
        built = feeds._yahoo_item(yahoo_story("Nvidia in talks with Rebellions"), "NVDA", None, NVDA)
        assert built is not None
        assert built.feed == "yahoo"
        assert built.source == "Reuters"
        assert built.sentiment_score is None

    def test_video_items_are_dropped(self):
        # Two of ten on a typical response, and they carry a player, not prose.
        assert feeds._yahoo_item(yahoo_story("Nvidia video", kind="VIDEO"), "NVDA", None, NVDA) is None

    def test_an_off_topic_story_is_dropped(self):
        # Yahoo's NVDA feed genuinely opens with items like this.
        assert feeds._yahoo_item(
            yahoo_story("Micron's AI boom rolls on with $10 billion Iowa data center"),
            "NVDA", None, NVDA,
        ) is None

    def test_an_item_with_no_url_is_dropped(self):
        raw = yahoo_story("Nvidia does something")
        raw["content"]["canonicalUrl"] = {}
        raw["content"]["clickThroughUrl"] = {}
        assert feeds._yahoo_item(raw, "NVDA", None, NVDA) is None

    def test_a_peer_story_is_filed_under_the_watched_ticker(self):
        built = feeds._yahoo_item(
            yahoo_story("AMD launches a rival part"), "NVDA", "AMD", feeds.aliases("AMD")
        )
        assert built is not None
        assert built.ticker == "NVDA"
        assert built.peer_of == "AMD"


class TestAlphaVantageSubjectGate:
    def test_top_ranked_ticker_is_the_subject(self):
        built = feeds._av_item(
            av_story("Chipmaker signs a supply deal", [("NVDA", 0.95, 0.4), ("DELL", 0.6, 0.1)]),
            "NVDA", None, NVDA,
        )
        assert built is not None
        assert built.sentiment_label == "Bullish"
        assert built.relevance == pytest.approx(0.95)

    def test_a_high_relevance_article_about_someone_else_is_dropped(self):
        # The live case: a TSSI earnings transcript scored NVDA at 0.895, which
        # clears any absolute relevance floor worth setting.
        assert feeds._av_item(
            av_story("TSS (TSSI) Q2 2026 Earnings Call Transcript",
                     [("TSSI", 1.0, 0.3), ("NVDA", 0.895, 0.2), ("DELL", 0.715, 0.1)]),
            "NVDA", None, NVDA,
        ) is None

    def test_a_named_company_survives_even_when_not_top_ranked(self):
        # Four of the seven genuine NVDA stories in the live sample were not the
        # top-ranked ticker. The alias check is what keeps them.
        built = feeds._av_item(
            av_story("Broadcom steps up Nvidia challenge",
                     [("AVGO", 1.0, 0.3), ("NVDA", 0.7, -0.1)]),
            "NVDA", None, NVDA,
        )
        assert built is not None

    def test_the_stored_sentiment_is_ticker_level_not_article_level(self):
        built = feeds._av_item(
            av_story("Chipmaker signs a supply deal", [("NVDA", 0.95, -0.42)]),
            "NVDA", None, NVDA,
        )
        assert built.sentiment_score == pytest.approx(-0.42)

    def test_an_item_with_no_ticker_sentiment_falls_back_to_the_alias_check(self):
        raw = av_story("Nvidia raises its guidance", [])
        built = feeds._av_item(raw, "NVDA", None, NVDA)
        assert built is not None
        assert built.sentiment_score is None


class TestYahooRequestPlan:
    def test_the_entry_and_each_competitor_get_a_feed(self):
        entry = TickerConfig(
            ticker="NVDA", company_name="NVIDIA Corporation", competitors=["AMD", "AVGO"]
        )
        plan = feeds.yahoo_requests(entry)
        assert [symbol for symbol, _, _, _ in plan] == ["NVDA", "AMD", "AVGO"]
        assert [peer for _, _, peer, _ in plan] == [None, "AMD", "AVGO"]
        assert all(filed == "NVDA" for _, filed, _, _ in plan)

    def test_a_peer_too_short_to_alias_is_skipped(self):
        # A two-letter peer symbol would match inside ordinary words, so it
        # contributes nothing and is dropped rather than filed as noise.
        entry = TickerConfig(ticker="NVDA", company_name="NVIDIA Corporation", competitors=["T"])
        assert [symbol for symbol, _, _, _ in feeds.yahoo_requests(entry)] == ["NVDA"]
