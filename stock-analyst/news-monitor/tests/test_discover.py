"""The gate: what becomes a row, and what that row's enable state is."""

from __future__ import annotations

import pytest

from news_monitor import discover
from news_monitor.errors import CandidateError

GATE = {"min_items": 3, "max_age_days": 30, "min_finance_hits": 3}


def _probe(url, taxonomy, fetcher, routes):
    return discover.probe(url, taxonomy, fetcher=lambda u: fetcher(routes)(u))


def test_a_live_finance_feed_passes_and_is_enabled(taxonomy, fetcher, now):
    result = _probe("https://wire.example.com/rss", taxonomy, fetcher,
                    {"https://wire.example.com/rss": "rss20.xml"})
    verdict = discover.judge(result, now, GATE)

    assert verdict.passed
    assert verdict.enable
    assert len(result.finance_hits) >= 3
    assert "macro-rates" in result.sectors


def test_an_html_page_is_rejected_outright(taxonomy, fetcher):
    with pytest.raises(CandidateError) as caught:
        _probe("https://paywall.example.com/feed", taxonomy, fetcher,
               {"https://paywall.example.com/feed": "notafeed.html"})
    assert caught.value.detail["reason"] == "not_a_feed"


def test_an_unreachable_url_is_rejected_outright(taxonomy, fetcher):
    with pytest.raises(CandidateError) as caught:
        _probe("https://gone.example.com/feed", taxonomy, fetcher, {})
    assert caught.value.detail["reason"] == "unreachable"


def test_an_off_topic_feed_is_stored_but_held_back(taxonomy, fetcher, now):
    result = _probe("https://delta.example.org/rss", taxonomy, fetcher,
                    {"https://delta.example.org/rss": "hobby.xml"})
    verdict = discover.judge(result, now, GATE)

    assert not verdict.enable
    assert verdict.reason == "off_topic"
    # Held back, not discarded: the reason and the evidence are on the row.
    assert verdict.detail["items"] == 3


def test_a_feed_whose_newest_story_is_years_old_is_stale(taxonomy, fetcher, now):
    result = _probe("https://epsilon.example.com/rss", taxonomy, fetcher,
                    {"https://epsilon.example.com/rss": "abandoned.xml"})
    verdict = discover.judge(result, now, GATE)

    assert not verdict.enable
    assert verdict.reason == "stale"


def test_a_thin_feed_is_held_back(taxonomy, fetcher, now):
    result = _probe("https://wire.example.com/rss", taxonomy, fetcher,
                    {"https://wire.example.com/rss": "rss20.xml"})
    verdict = discover.judge(result, now, {**GATE, "min_items": 10})

    assert verdict.reason == "thin"


def test_undated_items_are_not_called_stale(taxonomy, fetcher, now):
    """Undated is a formatting choice, not evidence a feed was abandoned."""
    document = (
        '<rss version="2.0"><channel><title>Undated Markets</title>'
        "<description>Equity market and bond yield coverage for investors.</description>"
        + "".join(
            f"<item><title>Stocks and treasury yields move {i}</title>"
            f"<link>https://u.example/{i}</link>"
            "<description>Equity investors watched the bond market.</description></item>"
            for i in range(4)
        )
        + "</channel></rss>"
    )
    from news_monitor.fetch import Response

    result = discover.probe(
        "https://u.example/rss", taxonomy,
        fetcher=lambda url: Response(url=url, status=200, text=document),
    )
    assert discover.judge(result, now, GATE).passed


def test_slug_drops_the_feed_boilerplate():
    assert discover.slugify("Reuters Business News RSS", "reuters.com") == "reuters-business"
    assert discover.slugify("", "feeds.example.com") == "feeds-example-com"


def test_unique_name_suffixes_rather_than_colliding():
    assert discover.unique_name("reuters", {"reuters"}) == "reuters-2"
    assert discover.unique_name("reuters", {"reuters", "reuters-2"}) == "reuters-3"
    assert discover.unique_name("reuters", set()) == "reuters"
