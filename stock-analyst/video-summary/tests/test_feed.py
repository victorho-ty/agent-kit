"""Parsing a channel feed, including the parts that bite."""

from __future__ import annotations

import pytest

from video_summary import feed as feed_parser
from video_summary.errors import FetchError


def test_entries_carry_id_title_url_and_thumbnail(feed_document):
    entries = feed_parser.parse(feed_document, "rates-desk")
    assert [entry.video_id for entry in entries] == ["aaaaaaaaaa1", "bbbbbbbbbb2", "cccccccccc3"]
    first = entries[0]
    assert first.url == "https://www.youtube.com/watch?v=aaaaaaaaaa1"
    assert first.thumbnail_url == "https://i4.ytimg.com/vi/aaaaaaaaaa1/hqdefault.jpg"
    assert first.channel == "Example Rates Desk"
    assert first.published_text == "2026-08-23T12:00:00+00:00"


def test_entities_are_decoded_once(feed_document):
    """A double-escaped title reaches the reader as mojibake if left alone."""
    first = feed_parser.parse(feed_document, "rates-desk")[0]
    assert first.title == "The 10–Year Yield’s Next Move"


def test_max_items_is_honoured(feed_document):
    assert len(feed_parser.parse(feed_document, "rates-desk", max_items=2)) == 2


def test_a_non_xml_document_is_a_fetch_problem(feed_document):
    """A captive portal or an error page served with a 200.

    Well-formed HTML parses and yields nothing, which the zero-yield guard
    catches; malformed HTML does not parse at all, and that is this case.
    """
    with pytest.raises(FetchError, match="not parseable as XML"):
        feed_parser.parse("<html><body><p>Sign in to continue</body></html>", "rates-desk")


def test_a_page_that_parses_but_is_not_a_feed_yields_nothing(feed_document):
    assert feed_parser.parse("<html><body>Sign in</body></html>", "rates-desk") == []


def test_published_text_is_never_parsed(feed_document):
    """It is a string all the way through. Nothing here needs the value."""
    first = feed_parser.parse(feed_document, "rates-desk")[0]
    assert isinstance(first.published_text, str)


def test_kind_reads_the_redirect():
    assert feed_parser.kind_of("x", lambda url: url) == "short"
    assert feed_parser.kind_of("x", lambda url: "https://www.youtube.com/watch?v=x") == "video"
    assert feed_parser.kind_of("x", lambda url: None) == "unknown"
