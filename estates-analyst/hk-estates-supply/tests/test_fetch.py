"""Finding the publication on the index page. No network: the fixture is the real page."""

from __future__ import annotations

import pytest

from hk_estates_supply import fetch
from hk_estates_supply.errors import ParseError


def test_the_real_page_yields_the_current_quarter(index_html):
    publication = fetch.find_publication(index_html)
    assert publication.href == "stat202606.pdf"
    assert publication.quarter == "2026/Jun"
    assert publication.label == "2026年6月"
    assert publication.url.endswith("/pshpm/stat202606.pdf")


def test_a_leading_byte_order_mark_changes_nothing(index_html):
    assert fetch.find_publication("﻿" + index_html).quarter == "2026/Jun"


def test_the_newest_link_wins_not_the_first_one():
    """The page's ordering is not a contract; 202609 sorting after 202606 is."""
    html = (
        "<h1>私人住宅一手市場供應</h1>"
        '<p><a href="stat202606.pdf">2026年6月</a></p>'
        '<p><a href="stat202609.pdf">2026年9月</a></p>'
    )
    assert fetch.find_publication(html).quarter == "2026/Sep"


def test_links_before_the_section_heading_are_ignored():
    html = (
        '<a href="stat209912.pdf">2099年12月</a>'
        "<h1>私人住宅一手市場供應</h1>"
        '<a href="stat202606.pdf">2026年6月</a>'
    )
    assert fetch.find_publication(html).quarter == "2026/Jun"


def test_a_reworded_heading_falls_back_to_the_whole_page():
    """A reworded heading is not a reason to stop reporting for three months."""
    html = '<h1>私人住宅供應統計</h1><a href="stat202606.pdf">2026年6月</a>'
    assert fetch.find_publication(html).quarter == "2026/Jun"


def test_a_filename_that_disagrees_with_the_printed_date_is_refused():
    """A quarter filed under the wrong label is believed. A failure is not."""
    html = '<h1>私人住宅一手市場供應</h1><a href="stat202606.pdf">2026年3月</a>'
    with pytest.raises(ParseError) as caught:
        fetch.find_publication(html)
    assert caught.value.detail["page_label"] == "2026年3月"


def test_a_missing_date_beside_the_link_is_tolerated():
    html = '<h1>私人住宅一手市場供應</h1><a href="stat202606.pdf">PDF</a>'
    publication = fetch.find_publication(html)
    assert publication.quarter == "2026/Jun"
    assert publication.label is None


def test_no_link_at_all_is_a_parse_error_not_silence():
    """This is the failure that would otherwise present as "nothing new" forever."""
    with pytest.raises(ParseError) as caught:
        fetch.find_publication("<h1>私人住宅一手市場供應</h1><p>暫停發布</p>")
    assert caught.value.detail["heading_found"] is True


def test_a_non_quarter_end_month_is_refused():
    html = '<h1>私人住宅一手市場供應</h1><a href="stat202607.pdf">2026年7月</a>'
    with pytest.raises(ParseError) as caught:
        fetch.find_publication(html)
    assert caught.value.detail["month"] == 7


def test_a_far_away_date_is_not_mistaken_for_the_link_label():
    """Only the anchor's own neighbourhood is read, not the footer's date."""
    html = (
        "<h1>私人住宅一手市場供應</h1>"
        '<a href="stat202606.pdf">PDF</a>'
        + "<p>filler</p>" * 60
        + "<footer>最後更新日期: 2020年1月</footer>"
    )
    assert fetch.find_publication(html).label is None
