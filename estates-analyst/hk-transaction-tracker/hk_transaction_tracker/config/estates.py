"""What is tracked: the estates, and the units within them worth hearing about.

Everything in ``estates.json`` changes more often than the code does -- a new
block to watch, a shift from two bedrooms to three, a size band widened by fifty
feet. None of those is a code change.

Two rules decide what gets reported, and they are ANDed:

* ``bedrooms`` -- the 間隔, as Centanet's own filter counts it. ``0`` is 開放式
  and ``4`` means 4房或以上, so a configured ``4`` matches a five-bedroom flat.
* ``size_ranges`` -- bands of 面積(實) in square feet, inclusive at both ends.

An empty list means "no constraint on this dimension", so an entry with neither
reports every transaction in the estate. An entry with both reports only units
satisfying both -- ``[2, 3]`` with ``[[500, 650]]`` is two- and three-bedroom
flats of 500 to 650 saleable feet, and a 700-foot three-bedroom is stored for
the trend but never announced.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import settings
from ..errors import ConfigError
from ..models import DEAL_TYPES

CONFIG_FILE = Path(__file__).parent / "estates.json"

# A Centanet 成交 list URL. Anything else decodes to a page with no
# transactionList, which would surface as ERR_PARSE on every run rather than as
# the config mistake it is.
REQUIRED_URL_FRAGMENT = "/list/transaction/"

MAX_BEDROOMS = 9


@dataclasses.dataclass(frozen=True)
class SizeRange:
    """One band of 面積(實), in saleable square feet. Inclusive at both ends.

    Either end may be ``None`` for an open band: ``[null, 500]`` is 500呎以下.
    """

    low: float | None = None
    high: float | None = None

    def __post_init__(self):
        if self.low is None and self.high is None:
            raise ConfigError("a size range needs at least one end")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ConfigError(
                f"size range [{self.low}, {self.high}]: low must not exceed high"
            )
        for end in (self.low, self.high):
            if end is not None and end <= 0:
                raise ConfigError(f"size range [{self.low}, {self.high}]: feet must be positive")

    def contains(self, area: float) -> bool:
        if self.low is not None and area < self.low:
            return False
        if self.high is not None and area > self.high:
            return False
        return True

    @property
    def label(self) -> str:
        from .. import fmt

        return fmt.size_range_label(self.low, self.high)

    def to_dict(self) -> dict:
        return {"low": self.low, "high": self.high, "label": self.label}


@dataclasses.dataclass(frozen=True)
class EstateEntry:
    """One estate, phase or block to watch, and the units within it to report."""

    name: str
    url: str
    label: str | None = None
    bedrooms: tuple[int, ...] = ()
    size_ranges: tuple[SizeRange, ...] = ()
    track: tuple[str, ...] = DEAL_TYPES
    enabled: bool = True

    def __post_init__(self):
        if not self.name.strip():
            raise ConfigError("an estate needs a name")
        if not self.url.startswith(("http://", "https://")):
            raise ConfigError(f"estate {self.name!r}: url must be http(s), got {self.url!r}")
        if REQUIRED_URL_FRAGMENT not in self.url:
            raise ConfigError(
                f"estate {self.name!r}: url must be a Centanet 成交 list "
                f"(containing {REQUIRED_URL_FRAGMENT!r}), got {self.url!r}"
            )
        for count in self.bedrooms:
            if not isinstance(count, int) or not 0 <= count <= MAX_BEDROOMS:
                raise ConfigError(
                    f"estate {self.name!r}: bedrooms must be whole numbers 0-{MAX_BEDROOMS} "
                    f"(0 是開放式, 4 是4房或以上), got {count!r}"
                )
        unknown = [side for side in self.track if side not in DEAL_TYPES]
        if unknown:
            raise ConfigError(
                f"estate {self.name!r}: track must be drawn from {list(DEAL_TYPES)}, got {unknown}"
            )
        if not self.track:
            raise ConfigError(
                f"estate {self.name!r}: track must not be empty -- "
                "set enabled to false to pause an estate instead"
            )

    @property
    def display(self) -> str:
        return self.label or self.name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "display": self.display,
            "url": self.url,
            "bedrooms": list(self.bedrooms),
            "bedroom_labels": [bedroom_label(count) for count in self.bedrooms],
            "size_ranges": [band.to_dict() for band in self.size_ranges],
            "track": list(self.track),
            "enabled": self.enabled,
        }


@dataclasses.dataclass(frozen=True)
class TrackerConfig:
    """The whole config file."""

    timezone_name: str
    request_delay_seconds: float
    fetch_size: int
    trend_window_days: int
    trend_min_samples: int
    chart_months: int
    chart_min_points: int
    estates: tuple[EstateEntry, ...]
    path: Path

    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def select(self, names: list[str] | None = None, include_disabled: bool = False):
        chosen = list(self.estates)
        if names:
            by_name = {entry.name: entry for entry in chosen}
            unknown = [name for name in names if name not in by_name]
            if unknown:
                raise ConfigError(
                    f"unknown estate(s) {unknown}; {self.path} defines {sorted(by_name)}"
                )
            return [by_name[name] for name in names]
        if not include_disabled:
            chosen = [entry for entry in chosen if entry.enabled]
        return chosen

    def entry(self, name: str) -> EstateEntry | None:
        return next((entry for entry in self.estates if entry.name == name), None)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "timezone": self.timezone_name,
            "request_delay_seconds": self.request_delay_seconds,
            "fetch_size": self.fetch_size,
            "trend": {
                "window_days": self.trend_window_days,
                "min_samples": self.trend_min_samples,
                "chart_months": self.chart_months,
                "chart_min_points": self.chart_min_points,
            },
            "estates": [entry.to_dict() for entry in self.estates],
        }


def bedroom_label(count: int) -> str:
    from ..models import bedroom_label as label

    return label(count)


# --------------------------------------------------------------------------- loading


def strip_comments(source: str) -> str:
    """Drop whole-line ``//`` comments so the shipped config can carry examples.

    Only lines whose first non-space characters are ``//`` are removed, which is
    what keeps a ``https://`` inside a value safe. Lines are blanked rather than
    deleted so a JSON error still reports the line number the operator sees in
    their editor.
    """
    return "\n".join("" if line.lstrip().startswith("//") else line for line in source.splitlines())


def _load_size_range(entry: object, estate: str) -> SizeRange:
    if isinstance(entry, dict):
        return SizeRange(low=_optional_number(entry.get("low")), high=_optional_number(entry.get("high")))
    if isinstance(entry, list) and len(entry) == 2:
        return SizeRange(low=_optional_number(entry[0]), high=_optional_number(entry[1]))
    raise ConfigError(
        f"estate {estate!r}: each size range must be [low, high] or "
        f'{{"low": .., "high": ..}}, got {entry!r}'
    )


def _optional_number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"not a number: {value!r}") from None


def _load_estate(entry: object) -> EstateEntry:
    if not isinstance(entry, dict):
        raise ConfigError(f"every estate must be a JSON object, got {entry!r}")
    entry = dict(entry)
    name = str(entry.get("name", "<unnamed>"))
    bedrooms = entry.get("bedrooms") or []
    if not isinstance(bedrooms, list):
        raise ConfigError(f"estate {name!r}: bedrooms must be a list of whole numbers")
    entry["bedrooms"] = tuple(
        count if isinstance(count, int) and not isinstance(count, bool) else count
        for count in bedrooms
    )
    ranges = entry.get("size_ranges") or []
    if not isinstance(ranges, list):
        raise ConfigError(f"estate {name!r}: size_ranges must be a list")
    entry["size_ranges"] = tuple(_load_size_range(band, name) for band in ranges)
    if "track" in entry:
        track = entry["track"]
        if not isinstance(track, list):
            raise ConfigError(f"estate {name!r}: track must be a list, e.g. [\"sale\", \"rental\"]")
        entry["track"] = tuple(str(side).strip().lower() for side in track)
    try:
        return EstateEntry(**entry)
    except TypeError as exc:
        raise ConfigError(f"estate {name!r}: {exc}") from exc


def load_config(path: Path | str | None = None) -> TrackerConfig:
    """Read and validate ``estates.json``."""
    path = Path(path) if path is not None else settings.config_path()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.loads(strip_comments(handle.read()))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {path}: {exc}", path=str(path)) from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: the config must be a JSON object")

    estates = tuple(_load_estate(entry) for entry in raw.get("estates", []))
    if not estates:
        raise ConfigError(f"{path}: at least one estate is required -- there is nothing to check")
    seen: set[str] = set()
    for entry in estates:
        if entry.name in seen:
            raise ConfigError(
                f"{path}: duplicate estate name {entry.name!r} -- "
                "the name is the archive's key and must be unique"
            )
        seen.add(entry.name)

    timezone_name = raw.get("timezone") or str(settings.timezone())
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:  # ZoneInfoNotFoundError and friends
        raise ConfigError(f"{path}: unknown timezone {timezone_name!r}: {exc}") from exc

    trend = raw.get("trend") or {}
    delay = float(raw.get("request_delay_seconds", settings.DEFAULT_DELAY))
    if delay < 0:
        raise ConfigError(f"{path}: request_delay_seconds must be >= 0")

    size = int(raw.get("fetch_size", settings.DEFAULT_FETCH_SIZE))
    if not 1 <= size <= settings.MAX_FETCH_SIZE:
        raise ConfigError(
            f"{path}: fetch_size must be 1-{settings.MAX_FETCH_SIZE}; above that "
            "Centanet returns an empty list rather than an error",
            fetch_size=size,
        )

    return TrackerConfig(
        timezone_name=timezone_name,
        request_delay_seconds=delay,
        fetch_size=size,
        trend_window_days=int(trend.get("window_days", settings.DEFAULT_TREND_WINDOW_DAYS)),
        trend_min_samples=int(trend.get("min_samples", settings.DEFAULT_TREND_MIN_SAMPLES)),
        chart_months=int(trend.get("chart_months", settings.DEFAULT_CHART_MONTHS)),
        chart_min_points=int(trend.get("chart_min_points", settings.DEFAULT_CHART_MIN_POINTS)),
        estates=estates,
        path=path,
    )
