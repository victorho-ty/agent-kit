"""Loading, validating and editing the watchlist.

The config states only what is unusual about a ticker; everything else falls
back to `defaults`. That makes the fallback path the *common* path rather than a
rarity, which is why so much of this file is about what happens when a key is
simply absent.
"""

from __future__ import annotations

import json

import pytest

from stock_desk.config import watchlist as W
from stock_desk.errors import ConfigError
from stock_desk.models import SectorConfig, TickerConfig

MINIMAL = {
    "tickers": [{"ticker": "NVDA", "company_name": "NVIDIA Corporation"}],
}


def write(tmp_path, payload):
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestDefaults:
    def test_a_bare_ticker_inherits_everything(self, tmp_path):
        entry = W.load(write(tmp_path, MINIMAL)).find("NVDA")
        assert entry.analysis_types == ("technical", "competitor")
        assert entry.technical_horizon_days == 30
        assert entry.enabled is True
        assert entry.competitors == ()

    def test_defaults_block_absent(self, tmp_path):
        config = W.load(write(tmp_path, MINIMAL))
        assert config.defaults.technical_horizon_days == 30
        assert config.defaults.min_avg_dollar_volume == 5_000_000.0

    def test_a_config_with_no_report_block_gets_real_defaults(self, tmp_path):
        report = W.load(write(tmp_path, MINIMAL)).report
        assert report.minutes_before_open == 30
        assert report.cluster_threshold == 0.6
        assert report.event_horizon_days == 10
        assert report.max_stories == 12

    def test_no_default_is_a_descriptor_repr(self, tmp_path):
        """The bug that a sparse config makes reachable.

        These dataclasses are `slots=True`, so `ReportConfig.frequency` is the
        slot descriptor rather than the default. Used as a fallback it yields
        the literal text "<member 'frequency' of 'ReportConfig' objects>" for a
        string field, and a TypeError inside int() for a numeric one. Neither
        failure says anything about defaults, and neither appears until somebody
        omits the key.
        """
        config = W.load(write(tmp_path, MINIMAL))
        assert config.report.frequency == "daily"
        for value in (
            config.report.frequency,
            str(config.report.minutes_before_open),
            str(config.report.max_stories),
        ):
            assert "member" not in value
            assert "object" not in value

    def test_explicit_values_win_over_defaults(self, tmp_path):
        payload = {
            "defaults": {"technical_horizon_days": 30},
            "tickers": [{"ticker": "NVDA", "technical_horizon_days": 90}],
        }
        assert W.load(write(tmp_path, payload)).find("NVDA").technical_horizon_days == 90

    def test_every_report_field_survives_being_omitted_one_at_a_time(self, tmp_path):
        full = {
            "minutes_before_open": 45,
            "cluster_threshold": 0.7,
            "event_horizon_days": 14,
            "max_stories": 20,
            "frequency": "daily",
        }
        for missing in full:
            partial = {k: v for k, v in full.items() if k != missing}
            config = W.load(write(tmp_path, {**MINIMAL, "report": partial}))
            assert isinstance(config.report.minutes_before_open, int)
            assert isinstance(config.report.frequency, str)


class TestValidation:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError):
            W.load(tmp_path / "nope.json")

    def test_malformed_json_names_the_line(self, tmp_path):
        path = tmp_path / "watchlist.json"
        path.write_text('{"tickers": [\n  {"ticker": "NVDA",}\n]}', encoding="utf-8")
        with pytest.raises(ConfigError) as caught:
            W.load(path)
        assert "line" in str(caught.value).lower()

    def test_tickers_are_upper_cased(self, tmp_path):
        payload = {"tickers": [{"ticker": "nvda"}]}
        assert W.load(write(tmp_path, payload)).tickers[0].ticker == "NVDA"

    def test_a_ticker_with_no_symbol_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError):
            W.load(write(tmp_path, {"tickers": [{"company_name": "Nameless"}]}))

    def test_a_duplicate_ticker_is_rejected(self, tmp_path):
        payload = {"tickers": [{"ticker": "NVDA"}, {"ticker": "NVDA"}]}
        with pytest.raises(ConfigError):
            W.load(write(tmp_path, payload))

    def test_an_unknown_analysis_type_is_rejected(self, tmp_path):
        payload = {"tickers": [{"ticker": "NVDA", "analysis_types": ["astrology"]}]}
        with pytest.raises(ConfigError):
            W.load(write(tmp_path, payload))


class TestSectors:
    def test_members_are_upper_cased(self, tmp_path):
        payload = {**MINIMAL, "sectors": [{"name": "AI", "members": ["nvda", "amd"]}]}
        assert W.load(write(tmp_path, payload)).sectors[0].members == ("NVDA", "AMD")

    def test_a_one_member_sector_is_rejected(self, tmp_path):
        # One member is a ticker, not a sector. Reporting it as "in line with
        # its group" would be a statement about nothing.
        payload = {**MINIMAL, "sectors": [{"name": "AI", "members": ["NVDA"]}]}
        with pytest.raises(ConfigError):
            W.load(write(tmp_path, payload))

    def test_a_nameless_sector_is_rejected(self, tmp_path):
        payload = {**MINIMAL, "sectors": [{"members": ["NVDA", "AMD"]}]}
        with pytest.raises(ConfigError):
            W.load(write(tmp_path, payload))

    def test_no_sectors_block_is_fine(self, tmp_path):
        assert W.load(write(tmp_path, MINIMAL)).sectors == ()

    def test_a_ticker_can_belong_to_two_sectors(self, tmp_path):
        payload = {
            **MINIMAL,
            "sectors": [
                {"name": "AI", "members": ["NVDA", "AMD"]},
                {"name": "Semis", "members": ["NVDA", "AVGO"]},
            ],
        }
        assert [s.name for s in W.load(write(tmp_path, payload)).sector_of("NVDA")] == ["AI", "Semis"]


class TestMacro:
    def test_absent_macro_block_is_enabled_with_no_overrides(self, tmp_path):
        macro = W.load(write(tmp_path, MINIMAL)).macro
        assert macro.enabled is True and macro.moves == {}

    def test_overrides_are_read_as_floats(self, tmp_path):
        payload = {**MINIMAL, "macro": {"enabled": True, "moves": {"ust_10y": "0.25"}}}
        assert W.load(write(tmp_path, payload)).macro.moves["ust_10y"] == pytest.approx(0.25)


class TestEditing:
    def test_add_then_reload(self, tmp_path):
        config = W.load(write(tmp_path, MINIMAL))
        updated = W.add_ticker(config, TickerConfig(ticker="AMD", company_name="AMD"))
        W.save(updated)
        assert W.load(config.path).find("AMD") is not None

    def test_adding_a_duplicate_is_rejected(self, tmp_path):
        config = W.load(write(tmp_path, MINIMAL))
        with pytest.raises(ConfigError):
            W.add_ticker(config, TickerConfig(ticker="NVDA"))

    def test_remove(self, tmp_path):
        config = W.load(write(tmp_path, MINIMAL))
        W.save(W.remove_ticker(config, "NVDA"))
        assert W.load(config.path).tickers == ()

    def test_removing_something_absent_is_an_error(self, tmp_path):
        config = W.load(write(tmp_path, MINIMAL))
        with pytest.raises(ConfigError):
            W.remove_ticker(config, "TSLA")

    def test_update_replaces_a_list_rather_than_appending(self, tmp_path):
        payload = {"tickers": [{"ticker": "NVDA", "competitors": ["AMD"]}]}
        config = W.load(write(tmp_path, payload))
        updated = W.update_ticker(config, "NVDA", competitors=["AVGO", "MRVL"])
        assert updated.find("NVDA").competitors == ("AVGO", "MRVL")

    def test_an_unknown_field_is_an_error_not_a_silent_no_op(self, tmp_path):
        config = W.load(write(tmp_path, MINIMAL))
        with pytest.raises(ConfigError):
            W.update_ticker(config, "NVDA", favourite_colour="blue")

    def test_a_round_trip_preserves_every_field(self, tmp_path):
        payload = {
            "timezone": "America/New_York",
            "report": {"minutes_before_open": 45, "max_stories": 5},
            "defaults": {"technical_horizon_days": 60},
            "tickers": [
                {
                    "ticker": "NVDA",
                    "company_name": "NVIDIA Corporation",
                    "competitors": ["AMD"],
                    "technical_horizon_days": 90,
                    "enabled": False,
                    "min_avg_dollar_volume": 1000000,
                }
            ],
            "sectors": [{"name": "AI", "members": ["NVDA", "AMD"]}],
            "macro": {"enabled": False, "moves": {"ust_10y": 0.25}},
        }
        first = W.load(write(tmp_path, payload))
        W.save(first)
        second = W.load(first.path)
        assert second.timezone == first.timezone
        assert second.report == first.report
        assert second.defaults == first.defaults
        assert second.tickers == first.tickers
        assert second.sectors == first.sectors
        assert second.macro == first.macro


class TestSparseSerialisation:
    def test_a_ticker_matching_the_defaults_writes_only_its_identity(self, tmp_path):
        config = W.load(write(tmp_path, MINIMAL))
        W.save(config)
        written = json.loads(config.path.read_text(encoding="utf-8"))
        assert written["tickers"][0] == {
            "ticker": "NVDA",
            "company_name": "NVIDIA Corporation",
        }

    def test_a_ticker_differing_from_the_defaults_says_so(self, tmp_path):
        payload = {"tickers": [{"ticker": "NVDA", "technical_horizon_days": 90}]}
        config = W.load(write(tmp_path, payload))
        W.save(config)
        written = json.loads(config.path.read_text(encoding="utf-8"))
        assert written["tickers"][0]["technical_horizon_days"] == 90

    def test_a_disabled_ticker_says_so(self, tmp_path):
        payload = {"tickers": [{"ticker": "NVDA", "enabled": False}]}
        config = W.load(write(tmp_path, payload))
        W.save(config)
        written = json.loads(config.path.read_text(encoding="utf-8"))
        assert written["tickers"][0]["enabled"] is False

    def test_dead_fields_are_gone_from_the_schema(self, tmp_path):
        """sector_keywords, notes, proxy and news_lookback_days drove no logic."""
        config = W.load(write(tmp_path, MINIMAL))
        W.save(config)
        written = json.loads(config.path.read_text(encoding="utf-8"))
        assert "news_lookback_days" not in written["report"]
        assert "sector_keywords" not in written["tickers"][0]
        assert "notes" not in written["tickers"][0]

    def test_an_unknown_key_in_the_file_is_simply_ignored(self, tmp_path):
        # Forward compatibility: a key this build does not know must not stop it
        # loading, or a config written by a newer build bricks the desk.
        payload = {"tickers": [{"ticker": "NVDA", "sector_keywords": ["AI"], "notes": "x"}]}
        assert W.load(write(tmp_path, payload)).find("NVDA") is not None
