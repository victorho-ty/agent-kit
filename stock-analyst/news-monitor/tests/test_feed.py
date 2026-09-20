"""Parsing, across the three dialects, and the identity that dedupes them."""

from __future__ import annotations

from news_monitor import feed as feed_parser

from .conftest import fixture_text


def test_rss20_yields_entries_with_links_and_summaries():
    entries = feed_parser.parse(fixture_text("rss20.xml"), "acme")

    assert [entry.title for entry in entries] == [
        "Fed holds rates steady, signals one cut this year",
        "CPI rises 0.2% in August as shelter inflation cools",
        "Nvidia earnings beat estimates on data center demand",
    ]
    assert entries[0].summary == (
        "The FOMC left the federal funds target unchanged and its median dot "
        "showed a single cut."
    )
    assert entries[0].published_text == "Thu, 18 Sep 2026 18:02:00 GMT"


def test_untitled_item_is_dropped_rather_than_given_a_title():
    entries = feed_parser.parse(fixture_text("rss20.xml"), "acme")
    assert all("untitled" not in entry.url for entry in entries)


def test_tracking_parameters_are_stripped_from_the_fingerprint():
    entries = feed_parser.parse(fixture_text("rss20.xml"), "acme")
    assert entries[0].url == "https://wire.example.com/markets/fed-holds"
    assert entries[0].fingerprint == entries[0].url


def test_atom_prefers_the_alternate_link_over_replies():
    entries = feed_parser.parse(fixture_text("atom.xml"), "beta")
    assert entries[0].url == "https://beta.example.org/notices/yields"
    assert entries[1].summary == "An enforcement settlement was reached with the member firm."


def test_rdf_items_are_found_through_their_namespace():
    entries = feed_parser.parse(fixture_text("rdf.xml"), "gamma")
    assert len(entries) == 3
    assert entries[0].url == "https://gamma.example.net/releases/payrolls"
    assert entries[0].published_text == "2026-09-05T12:30:00Z"


def test_same_article_from_two_feeds_shares_one_fingerprint():
    """The dedupe that makes two wires carrying one story one item."""
    first = feed_parser.parse(fixture_text("rss20.xml"), "dj-markets")[0]
    second = feed_parser.parse(fixture_text("rss20.xml"), "marketwatch-top")[0]
    assert first.fingerprint == second.fingerprint


def test_channel_title_is_read_rather_than_the_first_item_title():
    assert feed_parser.document_title(fixture_text("rss20.xml")) == "Acme Wire Markets"
    assert feed_parser.document_title(fixture_text("atom.xml")) == "Beta Exchange Notices"
    assert feed_parser.document_description(fixture_text("rdf.xml")) == (
        "Official releases: employment, prices, output."
    )


def test_html_page_is_a_fetch_failure_not_a_silent_empty_feed():
    """A paywall served with a 200 is a fetch problem wearing a parse problem's clothes."""
    import pytest

    from news_monitor.errors import FetchError

    with pytest.raises(FetchError):
        feed_parser.parse(fixture_text("notafeed.html"), "candidate")


def test_wellformed_xml_with_no_items_parses_to_nothing_rather_than_raising():
    """Distinct from the case above, and it must stay distinct.

    A tracked feed that has emptied out is ``zero_yield`` -- a real condition
    with its own report -- not a fetch failure. Only ``discover`` treats an
    itemless document as disqualifying, because there it means the candidate is
    not a feed.
    """
    document = '<rss version="2.0"><channel><title>Quiet</title></channel></rss>'
    assert feed_parser.parse(document, "quiet") == []


def test_summary_is_capped_with_an_ellipsis():
    document = (
        '<rss version="2.0"><channel><title>t</title>'
        "<item><title>Long one</title><link>https://x.example/1</link>"
        f"<description>{'word ' * 400}</description></item>"
        "</channel></rss>"
    )
    entry = feed_parser.parse(document, "x", summary_cap=100)[0]
    assert len(entry.summary) <= 104
    assert entry.summary.endswith("...")


def test_parse_date_accepts_both_spellings_and_defaults_to_utc():
    rfc822 = feed_parser.parse_date("Thu, 18 Sep 2026 18:02:00 GMT")
    iso = feed_parser.parse_date("2026-09-18T18:02:00Z")
    naive = feed_parser.parse_date("2026-09-18T18:02:00")
    assert rfc822 == iso == naive
    assert feed_parser.parse_date("not a date") is None
    assert feed_parser.parse_date(None) is None
