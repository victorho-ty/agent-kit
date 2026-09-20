"""Parse a free-text Telegram message into (description, amount) items."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Split on ; newline and Chinese enumeration comma; also on "," unless it is a
# thousands separator inside a number.
_SPLIT = re.compile(r"[;\n、]+|,(?!\d{3}(?:\D|$))")

_AMOUNT = re.compile(
    r"(?P<cur>HK\$|US\$|RMB|CNY|HKD|USD|\$|￥|¥|€|£)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<suffix>元|蚊|dollars?|bucks?|hkd|usd)?",
    re.IGNORECASE,
)

_LEADING_NOISE = re.compile(r"^(?:(?:i\s+)?(?:paid|spent|bought|got|for|on|at|the|a|an)\b\s*)+", re.IGNORECASE)
_TRAILING_NOISE = re.compile(r"\s*\b(?:for|on|at|each|total)\b$", re.IGNORECASE)
_EDGE_PUNCT = re.compile(r"^[\s\-–—:=@.]+|[\s\-–—:=@.]+$")


@dataclass(frozen=True)
class ParsedItem:
    description: str
    amount: float
    raw: str


def _clean(text: str) -> str:
    text = _EDGE_PUNCT.sub("", text)
    text = _LEADING_NOISE.sub("", text)
    text = _TRAILING_NOISE.sub("", text)
    return _EDGE_PUNCT.sub("", text).strip()


def _pick_amount(chunk: str):
    """Prefer a currency-marked number, else the last number in the chunk."""
    matches = [m for m in _AMOUNT.finditer(chunk) if m.group("num")]
    if not matches:
        return None
    marked = [m for m in matches if m.group("cur") or m.group("suffix")]
    return (marked or matches)[-1]


def parse_message(text: str) -> tuple[list[ParsedItem], list[str]]:
    """Return (items, ignored-chunks). A chunk with no number is ignored."""
    items: list[ParsedItem] = []
    ignored: list[str] = []

    for chunk in _SPLIT.split(text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _pick_amount(chunk)
        if not match:
            ignored.append(chunk)
            continue

        amount = float(match.group("num").replace(",", ""))
        description = _clean(chunk[: match.start()] + " " + chunk[match.end() :])
        if not description:
            ignored.append(chunk)
            continue
        items.append(ParsedItem(description=description, amount=amount, raw=chunk))

    return items, ignored
