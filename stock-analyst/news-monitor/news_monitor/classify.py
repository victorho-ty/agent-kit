"""Attaching sector and signal hints to a headline.

This is the smallest amount of judgement code is allowed to have, and it is
deliberately dumb: a keyword matched in the headline or in the feed's own
summary paragraph, nothing else. It reads no article body, weighs nothing, and
scores nothing.

**These are hints, not classifications.** The agent decides how to bucket an
item and whether it matters; what it gets from here is a cheap first pass so it
does not have to infer "this is about rates" from the word "FOMC" forty times an
hour. An item with no hints is not an item with nothing in it -- it is an item
whose words are not in the list.
"""

from __future__ import annotations

from .config import Taxonomy


def classify(title: str, summary: str | None, taxonomy: Taxonomy) -> tuple[list[str], list[str]]:
    """``(sector_hints, signals)`` for one headline."""
    text = f"{title}\n{summary or ''}"
    return (
        taxonomy.match_groups(text, taxonomy.sectors),
        taxonomy.match_groups(text, taxonomy.signals),
    )
