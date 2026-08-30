"""呎價(實) direction: the two windows, the sample floor, and the truncated month."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from hk_transaction_tracker import db, trend
from hk_transaction_tracker.match import MatchResult

from .conftest import make_transaction

NOW = datetime(2026, 8, 29, 12, 0)
TODAY = date(2026, 8, 29)
MATCH = MatchResult(matched=True, reason="test")


def add(conn, day: date, unit_price: float, *, deal_type="sale", estate="泓都", tx_id=None):
    transaction = make_transaction(
        estate=estate,
        tx_id=tx_id or f"{estate}-{deal_type}-{day}-{unit_price}",
        deal_type=deal_type,
        ins_date=day,
        saleable_unit_price=unit_price,
        saleable_area=500.0,
        price=unit_price * 500,
    )
    db.insert_transaction(conn, transaction, MATCH, NOW)


def test_a_rise_is_measured_against_the_preceding_window(conn):
    for offset in (5, 20, 40):
        add(conn, TODAY - timedelta(days=offset), 22_000)
    for offset in (100, 120, 150):
        add(conn, TODAY - timedelta(days=offset), 20_000)

    result = trend.bucket_trend(conn, "泓都", "sale", TODAY)
    assert result["basis"] == "ok"
    assert result["recent"]["median_unit_price"] == 22_000
    assert result["previous"]["median_unit_price"] == 20_000
    assert result["pct"] == 10.0
    assert result["direction"] == "up"


def test_too_few_samples_is_named_not_expressed_as_zero(conn):
    """'Not enough transactions to say' and 'the market did not move' differ."""
    add(conn, TODAY - timedelta(days=5), 22_000)
    add(conn, TODAY - timedelta(days=100), 20_000)

    result = trend.bucket_trend(conn, "泓都", "sale", TODAY)
    assert result["basis"] == "insufficient"
    assert result["pct"] is None
    assert result["direction"] == "none"
    assert "未足以判斷升跌" in trend.summarise(result)


def test_an_empty_bucket_says_so(conn):
    result = trend.bucket_trend(conn, "泓都", "sale", TODAY)
    assert result["basis"] == "no_data"
    assert "未有可計算呎價的成交" in trend.summarise(result)


def test_sales_and_rentals_never_mix(conn):
    """A 呎租 of 57 and a 呎價 of 24,000 in one median would be nonsense."""
    for offset in (5, 10, 15):
        add(conn, TODAY - timedelta(days=offset), 24_000, deal_type="sale")
        add(conn, TODAY - timedelta(days=offset), 57, deal_type="rental")
    for offset in (100, 110, 120):
        add(conn, TODAY - timedelta(days=offset), 24_000, deal_type="sale")
        add(conn, TODAY - timedelta(days=offset), 50, deal_type="rental")

    sale = trend.bucket_trend(conn, "泓都", "sale", TODAY)
    rental = trend.bucket_trend(conn, "泓都", "rental", TODAY)
    assert sale["recent"]["median_unit_price"] == 24_000
    assert rental["recent"]["median_unit_price"] == 57
    assert rental["pct"] == 14.0


def test_transactions_without_an_area_are_excluded(conn):
    """No 面積 means no 呎價, and nothing to put in a median."""
    for offset in (5, 10, 15, 100, 110, 120):
        add(conn, TODAY - timedelta(days=offset), 20_000)
    db.insert_transaction(
        conn,
        make_transaction(tx_id="no-area", saleable_area=None, saleable_unit_price=None),
        MatchResult(matched=True, reason="test", area_missing=True),
        NOW,
    )
    result = trend.bucket_trend(conn, "泓都", "sale", TODAY)
    assert result["archive"]["transactions"] == 6
    assert result["recent"]["samples"] == 3


def test_a_flat_move_is_flat_not_a_direction(conn):
    for offset in (5, 10, 15):
        add(conn, TODAY - timedelta(days=offset), 20_000)
    for offset in (100, 110, 120):
        add(conn, TODAY - timedelta(days=offset), 20_000)
    assert trend.bucket_trend(conn, "泓都", "sale", TODAY)["direction"] == "flat"


def test_the_truncated_first_month_is_dropped_from_the_series(conn):
    """The archive starts on whatever day the first check ran.

    A median over the last few days of that month is a sample of the month, not
    a reading of it, and it would anchor the left end of every chart for ever.
    """
    add(conn, date(2026, 5, 26), 18_000)       # archive begins mid-May
    add(conn, date(2026, 5, 30), 18_500)
    for day in (1, 12, 25):
        add(conn, date(2026, 6, day), 21_000)
        add(conn, date(2026, 7, day), 22_000)

    series = trend.monthly_series(conn, "泓都", "sale", TODAY)
    assert [point["month"] for point in series["points"]] == ["2026-06", "2026-07"]
    assert series["partial_first_month"]["month"] == "2026-05"
    assert series["partial_first_month"]["archive_begins"] == "2026-05-26"


def test_a_month_that_starts_on_the_first_is_kept(conn):
    for day in (1, 15):
        add(conn, date(2026, 6, day), 21_000)
    series = trend.monthly_series(conn, "泓都", "sale", TODAY)
    assert [point["month"] for point in series["points"]] == ["2026-06"]
    assert series["partial_first_month"] is None


def test_months_without_a_transaction_are_omitted_not_zeroed(conn):
    add(conn, date(2026, 6, 1), 21_000)
    add(conn, date(2026, 8, 1), 23_000)
    series = trend.monthly_series(conn, "泓都", "sale", TODAY)
    assert [point["month"] for point in series["points"]] == ["2026-06", "2026-08"]
