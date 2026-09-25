"""Deciding whether a url the operator asked for is a finance feed worth turning on.

Only ``news-monitor add`` reaches this module, and only when the operator has
asked for a source: nothing in this bundle goes looking for feeds. Which source
to add is the operator's call. Whether the url they handed over is actually a
live feed is not something anyone should take on trust, and that is this
module: every candidate is fetched, parsed and measured before it becomes a row.

The gate, in order. The first three decide whether there is a feed at all; the
last three decide whether it goes live:

1. **already tracked** -- the url, normalised, is in the ``feed`` table. Not an
   insert and not an error the agent should retry: the answer is to tell the
   operator which existing feed it already is.
2. **unreachable** -- the fetch failed after its retries. Rejected outright.
3. **not a feed** -- the document is not XML, or is XML with no items in it.
   Rejected outright. This is what catches an HTML page, a parked domain, a
   login wall and a rate-limit notice served with a 200.
4. **thin** -- fewer than ``min_items`` entries. A feed with one item is a feed
   that has just been set up or has been abandoned, and there is no way to tell
   which from one fetch.
5. **stale** -- no item dated inside ``max_age_days``. A feed whose newest story
   is from 2019 is a dead section nobody took down.
6. **off topic** -- fewer than ``min_finance_hits`` distinct finance terms
   across the feed's title, its description and its recent headlines. This is
   the "finance only" instruction, made into something a machine can apply: it
   is crude, and it is why failing it *stores the feed disabled* rather than
   dropping it. A feed the gate was wrong about is one ``enable`` away.

A candidate that reaches step 4 is always stored. Passing means enabled;
failing 4, 5 or 6 means enabled = 0 with the reason on the row, waiting for the
operator to decide. Nothing is silently discarded: the operator asked for
this url, and a held-back row keeps both the request and the reason in one
place.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta

from . import feed as feed_parser
from . import settings
from .config import Taxonomy
from .errors import CandidateError, FetchError

# Enough of the feed to judge it by. The gate reads headlines, not archives.
PROBE_ITEMS = 25

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_FEED_WORDS = re.compile(r"\b(rss|feed|xml|atom|news)\b", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class Probe:
    """What one candidate url turned out to be."""

    url: str
    title: str | None
    description: str | None
    entries: list
    newest: datetime | None
    finance_hits: list[str]
    sectors: list[str]


@dataclasses.dataclass(frozen=True)
class Verdict:
    """Whether it becomes a row, and whether that row is on."""

    enable: bool
    reason: str
    detail: dict

    @property
    def passed(self) -> bool:
        return self.reason == "pass"


def slugify(value: str, fallback: str) -> str:
    """A feed name from a feed's own title.

    Trailing "RSS", "Feed" and "News" are dropped -- almost every feed title
    ends in one and a source list where half the names end in "-rss" reads
    worse than one where none do.
    """
    stripped = _FEED_WORDS.sub(" ", value or "")
    slug = _SLUG_STRIP.sub("-", stripped.lower()).strip("-")
    return (slug or _SLUG_STRIP.sub("-", fallback.lower()).strip("-") or "feed")[:48]


def unique_name(candidate: str, taken) -> str:
    """``reuters-business``, then ``reuters-business-2``, and so on."""
    if candidate not in taken:
        return candidate
    for suffix in range(2, 100):
        attempt = f"{candidate}-{suffix}"
        if attempt not in taken:
            return attempt
    raise CandidateError(f"cannot find a free name near {candidate!r}", name=candidate)


def probe(url: str, taxonomy: Taxonomy, *, fetcher=None) -> Probe:
    """Fetch a candidate and measure it. Raises on anything that is not a feed."""
    fetcher = fetcher or _default_fetcher
    try:
        response = fetcher(url)
    except FetchError as exc:
        raise CandidateError(
            f"candidate unreachable: {exc.message}", url=url, reason="unreachable"
        ) from exc

    document = response.text
    try:
        entries = feed_parser.parse(
            document,
            "candidate",
            base_url=response.url,
            max_items=PROBE_ITEMS,
            summary_cap=settings.summary_char_cap(),
        )
    except FetchError as exc:
        raise CandidateError(
            f"candidate is not a feed: {exc.message}", url=url, reason="not_a_feed"
        ) from exc

    if not entries:
        raise CandidateError(
            "candidate parsed as XML but carries no items", url=url, reason="not_a_feed"
        )

    title = feed_parser.document_title(document)
    description = feed_parser.document_description(document)
    dates = [feed_parser.parse_date(entry.published_text) for entry in entries]
    newest = max((date for date in dates if date), default=None)

    corpus = "\n".join(
        [title or "", description or ""]
        + [f"{entry.title}\n{entry.summary or ''}" for entry in entries]
    )
    return Probe(
        url=response.url or url,
        title=title,
        description=description,
        entries=entries,
        newest=newest,
        finance_hits=taxonomy.finance_hits(corpus),
        sectors=taxonomy.match_groups(corpus, taxonomy.sectors),
    )


def judge(probe_result: Probe, now: datetime, gate: dict | None = None) -> Verdict:
    """Steps 4 to 6. Everything here stores a row either way."""
    gate = gate or settings.gate()
    detail = {
        "items": len(probe_result.entries),
        "finance_hits": probe_result.finance_hits,
        "newest_item_at": probe_result.newest.isoformat() if probe_result.newest else None,
        "sector_hints": probe_result.sectors,
        "gate": gate,
    }

    if len(probe_result.entries) < gate["min_items"]:
        return Verdict(False, "thin", detail)

    if probe_result.newest is not None:
        cutoff = now - timedelta(days=gate["max_age_days"])
        if probe_result.newest < cutoff:
            return Verdict(False, "stale", detail)
    # A feed whose items carry no parseable date at all is not called stale --
    # undated is a formatting choice, not evidence of abandonment, and the
    # Fed's own releases have been served without pubDate before now.

    if len(probe_result.finance_hits) < gate["min_finance_hits"]:
        return Verdict(False, "off_topic", detail)

    return Verdict(True, "pass", detail)


def _default_fetcher(url: str):
    from . import fetch

    return fetch.get(url)
