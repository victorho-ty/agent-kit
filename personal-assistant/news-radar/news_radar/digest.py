"""What to tell the reader, in sections they chose themselves.

There is no Telegram code here, and none anywhere in this package. Hermes owns
the channel; these tools only ever hand back JSON.

Three decisions worth stating out loud:

**"Since the last digest" is a ledger, not a clock.** This reads every item with
``digested_at IS NULL``, however many scans have happened since the last one.
That is what lets the scan and the digest run on completely independent cron
schedules: a missed scan needs no catch-up, a missed digest loses nothing and
merely makes the next one longer, and changing either cadence cannot
desynchronise the other.

**Sections come from the source's category as the config reads now**, not from
anything stored on the item. Move a source from ``tech`` to ``ai`` and
everything of its that has not gone out yet moves with it, which is what a
person expects after editing the config.

**Dates are relayed verbatim, never parsed.** ``published_text`` is the source's
own wording. Parsing it would let us sort by recency, at the price of
occasionally announcing a confidently wrong date -- and this skill's whole value
is that its facts can be trusted without checking.
"""

from __future__ import annotations

from datetime import datetime

from . import cluster, db
from .config.sources import UNCATEGORISED


def build(conn, config, now: datetime, *, commit: bool = False,
          categories: list[str] | None = None, limit: int | None = None) -> dict:
    """Everything not yet digested, clustered and grouped into sections.

    With ``commit``, every item returned is stamped before the payload is handed
    back. The order matters: stamp first, then send. A send that fails leaves
    items marked as reported, which is recoverable by reading ``items --since``;
    the other order re-announces the whole backlog after a crash, which is not.
    """
    sources = None
    if categories:
        sources = [source.name for source in config.select(categories=categories,
                                                           include_disabled=True)]
    items = db.pending_items(conn, sources, limit)

    # Group first, cluster second. Clustering runs inside a category because
    # sections are the reader's own taxonomy -- a story carried in two of them
    # is relevant to both, and merging across would force an arbitrary choice
    # about which section loses it.
    by_category: dict[str, list] = {}
    for item in items:
        by_category.setdefault(config.category_of(item.source), []).append(item)

    order = [category.name for category in config.categories]
    if UNCATEGORISED in by_category:
        order.append(UNCATEGORISED)

    sections = []
    for name in order:
        members = by_category.get(name)
        if not members:
            continue  # a section with nothing new is omitted, not printed empty
        known = config.category(name)
        stories = cluster.cluster(members, config.cluster_threshold)
        section = {
            "category": name,
            "label": known.display() if known else "Uncategorised",
            "stories": [story.to_dict() for story in stories],
        }
        if known is None:
            section["note"] = ("these come from sources that are no longer in sources.json")
        sections.append(section)

    committed = db.mark_digested(conn, [item.id for item in items], now) if commit else 0
    earliest = min((item.first_seen_at for item in items), default=None)

    return {
        "ok": True,
        "as_of": now.isoformat(),
        "since": earliest,
        "count": len(items),
        "stories": sum(len(section["stories"]) for section in sections),
        "committed": committed,
        "sections": sections,
        "totals": {
            "items": len(items),
            "stories": sum(len(section["stories"]) for section in sections),
            "sources": len({item.source for item in items}),
            "categories": len(sections),
        },
    }


def format_digest(payload: dict) -> str:
    """A ready-to-send body. The agent may reword the intro; the facts are here."""
    if not payload["count"]:
        return ""

    lines = [
        f"{payload['stories']} stories from "
        f"{payload['totals']['sources']} sources."
    ]
    for section in payload["sections"]:
        lines.append("")
        lines.append(section["label"])
        for story in section["stories"]:
            headline = f"• {story['title']}"
            if story.get("published_text"):
                headline += f" — {story['published_text']}"
            lines.append(headline)
            lines.append("  " + " · ".join(story["sources"]))
            lines.append(f"  {story['url']}")
        if section.get("note"):
            lines.append(f"  ⚠ {section['note']}")
    return "\n".join(lines)
