"""Rates: parsing the vendor's shape, and speaking only when something moved."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from stock_desk import macro
from stock_desk.db import SCHEMA
from stock_desk.models import MacroSettings

NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
SERIES = macro.DEFAULT_SERIES


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    yield connection
    connection.close()


def put(conn, series, as_of, value, fetched=NOW, notified=None):
    conn.execute(
        "INSERT INTO macro (series, as_of, value, fetched_at, notified_at) VALUES (?,?,?,?,?)",
        (series, as_of, value, fetched.isoformat(), notified.isoformat() if notified else None),
    )


def spec(key):
    return next(s for s in SERIES if s.key == key)


class TestParsing:
    def test_a_missing_observation_is_a_dot_not_a_null(self):
        assert macro._value(".") is None
        assert macro._value("") is None
        assert macro._value(None) is None
        assert macro._value("4.6500000000") == pytest.approx(4.65)

    def test_the_newest_usable_row_wins_regardless_of_order(self):
        payload = {
            "data": [
                {"date": "2026-08-17", "value": "4.72"},
                {"date": "2026-08-19", "value": "4.65"},
                {"date": "2026-08-18", "value": "4.71"},
            ]
        }
        assert macro._newest(payload) == ("2026-08-19", pytest.approx(4.65))

    def test_a_run_of_missing_observations_at_the_head_is_skipped(self):
        # Normal around a holiday.
        payload = {
            "data": [
                {"date": "2026-08-20", "value": "."},
                {"date": "2026-08-19", "value": "4.65"},
            ]
        }
        assert macro._newest(payload) == ("2026-08-19", pytest.approx(4.65))

    def test_an_empty_series_is_none(self):
        assert macro._newest({"data": []}) is None
        assert macro._newest({}) is None


class TestThresholds:
    def test_defaults_are_absolute_not_proportional(self):
        # Ten basis points on the 10-year is the same event at 2% or at 5%.
        assert spec("ust_10y").move == pytest.approx(0.10)

    def test_an_operator_override_is_applied(self):
        tuned = macro.series_for(MacroSettings(moves={"ust_10y": 0.25}))
        assert next(s for s in tuned if s.key == "ust_10y").move == pytest.approx(0.25)

    def test_an_override_leaves_the_others_alone(self):
        tuned = macro.series_for(MacroSettings(moves={"ust_10y": 0.25}))
        assert next(s for s in tuned if s.key == "ust_2y").move == spec("ust_2y").move

    def test_no_settings_is_the_default_set(self):
        assert macro.series_for(None) == macro.DEFAULT_SERIES


class TestReportedOnChange:
    def test_a_series_never_reported_is_not_pending(self, conn):
        """The first sight of a level is a starting point, not a move."""
        put(conn, "ust_10y", "2026-08-19", 4.65)
        assert macro.pending(conn, SERIES) == []

    def test_a_move_past_the_threshold_is_pending(self, conn):
        put(conn, "ust_10y", "2026-08-12", 4.50, notified=NOW - timedelta(days=7))
        put(conn, "ust_10y", "2026-08-19", 4.65)
        moved = macro.pending(conn, SERIES)
        assert [r.series for r in moved] == ["ust_10y"]
        assert moved[0].change == pytest.approx(0.15)

    def test_a_move_short_of_the_threshold_says_nothing(self, conn):
        put(conn, "ust_10y", "2026-08-12", 4.60, notified=NOW - timedelta(days=7))
        put(conn, "ust_10y", "2026-08-19", 4.65)
        assert macro.pending(conn, SERIES) == []

    def test_a_drift_accumulates_against_the_last_reported_level(self, conn):
        """Three four-basis-point days are a twelve-basis-point move.

        Comparing against yesterday rather than against the last reported level
        would report none of them, which is the whole reason the baseline is
        `notified_at` and not the previous row.
        """
        put(conn, "ust_10y", "2026-08-17", 4.50, notified=NOW - timedelta(days=4))
        for day, value in (("2026-08-18", 4.54), ("2026-08-19", 4.58), ("2026-08-20", 4.62)):
            put(conn, "ust_10y", day, value)
        moved = macro.pending(conn, SERIES)
        assert [r.series for r in moved] == ["ust_10y"]
        assert moved[0].value == pytest.approx(4.62)

    def test_stamping_stops_it_repeating(self, conn):
        put(conn, "ust_10y", "2026-08-12", 4.50, notified=NOW - timedelta(days=7))
        put(conn, "ust_10y", "2026-08-19", 4.65)
        moved = macro.pending(conn, SERIES)
        assert macro.mark_notified(conn, moved, NOW) == 1
        assert macro.pending(conn, SERIES) == []

    def test_seeding_reports_nothing_but_establishes_the_baseline(self, conn):
        put(conn, "ust_10y", "2026-08-19", 4.65)
        assert macro.seed(conn, SERIES, NOW) == 1
        assert macro.pending(conn, SERIES) == []
        put(conn, "ust_10y", "2026-08-20", 4.80)
        assert [r.series for r in macro.pending(conn, SERIES)] == ["ust_10y"]


class TestStaleness:
    def test_an_unseen_series_is_stale(self, conn):
        assert macro.is_stale(conn, "ust_10y", NOW)

    def test_a_daily_series_goes_stale_within_the_day(self, conn):
        put(conn, "ust_10y", "2026-08-19", 4.65, fetched=NOW - timedelta(hours=7))
        assert macro.is_stale(conn, "ust_10y", NOW)

    def test_a_monthly_series_does_not(self, conn):
        # Refreshing a monthly print hourly spends the news poller's budget to
        # learn nothing.
        put(conn, "cpi", "2026-07-01", 320.0, fetched=NOW - timedelta(hours=7))
        assert not macro.is_stale(conn, "cpi", NOW)

    def test_refresh_skips_everything_fresh(self, conn):
        for s in SERIES:
            put(conn, s.key, "2026-08-19", 1.0, fetched=NOW)
        result = macro.refresh(conn, SERIES, NOW, budget=10)
        assert result["status"] == "skipped"
        assert result["calls"] == 0
        assert set(result["fresh"]) == {s.key for s in SERIES}

    def test_a_budget_of_zero_spends_nothing(self, conn):
        result = macro.refresh(conn, SERIES, NOW, budget=0)
        assert result["calls"] == 0
        assert set(result["deferred"]) == {s.key for s in SERIES}

    def test_what_the_budget_could_not_afford_is_named(self, conn):
        # A macro section quietly covering four of six series looks identical
        # to one where the other two did not move.
        result = macro.refresh(conn, SERIES, NOW, budget=0)
        assert len(result["deferred"]) == len(SERIES)


class TestCurve:
    def test_the_spread_is_derived_from_one_session(self, conn):
        put(conn, "ust_2y", "2026-08-19", 4.20)
        put(conn, "ust_10y", "2026-08-19", 4.65)
        curve = macro.curve(conn)
        assert curve["spread_bp"] == pytest.approx(45.0)
        assert curve["inverted"] is False
        assert curve["basis"].startswith("derived")

    def test_an_inversion_is_flagged(self, conn):
        put(conn, "ust_2y", "2026-08-19", 4.80)
        put(conn, "ust_10y", "2026-08-19", 4.65)
        assert macro.curve(conn)["inverted"] is True

    def test_legs_from_different_days_are_not_a_spread(self, conn):
        # Subtracting Tuesday's 2-year from Friday's 10-year means nothing.
        put(conn, "ust_2y", "2026-08-18", 4.20)
        put(conn, "ust_10y", "2026-08-19", 4.65)
        assert macro.curve(conn) is None

    def test_a_missing_leg_is_not_a_spread(self, conn):
        put(conn, "ust_10y", "2026-08-19", 4.65)
        assert macro.curve(conn) is None


class TestRendering:
    def test_a_yield_is_quoted_in_basis_points(self, conn):
        put(conn, "ust_10y", "2026-08-12", 4.50, notified=NOW - timedelta(days=7))
        put(conn, "ust_10y", "2026-08-19", 4.65)
        rendered = macro.line(macro.pending(conn, SERIES)[0])
        assert "US 10-year 4.65%" in rendered
        assert "+15bp" in rendered
        assert rendered.endswith(".")

    def test_an_index_is_quoted_in_its_own_units(self, conn):
        put(conn, "cpi", "2026-06-01", 320.0, notified=NOW - timedelta(days=40))
        put(conn, "cpi", "2026-07-01", 321.4)
        rendered = macro.line(macro.pending(conn, SERIES)[0])
        assert "bp" not in rendered
        assert "+1.40" in rendered

    def test_snapshot_reports_levels_without_stamping_anything(self, conn):
        put(conn, "ust_2y", "2026-08-19", 4.20)
        put(conn, "ust_10y", "2026-08-19", 4.65)
        snap = macro.snapshot(conn, SERIES)
        assert snap["levels"]["ust_10y"]["value"] == pytest.approx(4.65)
        assert snap["curve"]["spread_bp"] == pytest.approx(45.0)
        # The on-demand read must not consume the next scheduled section.
        assert conn.execute("SELECT COUNT(*) AS n FROM macro WHERE notified_at IS NOT NULL").fetchone()["n"] == 0
