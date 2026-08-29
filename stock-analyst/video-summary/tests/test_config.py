"""What the config refuses, and why each refusal is worth having."""

from __future__ import annotations

import json

import pytest

from video_summary.config import load_config
from video_summary.errors import ConfigError

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCnexoc6tvesvcCEzZhmI-Ag"


def write(tmp_path, payload) -> object:
    path = tmp_path / "feeds.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_loads_a_minimal_config(tmp_path):
    config = load_config(write(tmp_path, {"feeds": [{"name": "a", "url": FEED_URL}]}))
    assert [feed.name for feed in config.feeds] == ["a"]
    assert config.feeds[0].channel_id == "UCnexoc6tvesvcCEzZhmI-Ag"
    assert config.summary_char_cap == 800
    assert config.max_per_check == 5


def test_a_channel_page_url_is_not_a_feed_url(tmp_path):
    """The commonest mistake, and it would otherwise parse as HTML and yield nothing."""
    path = write(tmp_path, {"feeds": [{"name": "a", "url": "https://www.youtube.com/@somebody"}]})
    with pytest.raises(ConfigError, match="must be a YouTube feed"):
        load_config(path)


def test_a_handle_is_not_a_channel_id(tmp_path):
    path = write(
        tmp_path,
        {"feeds": [{"name": "a", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=@somebody"}]},
    )
    with pytest.raises(ConfigError, match="does not look like a channel id"):
        load_config(path)


def test_duplicate_urls_are_rejected(tmp_path):
    """Two names for one channel would send every video twice."""
    path = write(tmp_path, {"feeds": [{"name": "a", "url": FEED_URL}, {"name": "b", "url": FEED_URL}]})
    with pytest.raises(ConfigError, match="share the url"):
        load_config(path)


def test_summary_cap_cannot_exceed_telegrams_own_limit(tmp_path):
    path = write(tmp_path, {"summary_char_cap": 9000, "feeds": [{"name": "a", "url": FEED_URL}]})
    with pytest.raises(ConfigError, match="summary_char_cap"):
        load_config(path)


def test_comments_are_stripped_but_urls_survive(tmp_path):
    path = tmp_path / "feeds.json"
    path.write_text(
        "// a note\n"
        '{"feeds": [{"name": "a", "url": "' + FEED_URL + '"}]}\n',
        encoding="utf-8",
    )
    assert load_config(path).feeds[0].url == FEED_URL


def test_shorts_are_excluded_unless_a_feed_says_otherwise(tmp_path):
    config = load_config(write(tmp_path, {"feeds": [
        {"name": "a", "url": FEED_URL},
        {"name": "b", "url": FEED_URL.replace("UCnexoc6", "UCbbbbbb"), "exclude_shorts": False},
    ]}))
    assert config.exclude_shorts is True
    assert [feed.exclude_shorts for feed in config.feeds] == [True, False]


def test_a_global_exclude_shorts_moves_the_default(tmp_path):
    """One key flips every feed that has not said otherwise."""
    config = load_config(write(tmp_path, {"exclude_shorts": False, "feeds": [
        {"name": "a", "url": FEED_URL},
        {"name": "b", "url": FEED_URL.replace("UCnexoc6", "UCbbbbbb"), "exclude_shorts": True},
    ]}))
    assert [feed.exclude_shorts for feed in config.feeds] == [False, True]


def test_unknown_feed_name_is_named_in_the_error(tmp_path):
    config = load_config(write(tmp_path, {"feeds": [{"name": "a", "url": FEED_URL}]}))
    with pytest.raises(ConfigError, match="unknown feed"):
        config.select(["b"])
