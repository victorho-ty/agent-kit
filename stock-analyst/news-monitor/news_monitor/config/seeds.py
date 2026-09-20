"""The feeds an empty database starts with.

This is a bootstrap, not a config. Once a feed is a row it is edited as a row,
because discovery writes rows at run time and a file the tools also wrote would
be a second truth that drifts. Re-running against a populated database inserts
nothing.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from ..errors import ConfigError

SEEDS_FILE = Path(__file__).parent / "seeds.json"


@dataclasses.dataclass(frozen=True)
class Seed:
    name: str
    url: str
    category: str
    note: str | None = None


def strip_comments(source: str) -> str:
    """Drop whole-line ``//`` comments so the shipped file can carry examples.

    Only lines whose first non-space characters are ``//`` are removed, which is
    what keeps a ``https://`` inside a value safe. Lines are blanked rather than
    deleted so a json error still reports the line number you are looking at.
    """
    return "\n".join("" if line.lstrip().startswith("//") else line for line in source.splitlines())


def load_seeds(path: Path | str | None = None) -> list[Seed]:
    from .. import settings

    path = Path(path) if path is not None else settings.seeds_path()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.loads(strip_comments(handle.read()))
    except FileNotFoundError as exc:
        raise ConfigError(f"seed file not found: {path}", path=str(path)) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}", path=str(path)) from exc

    seeds = []
    for entry in raw.get("feeds", []):
        name = str(entry.get("name", "")).strip()
        url = str(entry.get("url", "")).strip()
        if not name or not url:
            raise ConfigError(f"{path}: every seed needs a name and a url", entry=entry)
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"{path}: {name!r} has a url that is not http(s): {url!r}")
        seeds.append(
            Seed(
                name=name,
                url=url,
                category=str(entry.get("category", "general")).strip() or "general",
                note=entry.get("note"),
            )
        )
    return seeds
