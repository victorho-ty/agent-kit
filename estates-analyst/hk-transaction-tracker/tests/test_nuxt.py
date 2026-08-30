"""The decoder, against a real captured payload and against the ways it fails."""

from __future__ import annotations

import pytest

from hk_transaction_tracker import nuxt
from hk_transaction_tracker.errors import ParseError


def test_decodes_a_real_page(page_html):
    payload = nuxt.decode(page_html)
    listing = payload["state"]["transaction"]["transactionList"]
    assert listing["count"] == 286
    assert len(listing["data"]) == 24


def test_resolves_the_symbol_table(page_html):
    """``count:c`` has to come back as 0, not as the string 'c'."""
    search = nuxt.decode(page_html)["state"]["transaction"]["transactionSearch"]
    assert search["offset"] == 0
    assert search["size"] == 24
    assert search["postType"] == "Both"      # both sides, in one list
    assert search["day"] == "Day1095"        # three years


def test_applies_the_prelude_assignments(page_html):
    """``ci[0]=E;`` before the return patches a shared argument by reference.

    Ignoring the prelude would leave the hole empty and produce a payload that
    parses but is quietly incomplete -- the failure mode worth a test.
    """
    search = nuxt.decode(page_html)["state"]["transaction"]["transactionSearch"]
    # Passed in as Array(1) -- a hole -- and filled by the prelude.
    assert search["bigestAndEstate"] == ["2-SSPPWPPYPS"]


def test_escaped_slashes_survive(page_html):
    """The minifier writes every URL slash as a  escape."""
    row = nuxt.decode(page_html)["state"]["transaction"]["transactionList"]["data"][0]
    assert row["detailUrl"].startswith("https://hk.centanet.com/findproperty/")


def test_a_page_without_a_payload_is_a_parse_error():
    with pytest.raises(ParseError) as caught:
        nuxt.decode("<html><body>nothing here</body></html>")
    assert "window.__NUXT__" in caught.value.message or "not be a Centanet" in caught.value.message


def test_an_unknown_identifier_names_itself():
    """A changed minifier must fail loudly rather than yield a partial payload."""
    html = "window.__NUXT__=(function(a){return {value:zz}}(1));"
    with pytest.raises(ParseError) as caught:
        nuxt.decode(html)
    assert "zz" in caught.value.message


def test_a_mismatched_symbol_table_is_caught():
    html = "window.__NUXT__=(function(a,b){return {value:a}}(1));"
    with pytest.raises(ParseError) as caught:
        nuxt.decode(html)
    assert caught.value.detail["parameters"] != caught.value.detail["arguments"]


def test_brackets_inside_strings_do_not_end_the_function():
    """An estate name with a parenthesis must not close the argument list early."""
    html = 'window.__NUXT__=(function(a,b){return {name:a,n:b}}("2座 (2A)",7));'
    assert nuxt.decode(html) == {"name": "2座 (2A)", "n": 7}


def test_array_holes_and_void():
    html = "window.__NUXT__=(function(a){return {holes:Array(2),nothing:void 0,flag:a}}(true));"
    assert nuxt.decode(html) == {"holes": [None, None], "nothing": None, "flag": True}
