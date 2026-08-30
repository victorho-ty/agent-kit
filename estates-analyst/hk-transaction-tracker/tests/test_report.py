"""The summary: grouping, the delivery ledger, and the finished strings."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from hk_transaction_tracker import db, fmt, report
from hk_transaction_tracker.config.estates import SizeRange
from hk_transaction_tracker.match import MatchResult

from .conftest import make_transaction

NOW = datetime(2026, 8, 29, 12, 0)


@pytest.fixture
def config(config_factory):
    return config_factory([
        {
            "name": "泓都", "label": "泓都 Island Harbourview",
            "url": "https://hk.centanet.com/findproperty/list/transaction/a_2-A",
            "bedrooms": [2, 3], "size_ranges": [[500, 700]],
        },
        {
            "name": "海盈山", "label": "港島南岸 4A期 海盈山",
            "url": "https://hk.centanet.com/findproperty/list/transaction/b_2-B",
            "bedrooms": [2, 3], "size_ranges": [[600, 900]],
        },
    ])


def add(conn, **overrides):
    band = overrides.pop("band", SizeRange(500, 700))
    area_missing = overrides.pop("area_missing", False)
    transaction = make_transaction(**overrides)
    db.insert_transaction(
        conn, transaction,
        MatchResult(
            matched=True, reason="test",
            size_range=None if area_missing else band,
            area_missing=area_missing,
        ),
        NOW,
    )


def test_no_news_produces_no_message(config, conn):
    payload = report.build(config, conn=conn, draw=False)
    assert payload["new_count"] == 0
    assert payload["summary_lines"] == []
    assert payload["images"] == []


def test_sales_come_before_rentals(config, conn):
    add(conn, tx_id="r1", deal_type="rental", price=25_800, saleable_unit_price=51)
    add(conn, tx_id="s1", deal_type="sale")
    payload = report.build(config, conn=conn, draw=False)
    assert [group["deal_type"] for group in payload["groups"]] == ["sale", "rental"]


def test_estates_keep_their_config_order(config, conn):
    add(conn, tx_id="b1", estate="海盈山", saleable_area=680, band=SizeRange(600, 900))
    add(conn, tx_id="a1", estate="泓都")
    payload = report.build(config, conn=conn, draw=False)
    assert [estate["estate"] for estate in payload["groups"][0]["estates"]] == ["泓都", "海盈山"]


def test_grouping_runs_estate_then_bedrooms_then_size(config, conn):
    add(conn, tx_id="two", bedrooms=2, saleable_area=507)
    add(conn, tx_id="three", bedrooms=3, saleable_area=660)
    estate = report.build(config, conn=conn, draw=False)["groups"][0]["estates"][0]
    assert [group["bedroom_label"] for group in estate["bedroom_groups"]] == ["2房", "3房"]
    assert estate["bedroom_groups"][0]["size_groups"][0]["size_label"] == "500-700呎"


def test_transactions_without_an_area_get_their_own_group(config, conn):
    add(conn, tx_id="ok", bedrooms=2, saleable_area=507)
    add(
        conn, tx_id="pending", bedrooms=3,
        saleable_area=None, saleable_unit_price=None, area_missing=True,
    )
    estate = report.build(config, conn=conn, draw=False)["groups"][0]["estates"][0]
    assert len(estate["area_pending"]) == 1
    assert estate["area_pending"][0]["unit_price_text"] == fmt.EM_DASH
    # and it is not hiding in a size bucket
    sized = [item for group in estate["bedroom_groups"]
             for size in group["size_groups"] for item in size["items"]]
    assert [item["tx_id"] for item in sized] == ["ok"]


def test_commit_stamps_the_ledger_and_the_next_report_is_empty(config, conn):
    add(conn, tx_id="s1")
    first = report.build(config, conn=conn, draw=False, commit=True)
    assert first["new_count"] == 1
    assert first["committed_rows"] == 1
    assert db.pending_count(conn) == 0
    assert report.build(config, conn=conn, draw=False)["new_count"] == 0


def test_without_commit_nothing_is_stamped(config, conn):
    """Asking for a copy of the report must not consume the pending flag."""
    add(conn, tx_id="s1")
    report.build(config, conn=conn, draw=False)
    assert db.pending_count(conn) == 1


def test_summary_lines_are_finished_strings(config, conn):
    add(conn, tx_id="s1", bedrooms=2, saleable_area=507, price=12_400_000,
        saleable_unit_price=24_458, ins_date=date(2026, 8, 17))
    lines = report.build(config, conn=conn, draw=False)["summary_lines"]
    body = "\n".join(lines)
    assert "【買賣】" in body
    assert "$1,240萬" in body        # 萬, not 12.4M
    assert "$24,458/呎" in body
    assert "2026-08-17" in body


def test_a_rental_line_says_rent_not_price(config, conn):
    add(conn, tx_id="r1", deal_type="rental", price=42_500, saleable_unit_price=57)
    body = "\n".join(report.build(config, conn=conn, draw=False)["summary_lines"])
    assert "$42,500/月" in body
    assert "$57/呎" in body


def test_a_backlog_is_capped_and_says_so(config, conn):
    for index in range(8):
        add(conn, tx_id=f"s{index}")
    payload = report.build(config, conn=conn, draw=False, limit=5)
    assert payload["new_count"] == 5
    assert payload["held_back"] == 3
    assert any("仍在待報名單內" in line for line in payload["summary_lines"])


def test_prices_are_written_the_way_hong_kong_writes_them():
    assert fmt.price(12_400_000, "sale") == "$1,240萬"
    assert fmt.price(35_474_000, "sale") == "$3,547.4萬"
    assert fmt.price(128_000_000, "sale") == "$1.28億"
    assert fmt.price(42_500, "rental") == "$42,500/月"
    assert fmt.price(None, "sale") == fmt.EM_DASH
