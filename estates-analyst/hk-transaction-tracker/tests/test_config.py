"""The config file: what it accepts, and what it refuses with a reason."""

from __future__ import annotations

import pytest

from hk_transaction_tracker.config import load_config, strip_comments
from hk_transaction_tracker.errors import ConfigError
from hk_transaction_tracker.settings import DEFAULT_CONFIG_FILE

from .conftest import write_config

URL = "https://hk.centanet.com/findproperty/list/transaction/x_2-Y"


def test_the_shipped_config_is_valid():
    """The file that ships with the bundle must load, or a fresh install is broken."""
    config = load_config(DEFAULT_CONFIG_FILE)
    assert config.estates
    assert all(entry.url.startswith("https://") for entry in config.estates)


def test_comments_survive_the_parser():
    source = '// a note\n{\n  "url": "https://example.com" // not a comment\n}\n'
    assert '"url": "https://example.com"' in strip_comments(source)
    assert "a note" not in strip_comments(source)


def test_a_url_that_is_not_a_transaction_list_is_refused(tmp_path):
    """Anything else parses to a page with no transactionList, on every run for ever."""
    path = write_config(tmp_path, [
        {"name": "x", "url": "https://hk.centanet.com/findproperty/list/buy/x_2-Y"}
    ])
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "list/transaction" in caught.value.message


def test_duplicate_names_are_refused(tmp_path):
    """The name is the archive's key; two entries sharing it would merge histories."""
    path = write_config(tmp_path, [
        {"name": "x", "url": URL}, {"name": "x", "url": URL},
    ])
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "duplicate" in caught.value.message


def test_an_oversized_fetch_is_refused(tmp_path):
    """101 returns an empty list rather than an error, so it must never be set."""
    path = write_config(tmp_path, [{"name": "x", "url": URL}], fetch_size=250)
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "empty list" in caught.value.message


def test_an_inverted_size_range_is_refused(tmp_path):
    path = write_config(tmp_path, [
        {"name": "x", "url": URL, "size_ranges": [[900, 400]]}
    ])
    with pytest.raises(ConfigError):
        load_config(path)


def test_a_bad_bedroom_count_is_refused(tmp_path):
    path = write_config(tmp_path, [{"name": "x", "url": URL, "bedrooms": ["two"]}])
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "bedrooms" in caught.value.message


def test_an_unknown_track_side_is_refused(tmp_path):
    path = write_config(tmp_path, [{"name": "x", "url": URL, "track": ["lease"]}])
    with pytest.raises(ConfigError):
        load_config(path)


def test_an_empty_estate_list_is_refused(tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_config(write_config(tmp_path, []))
    assert "at least one estate" in caught.value.message


def test_selecting_an_unknown_estate_names_the_known_ones(tmp_path):
    config = load_config(write_config(tmp_path, [{"name": "泓都", "url": URL}]))
    with pytest.raises(ConfigError) as caught:
        config.select(["nope"])
    assert "泓都" in caught.value.message


def test_disabled_estates_are_skipped_unless_named(tmp_path):
    config = load_config(write_config(tmp_path, [
        {"name": "on", "url": URL},
        {"name": "off", "url": URL, "enabled": False},
    ]))
    assert [entry.name for entry in config.select()] == ["on"]
    assert [entry.name for entry in config.select(["off"])] == ["off"]


def test_both_size_range_spellings_work(tmp_path):
    config = load_config(write_config(tmp_path, [{
        "name": "x", "url": URL,
        "size_ranges": [[500, 700], {"low": 900, "high": None}],
    }]))
    assert [band.label for band in config.estates[0].size_ranges] == ["500-700呎", "900呎以上"]
