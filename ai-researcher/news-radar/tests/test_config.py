"""Config validation.

Every rule here exists to make a typo loud. A miscategorised source that quietly
invents a one-line section at the bottom of the digest is exactly the kind of
wrongness nobody notices for a month.
"""

from __future__ import annotations

import json

import pytest

from news_radar.config import load_config
from news_radar.config.sources import strip_comments
from news_radar.errors import ConfigError

from .conftest import ALPHA, write_config


def test_a_source_must_declare_a_category(tmp_path):
    source = {key: value for key, value in ALPHA.items() if key != "category"}
    with pytest.raises(ConfigError, match="category"):
        load_config(write_config(tmp_path, sources=[source]))


def test_a_category_must_be_declared(tmp_path):
    """The typo guard: 'ai ' or 'Ai' must fail, not create a new section."""
    with pytest.raises(ConfigError, match="not declared"):
        load_config(write_config(tmp_path, sources=[{**ALPHA, "category": "Ai"}]))


def test_at_least_one_category_is_required(tmp_path):
    with pytest.raises(ConfigError, match="category"):
        load_config(write_config(tmp_path, categories=[], sources=[]))


def test_duplicate_names_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="duplicate source"):
        load_config(write_config(tmp_path, sources=[ALPHA, ALPHA]))


@pytest.mark.parametrize("override, message", [
    ({"url": "ftp://example.com/feed"}, "http"),
    ({"kind": "telepathy"}, "kind"),
    ({"render": "interpretive-dance"}, "render"),
    ({"max_items": 0}, "max_items"),
    ({"min_interval_minutes": -5}, "min_interval_minutes"),
    ({"kind": "html"}, "list_selector"),
])
def test_a_bad_field_is_named_in_the_error(tmp_path, override, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, sources=[{**ALPHA, **override}]))


def test_cluster_threshold_must_be_a_fraction(tmp_path):
    with pytest.raises(ConfigError, match="cluster_threshold"):
        load_config(write_config(tmp_path, cluster_threshold=1.5, sources=[ALPHA]))


def test_categories_may_be_bare_strings(tmp_path):
    """Convenience for a config that does not need display labels."""
    config = load_config(write_config(tmp_path, categories=["ai"], sources=[ALPHA]))
    assert config.category("ai").display() == "ai"


def test_selecting_by_unknown_category_is_an_error(tmp_path):
    config = load_config(write_config(tmp_path, sources=[ALPHA]))
    with pytest.raises(ConfigError, match="unknown categor"):
        config.select(categories=["nope"])


def test_disabled_sources_are_excluded_by_default(tmp_path):
    config = load_config(write_config(tmp_path, sources=[{**ALPHA, "enabled": False}]))
    assert config.select() == []
    assert len(config.select(include_disabled=True)) == 1


def test_comments_are_stripped_but_urls_survive(tmp_path):
    raw = """
// a leading comment
{
  "categories": ["ai"],
  "sources": [
//  { "name": "off", "category": "ai", "url": "https://off.example/feed" },
    { "name": "on", "category": "ai", "url": "https://on.example/feed" }
  ]
}
"""
    path = tmp_path / "sources.json"
    path.write_text(raw, encoding="utf-8")

    config = load_config(path)

    assert [source.name for source in config.sources] == ["on"]
    assert config.sources[0].url == "https://on.example/feed"


def test_stripping_keeps_line_numbers(tmp_path):
    """Blanked, not deleted, so a JSON error points at the line the editor shows."""
    assert strip_comments("a\n// b\nc").splitlines() == ["a", "", "c"]


def test_category_of_an_unknown_source_is_uncategorised(tmp_path):
    config = load_config(write_config(tmp_path, sources=[ALPHA]))
    assert config.category_of("alpha") == "ai"
    assert config.category_of("deleted-last-week") == "uncategorised"


def test_the_shipped_config_is_valid():
    """The one that ships must load, or the bundle is broken out of the box."""
    from news_radar.config.sources import CONFIG_FILE

    config = load_config(CONFIG_FILE)
    known = {category.name for category in config.categories}
    assert config.sources
    assert all(source.category in known for source in config.sources)


def test_a_config_file_that_is_not_json_says_so(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
