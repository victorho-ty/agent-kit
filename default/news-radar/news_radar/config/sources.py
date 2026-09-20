"""What is scanned, and which section of the digest it belongs to.

Adapted from education-radar/education_radar/config/sites.py. Two differences
worth knowing:

**There is no scan window.** education-radar had one because quiet hours are a
policy about when to bother a person, and a cron expression states that badly.
Here the scan never talks to anyone -- only the digest does -- so the scan can
run continuously and the window, its arithmetic and ``--force`` are all gone.

**There is no global interval either.** The cron entry *is* the cadence;
restating it here would create two sources of truth that drift silently. What
cron cannot express is per-source politeness, so that is the one knob provided:
``min_interval_minutes`` is a floor on how often a single source may be
fetched, not a schedule.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from ..errors import ConfigError
from ..models import RENDER_MODES, SOURCE_KINDS

CONFIG_FILE = Path(__file__).parent / "sources.json"

DEFAULT_DELAY_SECONDS = 2.0
DEFAULT_MAX_ITEMS = 40
DEFAULT_DETAIL_BUDGET = 10
# Two headlines whose significant words overlap by at least this much are taken
# to be the same story. Raising it splits rewordings apart; lowering it merges
# unrelated stories that share a subject.
#
# 0.7 rather than 0.6 because of a false merge found against live feeds:
# "Introducing Muse Glimmer" and "Introducing Muse Code and Muse Spark 1.2" are
# two different product posts sharing {introducing, muse}, which scores 2/3 =
# 0.667. That is the shape of the mistake this dial guards against -- a short
# headline is easily contained in a longer one -- and it is pinned in
# tests/test_cluster.py.
DEFAULT_CLUSTER_THRESHOLD = 0.7

UNCATEGORISED = "uncategorised"


@dataclasses.dataclass(frozen=True)
class Category:
    """One section of the digest.

    The order of these in the config is the order of sections in the message,
    which is the whole reason they are a list rather than a set of strings on
    the sources: most-important-first is the point, and alphabetical would bury
    it.
    """

    name: str
    label: str | None = None

    def __post_init__(self):
        if not self.name.strip():
            raise ConfigError("a category needs a name")

    def display(self) -> str:
        return self.label or self.name

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.display()}


@dataclasses.dataclass(frozen=True)
class Source:
    """One place to read, and which section its stories belong in."""

    name: str
    url: str
    category: str
    kind: str = "rss"
    render: str = "static"
    list_selector: str | None = None
    fields: dict[str, str] = dataclasses.field(default_factory=dict)
    item_pattern: str | None = None
    follow_detail: bool = False
    detail_selector: str | None = None
    min_interval_minutes: int = 0
    max_items: int = DEFAULT_MAX_ITEMS
    enabled: bool = True

    def __post_init__(self):
        if not self.name.strip():
            raise ConfigError("a source needs a name")
        if not self.url.startswith(("http://", "https://")):
            raise ConfigError(f"source {self.name!r}: url must be http(s), got {self.url!r}")
        if not str(self.category).strip():
            raise ConfigError(
                f"source {self.name!r}: category is required -- it decides which section of "
                "the digest this source's stories appear under"
            )
        if self.kind not in SOURCE_KINDS:
            raise ConfigError(f"source {self.name!r}: kind must be one of {SOURCE_KINDS}, got {self.kind!r}")
        if self.render not in RENDER_MODES:
            raise ConfigError(f"source {self.name!r}: render must be one of {RENDER_MODES}, got {self.render!r}")
        if self.kind == "html":
            if not self.list_selector:
                raise ConfigError(f"source {self.name!r}: kind 'html' needs a list_selector")
            if "title" not in self.fields:
                raise ConfigError(f"source {self.name!r}: fields must include 'title'")
        if self.kind == "regex" and not self.item_pattern:
            raise ConfigError(f"source {self.name!r}: kind 'regex' needs an item_pattern")
        if self.max_items < 1:
            raise ConfigError(f"source {self.name!r}: max_items must be >= 1")
        if self.min_interval_minutes < 0:
            raise ConfigError(f"source {self.name!r}: min_interval_minutes must be >= 0")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "category": self.category,
            "kind": self.kind,
            "render": self.render,
            "follow_detail": self.follow_detail,
            "min_interval_minutes": self.min_interval_minutes,
            "max_items": self.max_items,
            "enabled": self.enabled,
        }


@dataclasses.dataclass(frozen=True)
class RadarConfig:
    """The whole config file."""

    timezone_name: str
    categories: tuple[Category, ...]
    sources: tuple[Source, ...]
    exclude_keywords: tuple[str, ...]
    request_delay_seconds: float
    detail_budget: int
    cluster_threshold: float
    path: Path

    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def select(
        self,
        names: list[str] | None = None,
        categories: list[str] | None = None,
        include_disabled: bool = False,
    ) -> list[Source]:
        chosen = list(self.sources)
        if names:
            by_name = {source.name: source for source in chosen}
            unknown = [name for name in names if name not in by_name]
            if unknown:
                raise ConfigError(f"unknown source(s) {unknown}; {self.path} defines {sorted(by_name)}")
            chosen = [by_name[name] for name in names]
        if categories:
            known = {category.name for category in self.categories}
            unknown = [name for name in categories if name not in known]
            if unknown:
                raise ConfigError(f"unknown categor(y/ies) {unknown}; {self.path} defines {sorted(known)}")
            chosen = [source for source in chosen if source.category in categories]
        if not include_disabled:
            chosen = [source for source in chosen if source.enabled]
        return chosen

    def category(self, name: str) -> Category | None:
        return next((category for category in self.categories if category.name == name), None)

    def category_of(self, source_name: str) -> str:
        """Which section a source's items belong in, as the config reads *now*.

        Deliberately looked up live rather than stored on the item: moving a
        source from ``tech`` to ``ai`` should file everything not yet sent under
        AI, which is what a person expects after editing the config. A source
        that has since been deleted returns ``uncategorised`` so its pending
        items surface in the digest instead of vanishing.
        """
        source = next((item for item in self.sources if item.name == source_name), None)
        return source.category if source else UNCATEGORISED


# --------------------------------------------------------------------------- loading


def strip_comments(source: str) -> str:
    """Drop whole-line ``//`` comments so the shipped config can carry examples.

    Only lines whose first non-space characters are ``//`` are removed, which is
    what keeps a ``https://`` inside a value safe. Lines are blanked rather than
    deleted so a JSON error still reports the line number the operator sees in
    their editor.
    """
    return "\n".join("" if line.lstrip().startswith("//") else line for line in source.splitlines())


def _load_category(entry: object) -> Category:
    if isinstance(entry, str):
        return Category(name=entry)
    if not isinstance(entry, dict):
        raise ConfigError(f"every category must be a string or a JSON object, got {entry!r}")
    try:
        return Category(**entry)
    except TypeError as exc:
        raise ConfigError(f"category {entry.get('name', '<unnamed>')!r}: {exc}") from exc


def _load_source(entry: object) -> Source:
    if not isinstance(entry, dict):
        raise ConfigError(f"every source must be a JSON object, got {entry!r}")
    entry = dict(entry)
    if "category" not in entry:
        raise ConfigError(
            f"source {entry.get('name', '<unnamed>')!r}: missing 'category' -- every source must say "
            "which section of the digest it belongs to"
        )
    if "fields" in entry and not isinstance(entry["fields"], dict):
        raise ConfigError(f"source {entry.get('name')!r}: fields must be a JSON object of name -> selector")
    try:
        return Source(**entry)
    except TypeError as exc:
        raise ConfigError(f"source {entry.get('name', '<unnamed>')!r}: {exc}") from exc


def load_config(path: Path | str | None = None) -> RadarConfig:
    """Read and validate ``sources.json``."""
    if path is None:
        from .. import settings

        path = settings.config_path()
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.loads(strip_comments(handle.read()))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: the config must be a JSON object")

    categories = tuple(_load_category(entry) for entry in raw.get("categories", []))
    if not categories:
        raise ConfigError(f"{path}: at least one category is required -- the digest is built from them")
    duplicate = _first_duplicate(category.name for category in categories)
    if duplicate:
        raise ConfigError(f"{path}: duplicate category name {duplicate!r}")

    sources = tuple(_load_source(entry) for entry in raw.get("sources", []))
    duplicate = _first_duplicate(source.name for source in sources)
    if duplicate:
        raise ConfigError(f"{path}: duplicate source name {duplicate!r}")

    # A category typo must not quietly invent a one-source section that nobody
    # ordered and nobody notices at the bottom of the digest.
    known = {category.name for category in categories}
    for source in sources:
        if source.category not in known:
            raise ConfigError(
                f"source {source.name!r}: category {source.category!r} is not declared in 'categories' "
                f"({sorted(known)})"
            )

    timezone_name = raw.get("timezone")
    if timezone_name is None:
        from .. import settings

        timezone_name = str(settings.timezone())
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:  # ZoneInfoNotFoundError and friends
        raise ConfigError(f"{path}: unknown timezone {timezone_name!r}: {exc}") from exc

    delay = float(raw.get("request_delay_seconds", DEFAULT_DELAY_SECONDS))
    if delay < 0:
        raise ConfigError(f"{path}: request_delay_seconds must be >= 0")

    threshold = float(raw.get("cluster_threshold", DEFAULT_CLUSTER_THRESHOLD))
    if not 0.0 < threshold <= 1.0:
        raise ConfigError(f"{path}: cluster_threshold must be between 0 and 1, got {threshold}")

    return RadarConfig(
        timezone_name=timezone_name,
        categories=categories,
        sources=sources,
        exclude_keywords=tuple(raw.get("exclude", ())),
        request_delay_seconds=delay,
        detail_budget=int(raw.get("detail_budget", DEFAULT_DETAIL_BUDGET)),
        cluster_threshold=threshold,
        path=path,
    )


def _first_duplicate(names) -> str | None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            return name
        seen.add(name)
    return None
