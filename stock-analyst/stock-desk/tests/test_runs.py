"""Run profiles and what they decide before any network is touched."""

from __future__ import annotations

import sqlite3

import pytest

from stock_desk import runs
from stock_desk.config.watchlist import Defaults, ReportConfig, WatchlistConfig
from stock_desk.db import SCHEMA
from stock_desk.errors import ConfigError
from stock_desk.models import SectorConfig, TickerConfig


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    yield connection
    connection.close()


def config(tickers, sectors=(), tmp_path=None):
    return WatchlistConfig(
        timezone="Asia/Hong_Kong",
        report=ReportConfig(),
        defaults=Defaults(),
        tickers=tuple(tickers),
        path=(tmp_path / "watchlist.json") if tmp_path else None,
        sectors=tuple(sectors),
    )


class TestProfiles:
    def test_both_scheduled_profiles_exist(self):
        assert sorted(runs.PROFILES) == ["morning-hkt", "pre-us-open"]

    def test_an_unknown_profile_names_the_known_ones(self):
        with pytest.raises(ConfigError) as caught:
            runs.profile("afternoon-tea")
        assert "morning-hkt" in caught.value.detail["known"]

    def test_the_daily_budget_across_both_runs_fits_the_free_tier(self):
        """25 calls a day, shared with whatever the operator asks by hand.

        Spending the lot on a schedule would leave nothing for a question at
        lunchtime, and the failure mode is silent -- the vendor answers a
        depleted quota with an HTTP 200.
        """
        spent = sum(p.av_news_calls + p.av_macro_calls for p in runs.PROFILES.values())
        assert spent <= 20

    def test_yahoo_is_never_budgeted(self):
        # It is free and uncapped, and it is what keeps the desk working when
        # the Alpha Vantage key is missing or its quota is gone.
        assert not any(hasattr(p, "yahoo_calls") for p in runs.PROFILES.values())


class TestSyncScope:
    def test_the_watchlist_is_in_scope(self, conn):
        cfg = config([TickerConfig(ticker="NVDA"), TickerConfig(ticker="MSFT")])
        assert set(runs.sync_scope(conn, cfg)) == {"NVDA", "MSFT"}

    def test_a_disabled_ticker_is_not_synced(self, conn):
        cfg = config([TickerConfig(ticker="NVDA"), TickerConfig(ticker="MSFT", enabled=False)])
        assert runs.sync_scope(conn, cfg) == ["NVDA"]

    def test_sector_members_are_synced_even_when_not_watched(self, conn):
        """The one that is easy to forget.

        AMD is a sector member and not a watchlist entry. Without its bars the
        sector reports on two of three members and says `missing`, which reads
        as a data outage rather than a config gap.
        """
        cfg = config(
            [TickerConfig(ticker="NVDA")],
            [SectorConfig(name="AI", members=("NVDA", "AMD", "AVGO"))],
        )
        assert set(runs.sync_scope(conn, cfg)) == {"NVDA", "AMD", "AVGO"}

    def test_competitors_are_not_synced(self, conn):
        # A competitor is a news relationship, not a priced one. Syncing every
        # peer would multiply the bar cache for readings nobody computes.
        cfg = config([TickerConfig(ticker="NVDA", competitors=("AMD", "AVGO"))])
        assert runs.sync_scope(conn, cfg) == ["NVDA"]

    def test_open_positions_are_synced_even_when_unwatched(self, conn):
        conn.execute(
            """INSERT INTO positions (ticker, trade_date, side, quantity, price, fee, note, created_at)
               VALUES ('TSLA', '2026-07-01', 'buy', 10, 100, 0, '', '2026-07-01T00:00:00')"""
        )
        cfg = config([TickerConfig(ticker="NVDA")])
        assert set(runs.sync_scope(conn, cfg)) == {"NVDA", "TSLA"}

    def test_scope_has_no_duplicates(self, conn):
        cfg = config(
            [TickerConfig(ticker="NVDA"), TickerConfig(ticker="AMD")],
            [
                SectorConfig(name="AI", members=("NVDA", "AMD")),
                SectorConfig(name="Semis", members=("NVDA", "AVGO")),
            ],
        )
        scope = runs.sync_scope(conn, cfg)
        assert len(scope) == len(set(scope))
        assert set(scope) == {"NVDA", "AMD", "AVGO"}
