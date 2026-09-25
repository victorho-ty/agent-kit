"""The vocabulary, compiled once.

Every term becomes a word-boundary regex, case-insensitive. Word boundaries are
the whole reason this is not a substring scan: ``"ism"`` inside ``"mechanism"``
and ``"cds"`` inside ``"cdss"`` would both bucket a headline into a sector it
has nothing to do with, and the agent would have no way of knowing.

Terms containing characters a word boundary cannot sit next to -- ``s&p``,
``chapter 11`` -- fall back to a boundary on whichever end accepts one, which is
what keeps them matchable at all.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from ..errors import ConfigError
from .seeds import strip_comments

TAXONOMY_FILE = Path(__file__).parent / "taxonomy.json"


def _pattern(term: str) -> re.Pattern:
    escaped = re.escape(term)
    left = r"\b" if term[:1].isalnum() else ""
    right = r"\b" if term[-1:].isalnum() else ""
    return re.compile(f"{left}{escaped}{right}", re.IGNORECASE)


def _prefix_pattern(term: str) -> re.Pattern:
    """A boundary on the left only, so the term also matches as a word stem.

    For exclusion that is the point: "Taiwan" must also drop "Taiwanese" and
    "Australia" must also drop "Australian dollar", or the list needs every
    adjective spelled out and still leaks the one nobody thought of.
    """
    left = r"\b" if term[:1].isalnum() else ""
    return re.compile(f"{left}{re.escape(term)}", re.IGNORECASE)


def _compile(mapping: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    return {key: [_pattern(term) for term in terms if term] for key, terms in mapping.items()}


@dataclasses.dataclass(frozen=True)
class Taxonomy:
    """Sector buckets, event signals, the gate's vocabulary, and the drop list."""

    sectors: dict[str, list[re.Pattern]]
    signals: dict[str, list[re.Pattern]]
    finance_terms: list[tuple[str, re.Pattern]]
    exclude: list[tuple[str, re.Pattern]]
    path: Path

    def match_groups(self, text: str, groups: dict[str, list[re.Pattern]]) -> list[str]:
        return [key for key, patterns in groups.items() if any(p.search(text) for p in patterns)]

    def finance_hits(self, text: str) -> list[str]:
        """Distinct finance terms present. The count is the gate; the list is why."""
        return [term for term, pattern in self.finance_terms if pattern.search(text)]

    def excluded_by(self, text: str) -> str | None:
        """The exclude term ``text`` matches, or ``None`` if it may be kept."""
        for term, pattern in self.exclude:
            if pattern.search(text):
                return term
        return None


def load_taxonomy(path: Path | str | None = None) -> Taxonomy:
    from .. import settings

    path = Path(path) if path is not None else settings.taxonomy_path()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.loads(strip_comments(handle.read()))
    except FileNotFoundError as exc:
        raise ConfigError(f"taxonomy file not found: {path}", path=str(path)) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}", path=str(path)) from exc

    for key in ("sectors", "signals", "finance_terms"):
        if key not in raw:
            raise ConfigError(f"{path}: missing {key!r}", path=str(path))

    return Taxonomy(
        sectors=_compile(raw["sectors"]),
        signals=_compile(raw["signals"]),
        finance_terms=[(term, _pattern(term)) for term in raw["finance_terms"] if term],
        # Optional: an empty drop list is a valid choice, not a broken file.
        exclude=[(term, _prefix_pattern(term)) for term in raw.get("exclude", []) if term],
        path=path,
    )
