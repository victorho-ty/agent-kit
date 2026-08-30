"""Named speaking styles, per category.

*Which* style a drill asks for is decided here, by rotation on the entry's own
drill count, so the same word is heard in a different voice each time it comes
round and the choice is reproducible. *How* the style sounds is the model's
job -- this file only names it and describes its register in one line.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import ConfigError

CONFIG_FILE = Path(__file__).with_name("styles.json")


class StyleSet:
    def __init__(self, default: list[dict], categories: dict[str, list[dict]]):
        self.default = default
        # Lookup is case-insensitive: the agent writes the category label, and
        # "food" must find the styles filed under "Food".
        self.categories = {name.casefold(): styles for name, styles in categories.items()}

    def for_category(self, category: str, rotation: int) -> dict | None:
        """The style to ask for, chosen by rotation rather than by taste.

        `source` says whether the category had its own styles or fell back to
        the general ones -- which is how a mistyped category label shows up.
        """
        configured = self.categories.get(category.strip().casefold())
        styles = configured or self.default
        if not styles:
            return None
        chosen = styles[rotation % len(styles)]
        return {
            **chosen,
            "category": category,
            "source": "category" if configured else "default",
        }

    def as_dict(self) -> dict:
        return {"default": self.default, "categories": self.categories}


def load(path: str | Path | None = None) -> StyleSet:
    file = Path(path) if path else CONFIG_FILE
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"styles config not found: {file}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"styles config is not valid JSON: {file}: {exc}") from exc

    default = _validate(raw.get("default", []), "default")
    categories = {
        name: _validate(styles, name) for name, styles in (raw.get("categories") or {}).items()
    }
    return StyleSet(default, categories)


def _validate(styles, where: str) -> list[dict]:
    if not isinstance(styles, list):
        raise ConfigError(f"styles for {where!r} must be a list")
    for style in styles:
        if not isinstance(style, dict) or not style.get("name") or not style.get("voice"):
            raise ConfigError(f"every style in {where!r} needs a name and a voice")
    return [{"name": s["name"], "voice": s["voice"]} for s in styles]
