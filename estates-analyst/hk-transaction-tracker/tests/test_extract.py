"""Payload to transactions: the 買賣/租賃 split, and what gets dropped."""

from __future__ import annotations

import copy

import pytest

from hk_transaction_tracker import extract, nuxt
from hk_transaction_tracker.errors import ParseError


@pytest.fixture
def extraction(page_html):
    return extract.extract(nuxt.decode(page_html), "泓都")


def test_yields_both_sides_of_the_market(extraction):
    sides = {record.deal_type for record in extraction.records}
    assert sides == {"sale", "rental"}


def test_car_parks_are_dropped_and_counted(extraction):
    """A 車位 has no 間隔 and no business in a 呎價 median."""
    assert extraction.skipped["non_residential"] == 1
    assert len(extraction.records) == 23


def test_published_count_is_the_source_total_not_the_page(extraction):
    """286 transactions exist; 24 were served. Both facts are reported."""
    assert extraction.published_count == 286
    assert len(extraction.records) < extraction.published_count


def test_a_sale_carries_registry_fields(extraction):
    sale = next(record for record in extraction.records if record.deal_type == "sale")
    assert sale.price > 1_000_000
    assert sale.reg_date is not None          # 登記日期: land registry
    assert sale.saleable_unit_price > 1_000   # 呎價, in thousands


def test_a_rental_has_no_registration_and_a_small_unit_price(extraction):
    """呎租 sits in the same field as 呎價 and is two orders of magnitude smaller."""
    rental = next(record for record in extraction.records if record.deal_type == "rental")
    assert rental.reg_date is None
    assert rental.price < 200_000             # a monthly rent
    assert rental.saleable_unit_price < 500   # 呎租


def test_every_record_has_a_transaction_date(extraction):
    """insDate is the only date on both sides; regDate is null on every rental."""
    assert all(record.ins_date is not None for record in extraction.records)


def test_an_empty_listing_is_a_parse_error_not_a_quiet_estate():
    """Exactly what asking for size=101 produces, and it must never read as 'no news'."""
    payload = {"state": {"transaction": {"transactionList": {}, "transactionSearch": {}}}}
    with pytest.raises(ParseError) as caught:
        extract.extract(payload, "泓都")
    assert "empty transactionList" in caught.value.message


def test_a_moved_payload_is_a_parse_error():
    with pytest.raises(ParseError):
        extract.extract({"state": {}}, "泓都")


def _one_row(page_html, **changes):
    payload = copy.deepcopy(nuxt.decode(page_html))
    listing = payload["state"]["transaction"]["transactionList"]
    row = dict(listing["data"][0])
    row.update(changes)
    listing["data"] = [row]
    return extract.extract(payload, "泓都")


def test_zero_is_not_an_area(page_html):
    """Centanet uses 0 and null interchangeably for 'not published'."""
    result = _one_row(page_html, nArea=0, nUnitPrice=0)
    assert result.records[0].saleable_area is None
    assert result.records[0].area_missing


def test_a_withheld_price_is_dropped(page_html):
    result = _one_row(page_html, transactionPrice=None)
    assert result.records == ()
    assert result.skipped["no_price"] == 1


def test_an_unknown_post_type_warns_rather_than_guessing(page_html):
    result = _one_row(page_html, postType="X")
    assert result.records == ()
    assert result.skipped["unknown_post_type"] == 1
    assert "X" in result.warnings[0]
