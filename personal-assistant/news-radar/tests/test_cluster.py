"""Clustering: the test that decides whether the digest is readable.

Everything here pulls in two directions at once. Merge too eagerly and the
digest invents connections between unrelated stories; merge too timidly and one
piece of news is repeated once per outlet, which is the spam that gets the whole
skill muted. Both failures are represented below on purpose.
"""

from __future__ import annotations

import pytest

from news_radar import cluster
from news_radar.config.sources import DEFAULT_CLUSTER_THRESHOLD
from news_radar.models import Item

THRESHOLD = DEFAULT_CLUSTER_THRESHOLD


def item(id: int, title: str, url: str, domain: str | None = None,
         source: str | None = None) -> Item:
    return Item(
        id=id, source=source or f"s{id}", item_key=f"k{id}", url=url, title=title,
        summary=None, detail_text=None, date_text=None,
        source_domain=domain or f"outlet{id}.example.com",
        first_seen_at="2026-08-11T14:00:00+08:00", digested_at=None, run_id=1,
    )


# --------------------------------------------------------------------------- must merge


@pytest.mark.parametrize("left, right", [
    # The canonical case: same story, house style differs.
    ("OpenAI releases GPT-X", "OpenAI Releases GPT-X, Its Biggest Model Yet"),
    # Stopwords and punctuation must not keep them apart.
    ("Central bank holds rates", "The central bank holds rates, again"),
    # Case and width folding, inherited from db.normalize.
    ("Storm warning issued", "STORM WARNING ISSUED"),
])
def test_rewordings_of_one_story_merge(left, right):
    stories = cluster.cluster(
        [item(1, left, "https://a.example/1"), item(2, right, "https://b.example/2")], THRESHOLD)
    assert len(stories) == 1
    assert stories[0].items[0].id == 1        # oldest wins: it supplies title and link


def test_identical_urls_merge_across_sources():
    """Straight syndication. Across outlets, the URL is the strongest signal."""
    stories = cluster.cluster([
        item(1, "Rates held", "https://wire.example/story/1", source="alpha"),
        item(2, "Completely different words here", "https://wire.example/story/1", source="beta"),
    ], THRESHOLD)
    assert len(stories) == 1


# --------------------------------------------------------------------------- must not merge


@pytest.mark.parametrize("left, right", [
    # Shares the subject, is not the same story. The failure that would make the
    # digest actively misleading.
    ("OpenAI releases GPT-X", "OpenAI hires a new chief financial officer"),
    ("Storm warning issued for the eastern seaboard", "Storm cleanup begins in the west"),
])
def test_different_stories_stay_apart(left, right):
    stories = cluster.cluster(
        [item(1, left, "https://a.example/1"), item(2, right, "https://b.example/2")], THRESHOLD)
    assert len(stories) == 2


def test_a_source_with_no_per_item_links_does_not_collapse():
    """Regression, found while configuring llm-stats.com/ai-news.

    That page renders its headlines in <button> elements with no href anywhere,
    so every item falls back to the page's own URL. Treating identical URLs as
    syndication regardless of source turned ten distinct stories into one, and
    the source would have gone almost silent without ever failing.
    """
    page = "https://llm-stats.com/ai-news"
    stories = cluster.cluster([
        item(1, "Nvidia releases Nemotron 3.5 Lightning, an open MoE model", page, source="llm-news"),
        item(2, "Spotify plans to roll out AI Persona labeling in September", page, source="llm-news"),
        item(3, "Apple's iOS 27 beta contains references to unreleased devices", page, source="llm-news"),
    ], THRESHOLD)
    assert len(stories) == 3


def test_a_shared_product_family_is_not_a_shared_story():
    """Regression, found against live feeds rather than imagined.

    Two different product announcements from one blog share {introducing, muse}
    and nothing else. At a 0.6 threshold that scored 2/3 and merged them into a
    story that never existed, which is what moved the default to 0.7. Short
    headlines are easily contained in longer ones, and this is the shape that
    mistake takes in the wild.
    """
    stories = cluster.cluster([
        item(1, "Introducing Muse Glimmer", "https://simonwillison.net/a"),
        item(2, "Introducing Muse Code and Muse Spark 1.2", "https://simonwillison.net/b"),
    ], THRESHOLD)
    assert len(stories) == 2


def test_a_feed_that_lists_one_post_twice_still_collapses():
    """The other half of the same live run: an identical title under two URLs.

    item_key keeps them apart at scan time because the URLs differ, so the
    clusterer is the only thing standing between the reader and a doubled line.
    """
    stories = cluster.cluster([
        item(1, "Now we have a timeline of the OpenAI attack", "https://blog.example/link/1"),
        item(2, "Now we have a timeline of the OpenAI attack", "https://blog.example/quote/2"),
    ], THRESHOLD)
    assert len(stories) == 1


def test_two_thin_headlines_are_not_merged():
    """'It begins' and 'It ends' share their only surviving token.

    With almost nothing left after stopword removal, any two thin headlines look
    identical, so the clusterer must decline rather than guess.
    """
    stories = cluster.cluster(
        [item(1, "It begins", "https://a.example/1"), item(2, "It ends", "https://b.example/2")],
        THRESHOLD)
    assert len(stories) == 2


# --------------------------------------------------------------------------- shape


def test_a_story_lists_every_outlet_once_in_order():
    stories = cluster.cluster([
        item(1, "OpenAI releases GPT-X", "https://a.example/1", "alpha.example.com"),
        item(2, "OpenAI Releases GPT-X, Its Biggest Model Yet", "https://b.example/2", "beta.example.org"),
        item(3, "OpenAI releases GPT-X today", "https://c.example/3", "alpha.example.com"),
    ], THRESHOLD)

    assert len(stories) == 1
    # alpha appears twice in the input and once in the label, oldest first.
    assert stories[0].domains == ("alpha.example.com", "beta.example.org")
    assert stories[0].to_dict()["ids"] == [1, 2, 3]


def test_the_primary_link_is_the_earliest_item():
    """The outlet that broke the story supplies the link a reader follows."""
    stories = cluster.cluster([
        item(1, "OpenAI releases GPT-X", "https://first.example/scoop"),
        item(2, "OpenAI Releases GPT-X, Its Biggest Model Yet", "https://second.example/follow"),
    ], THRESHOLD)
    assert stories[0].url == "https://first.example/scoop"


def test_clustering_is_deterministic():
    """Same input, same grouping -- a re-run of a digest must not reshuffle."""
    items = [
        item(1, "OpenAI releases GPT-X", "https://a.example/1"),
        item(2, "Storm warning issued", "https://b.example/2"),
        item(3, "OpenAI Releases GPT-X, Its Biggest Model Yet", "https://c.example/3"),
    ]
    first = [story.to_dict()["ids"] for story in cluster.cluster(items, THRESHOLD)]
    second = [story.to_dict()["ids"] for story in cluster.cluster(items, THRESHOLD)]
    assert first == second == [[1, 3], [2]]


def test_an_empty_input_is_no_stories():
    assert cluster.cluster([], THRESHOLD) == []


def test_the_threshold_is_the_dial():
    """Documented as tunable, so prove it actually changes the outcome."""
    pair = [item(1, "Rates held steady by the central bank", "https://a.example/1"),
            item(2, "Central bank holds rates", "https://b.example/2")]
    assert len(cluster.cluster(pair, 0.4)) == 1
    assert len(cluster.cluster(pair, 0.95)) == 2
