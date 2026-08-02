"""Database + report tests: upsert idempotency, series integrity, WoW maths."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from plane_ticket_prices import db
from plane_ticket_prices.report import build_report_data
from tests.conftest import seed_series


def _cell(run_date: str, scope: str = "SCOPE-1", airline: str = "Cathay Pacific",
          dep_bucket: str = "09-12", ret_bucket: str = "15-18", price: float = 1000.0) -> dict:
    return {
        "run_date": run_date, "scope": scope,
        "origin": "HKG", "dest": "DXB",
        "depart_date": date(2026, 12, 18), "return_date": date(2026, 12, 22),
        "airline": airline, "dep_bucket": dep_bucket, "ret_bucket": ret_bucket,
        "out_stops": 0, "ret_stops": 0, "seat": "economy", "currency": "HKD",
        "min_price": price, "n_itineraries": 1,
    }


class TestUpsertIdempotency:
    def test_partial_rerun_does_not_touch_other_pairs(self, tmp_db):
        conn = db.connect()
        # Two date pairs recorded on the same run day.
        db.upsert_cell(conn, {**_cell("2026-12-01"), "airline": "A1"})
        db.upsert_cell(conn, {**_cell("2026-12-01"), "airline": "A2"})
        assert conn.execute("SELECT COUNT(*) FROM round_trip_prices").fetchone()[0] == 2

        # Re-run ONLY pair A1 with a new price.
        changed = db.upsert_cell(conn, {**_cell("2026-12-01"), "airline": "A1", "min_price": 900.0})
        rows = conn.execute("SELECT airline, min_price FROM round_trip_prices").fetchall()
        by_airline = {r["airline"]: r["min_price"] for r in rows}
        assert changed == 1
        assert by_airline == {"A1": 900.0, "A2": 1000.0}   # A2 untouched
        assert conn.execute("SELECT COUNT(*) FROM round_trip_prices").fetchone()[0] == 2

    def test_identical_rerun_changes_nothing(self, tmp_db):
        conn = db.connect()
        db.upsert_cell(conn, _cell("2026-12-01"))
        db.upsert_cell(conn, _cell("2026-12-01"))
        assert conn.execute("SELECT COUNT(*) FROM round_trip_prices").fetchone()[0] == 1
        assert conn.execute("SELECT min_price FROM round_trip_prices").fetchone()["min_price"] == 1000.0

    def test_unique_key_spans_full_grouping(self, tmp_db):
        conn = db.connect()
        db.upsert_cell(conn, _cell("2026-12-01", airline="CX", dep_bucket="09-12", ret_bucket="15-18"))
        db.upsert_cell(conn, _cell("2026-12-01", airline="CX", dep_bucket="09-12", ret_bucket="18-21"))
        db.upsert_cell(conn, _cell("2026-12-01", airline="CX", dep_bucket="12-15", ret_bucket="15-18"))
        assert conn.execute("SELECT COUNT(*) FROM round_trip_prices").fetchone()[0] == 3


class TestSeries:
    def test_run_dates_are_ordered_and_unique(self, tmp_db):
        conn = db.connect()
        seed_series(conn, "SCOPE-1", days=5, seed_start=date(2026, 11, 20))
        dates = db.run_dates(conn, "SCOPE-1")
        assert dates == [f"2026-11-{d:02d}" for d in range(20, 25)]
        # 5 days x 4 cells
        assert conn.execute("SELECT COUNT(*) FROM round_trip_prices").fetchone()[0] == 20

    def test_series_is_per_cell_continuous(self, tmp_db):
        conn = db.connect()
        seed_series(conn, "SCOPE-1", days=3, seed_start=date(2026, 11, 20))
        series = db.cell_series(conn, "SCOPE-1")
        # every cell has exactly 3 points, one per run day
        counts: dict = {}
        for row in series:
            key = (row["airline"], row["dep_bucket"], row["ret_bucket"])
            counts[key] = counts.get(key, 0) + 1
        assert set(counts.values()) == {3}

    def test_wow_movement_math(self, tmp_db):
        conn = db.connect()
        seed_series(conn, "SCOPE-1", days=8, seed_start=date(2026, 11, 20))
        movers = db.wo_w_movement(conn, "SCOPE-1", "2026-11-27")
        assert len(movers) == 4
        for m in movers:
            assert m["price_7d_ago"] is not None
            expected = m["price"] - m["price_7d_ago"]
            assert m["delta"] == pytest.approx(expected)
        # sorted biggest drop first
        deltas = [m["delta"] for m in movers if m["delta"] is not None]
        assert deltas == sorted(deltas)

    def test_wow_with_insufficient_history(self, tmp_db):
        conn = db.connect()
        seed_series(conn, "SCOPE-1", days=3, seed_start=date(2026, 11, 20))
        movers = db.wo_w_movement(conn, "SCOPE-1", "2026-11-22")
        assert all(m["price_7d_ago"] is None for m in movers)


class TestReportData:
    def test_rankings_sorted_cheapest_first(self, tmp_db):
        conn = db.connect()
        seed_series(conn, "SCOPE-1", days=8, seed_start=date(2026, 11, 20))
        data = build_report_data(conn, "SCOPE-1", "2026-11-27")
        prices = [r["min_price"] for r in data["rankings"]]
        assert prices == sorted(prices)
        assert data["run_date"] == "2026-11-27"
        assert len(data["dates"]) == 8
        # every ranking grouping has a series of 8 points
        assert all(len(points) == 8 for points in data["by_grouping"].values())
        # wow rows carry the delta direction
        assert any(r["delta"] is not None for r in data["wow"])

    def test_empty_db_report_data(self, tmp_db):
        conn = db.connect()
        data = build_report_data(conn, "SCOPE-1", "2026-11-27")
        assert data["rankings"] == []
        assert data["wow"] == []
        assert data["dates"] == []


class TestRunsTable:
    def test_run_lifecycle(self, tmp_db):
        conn = db.connect()
        run_id = db.start_run(conn, "SCOPE-1", "2026-11-27", pairs_planned=3)
        db.finish_run(conn, run_id, status="partial", pairs_succeeded=2, pairs_failed=1,
                      searches_used=7, rows_written=4, detail={"pair_failures": [{"pair": "x"}]})
        run = db.latest_run(conn, "SCOPE-1")
        assert run["status"] == "partial"
        assert run["pairs_planned"] == 3
        assert run["pairs_succeeded"] == 2
        assert run["detail"] is not None
