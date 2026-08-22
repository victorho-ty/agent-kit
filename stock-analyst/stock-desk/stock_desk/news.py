"""News intake: fetch on a schedule, alert on an event.

Headlines come from two MCP servers -- Yahoo Finance for uncapped breadth and
Alpha Vantage for scored sentiment -- via :mod:`stock_desk.feeds`, which also
decides whether an article is about the ticker it arrived under. Nothing is
fetched over a keyword search: the Google News RSS this module used to spray
across every ticker, competitor and sector phrase produced twenty-seven requests
a poll and a majority of results that were about somebody else.

The split this module exists to enforce:

* :func:`poll` runs on a timer, writes rows, and tells nobody. It is pure Python
  and costs no model tokens however often it runs.
* :func:`pending` returns only rows whose ``notified_at`` is null, clustered so
  one story carried by four outlets is one entry.

So polling frequency and alert frequency are independent knobs. Poll every
fifteen minutes if you like; a quiet afternoon still wakes nobody, because
:func:`pending_count` returns 0 and the cron wrapper never invokes the agent.

Three filters run in series on the way in, and the order is load-bearing:
the subject gate (is this about us) in :mod:`stock_desk.feeds`, then the
materiality classifier (is this an event) in :func:`store`, then clustering.
Judging materiality after clustering would score a story on one arbitrary copy
of it; judging it before the subject gate would spend effort on articles about
other companies.

Deduplication happens twice, deliberately. **Across polls** by URL hash, in a
UNIQUE column, so the same article seen at 10:00 and 11:00 is one row. **Within
a batch** by title overlap, so one event written up by Reuters, Bloomberg and two
aggregators is one story. The first is exact and permanent; the second is fuzzy
and recomputed each time, because which items happen to be pending together is
an accident not worth persisting.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import urllib.parse
from dataclasses import replace
from datetime import datetime

from . import feeds, materiality
from .db import normalize
from .models import NewsItem, Story, TickerConfig

# Parameters that identify a *visit*, not an *article*. Left in place they defeat
# the URL dedupe entirely: the same story arrives with a fresh utm_ tag on every
# poll and looks new every time.
TRACKING_PARAMS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "icid",
        "at_medium", "at_campaign", "ncid", "guccounter",
    }
)

STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from by with
is are was were be been being as it its he she they them his her their we our you your
new now says say said report reports amid over after before into out up down about
stock stocks shares share inc corp ltd plc holdings group company co
""".split())

_TOKEN = re.compile(r"[0-9a-z㐀-鿿]+(?:-[0-9a-z㐀-鿿]+)*")

MIN_SIGNIFICANT_TOKENS = 2


# ------------------------------------------------------------------ URL identity


def canonical_url(url: str) -> str:
    """Strip tracking parameters and the fragment; keep everything else."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/"), urllib.parse.urlencode(kept), "")
    )


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def domain_of(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return "unknown"
    return host[4:] if host.startswith("www.") else (host or "unknown")


# -------------------------------------------------------------------- clustering


def signature(title: str) -> frozenset[str]:
    """The significant words of a headline, order-insensitive.

    The stopword list carries the usual grammar plus the words in every financial
    headline ever written -- ``stock``, ``shares``, ``inc``. Left in, they make
    two unrelated stories look similar purely because both are about equities.
    """
    tokens = _TOKEN.findall(normalize(title))
    significant = {token for token in tokens if token not in STOPWORDS and len(token) > 1}
    return frozenset(significant or tokens)


def similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Overlap coefficient: shared words over the *smaller* set.

    Not Jaccard. Dividing by the union punishes a headline for being long even
    when it contains the other one whole -- which is exactly the pair that has to
    collapse: "Nvidia beats" against "Nvidia beats estimates, guides higher".
    """
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def same_story(first: NewsItem, second: NewsItem, threshold: float) -> bool:
    if first.url_hash and first.url_hash == second.url_hash:
        return True
    left, right = signature(first.title), signature(second.title)
    if len(left) < MIN_SIGNIFICANT_TOKENS or len(right) < MIN_SIGNIFICANT_TOKENS:
        # Too little to go on -- treat as distinct rather than merge two
        # unrelated thin headlines into a story that never existed.
        return False
    return similarity(left, right) >= threshold


def cluster(items: list[NewsItem], threshold: float) -> list[Story]:
    """Single-link agglomeration in one pass, preserving first-seen order.

    Clustering happens **within a ticker**, never across. A story touching two
    holdings is genuinely relevant to both, and merging would force an arbitrary
    choice about which position loses it.
    """
    by_ticker: dict[str, list[NewsItem]] = {}
    for item in items:
        by_ticker.setdefault(item.ticker, []).append(item)

    stories: list[Story] = []
    for group in by_ticker.values():
        clusters: list[list[NewsItem]] = []
        for item in group:
            for members in clusters:
                if any(same_story(member, item, threshold) for member in members):
                    members.append(item)
                    break
            else:
                clusters.append([item])
        stories.extend(
            Story(title=members[0].title, url=members[0].url, items=tuple(members))
            for members in clusters
        )
    return stories


# ------------------------------------------------------------------------ fetch


def _strip_outlet_suffix(title: str, outlet: str | None) -> str:
    """Some syndication feeds append " - Outlet" to the headline. It is not part
    of the title and, left in, becomes a shared token across every story from
    that outlet, inflating similarity and merging unrelated events."""
    if outlet and title.endswith(f" - {outlet}"):
        return title[: -(len(outlet) + 3)].strip()
    return title.strip()


# ------------------------------------------------------------------------ store


# Classes that never reach a report, whatever they score.
#
# ``price_move`` because it is the market reacting to something rather than the
# something, and the setup detector already describes price action with a pivot
# and a volume ratio.
#
# ``unclassified`` because on a two-feed poll it is the majority class -- 58 of
# 96 rows on the first live run -- and almost all of it is think-pieces. The
# cost is real and worth stating plainly: a genuine event whose phrasing no
# pattern recognises disappears from the report. That is why the rows are still
# stored with their class, why :func:`suppression_breakdown` exists, and why an
# unclassified count climbing run over run is the signal that a pattern is
# missing rather than that the news went quiet.
SUPPRESSED_CLASSES = frozenset({"price_move", "unclassified"})


def _suppress(verdict) -> bool:
    """Never worth waking anybody for, whatever else is true of it.

    Noise, plus bare price moves. A price move is the market reacting to
    something rather than the something, and the desk already describes price
    action far better than a headline does -- the setup detector reports the
    pivot, the volume and the distance to it. Alerting on "Nvidia stock rises
    as it bets on robots" adds nothing and arrives every single day.

    The row is still stored and still carries its class and score; it is simply
    invisible to :func:`pending`.
    """
    return verdict.is_noise or verdict.event_class in SUPPRESSED_CLASSES



def store(
    conn: sqlite3.Connection,
    items: list[NewsItem],
    now: datetime,
    absorb: bool = False,
    held: frozenset[str] | set[str] = frozenset(),
) -> int:
    """Insert what is new, judging each item on the way in.

    The UNIQUE ``(ticker, url_hash)`` does the across-poll dedupe, scoped to the
    ticker so a peer story reaches every entry that declared that peer. Every row is stored --
    including the ones the classifier rejects -- because "why was this not
    reported" is a question worth being able to answer, and the row is the only
    thing that can answer it. Rejected rows carry ``suppressed = 1`` and are
    invisible to :func:`pending`, so storing them wakes nobody.

    ``absorb`` stamps rows as already reported on the way in -- the silent first
    poll for a ticker.
    """
    stamped = now.isoformat() if absorb else None
    inserted = 0
    for item in items:
        digest = item.url_hash or url_hash(item.url)
        verdict = materiality.assess(
            item.title,
            (item.source,) if item.source else (),
            held=item.ticker in held,
            peer_of=item.peer_of,
        )
        cursor = conn.execute(
            """
            INSERT INTO news (ticker, peer_of, url_hash, url, title, source,
                              published_at, published_text, first_seen_at, notified_at,
                              feed, summary, sentiment_score, sentiment_label, relevance,
                              event_class, materiality, band, suppressed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker, url_hash) DO NOTHING
            """,
            (
                item.ticker,
                item.peer_of,
                digest,
                item.url,
                item.title,
                item.source,
                item.published_at,
                item.published_text,
                now.isoformat(),
                stamped,
                item.feed,
                item.summary,
                item.sentiment_score,
                item.sentiment_label,
                item.relevance,
                verdict.event_class,
                verdict.score,
                verdict.band,
                1 if _suppress(verdict) else 0,
            ),
        )
        inserted += cursor.rowcount if cursor.rowcount > 0 else 0
    return inserted


# ------------------------------------------------------------------------- poll


def suppression_breakdown(conn: sqlite3.Connection, since_iso: str | None = None) -> dict:
    """How many rows each class swallowed. The audit trail for what is not said.

    Suppression is invisible by construction, so this is the only way to notice
    that a real event class has started arriving in a shape the patterns miss.
    """
    sql = "SELECT event_class, COUNT(*) AS n FROM news WHERE suppressed = 1"
    params: tuple = ()
    if since_iso:
        sql += " AND first_seen_at >= ?"
        params = (since_iso,)
    return {row["event_class"]: row["n"] for row in conn.execute(sql + " GROUP BY event_class", params)}


def poll(
    conn: sqlite3.Connection,
    entries: list[TickerConfig],
    now: datetime,
    av_budget: int = 0,
    since: datetime | None = None,
    held: frozenset[str] | set[str] = frozenset(),
) -> dict:
    """Fetch both feeds and store what has not been seen. Tells nobody.

    ``av_budget`` is the number of Alpha Vantage calls this poll may spend, and
    it is a hard ceiling rather than a hint. The free tier allows 25 a day for
    the whole profile, macro readings included, so a poller helping itself to as
    many as it liked would take the macro section down with it partway through
    the afternoon. Zero means Yahoo only, which is a complete and perfectly
    usable poll -- it simply carries no sentiment scores.

    Yahoo is always polled and is never budgeted: it is free and uncapped, and it
    is what keeps the desk working when the Alpha Vantage key is missing or its
    quota is gone.
    """
    active = [entry for entry in entries if entry.wants("competitor")]

    # Computed once, before anything is inserted. A ticker's *first* poll is
    # silent by design -- a back catalogue is not news -- and evaluating this
    # per-request would let the first feed for a ticker seed it and the second
    # report as new.
    already_seeded = {row["ticker"] for row in conn.execute("SELECT DISTINCT ticker FROM news")}

    plan: list[tuple[str, str, str | None, tuple[str, ...]]] = []
    for entry in active:
        plan.extend(feeds.yahoo_requests(entry))

    items, failures = feeds.yahoo_news(plan)

    av_spent = 0
    if av_budget > 0:
        subjects = [
            (entry.ticker, feeds.aliases(entry.ticker, entry.company_name))
            for entry in active
        ][:av_budget]
        av_items, av_failures, av_spent = feeds.alphavantage_news(
            subjects,
            time_from=feeds.av_time_from(since) if since else None,
        )
        items.extend(av_items)
        failures.extend(av_failures)

    before = int(
        conn.execute("SELECT COUNT(*) AS n FROM news WHERE suppressed = 1").fetchone()["n"]
    )

    inserted = absorbed = 0
    by_ticker: dict[str, list[NewsItem]] = {}
    for item in items:
        by_ticker.setdefault(item.ticker, []).append(item)
    for ticker, group in by_ticker.items():
        absorb = ticker not in already_seeded
        stored_now = store(conn, group, now, absorb=absorb, held=held)
        if absorb:
            absorbed += stored_now
        else:
            inserted += stored_now

    after = int(
        conn.execute("SELECT COUNT(*) AS n FROM news WHERE suppressed = 1").fetchone()["n"]
    )

    if not plan:
        status = "skipped"
    elif failures and not items:
        status = "error"
    elif failures:
        status = "partial"
    else:
        status = "ok"

    return {
        "status": status,
        "feeds": ["yahoo"] + (["alphavantage"] if av_spent else []),
        "yahoo_requests": len(plan),
        "alphavantage_calls": av_spent,
        "seen": len(items),
        "new": inserted,
        "suppressed": after - before,
        "suppressed_by_class": suppression_breakdown(conn, now.isoformat()[:10]),
        "absorbed": absorbed,
        "seeded_tickers": sorted({t for _, t, _, _ in plan} - already_seeded),
        "failures": failures,
    }


# ------------------------------------------------------------------------- read


def _rows_to_items(rows) -> list[NewsItem]:
    return [
        NewsItem(
            ticker=row["ticker"],
            title=row["title"],
            url=row["url"],
            source=row["source"],
            published_at=row["published_at"],
            published_text=row["published_text"],
            peer_of=row["peer_of"],
            url_hash=row["url_hash"],
            item_id=row["id"],
            feed=row["feed"],
            summary=row["summary"],
            sentiment_score=row["sentiment_score"],
            sentiment_label=row["sentiment_label"],
            relevance=row["relevance"],
            event_class=row["event_class"],
            materiality=row["materiality"],
            band=row["band"],
        )
        for row in rows
    ]


def pending_count(conn: sqlite3.Connection, tickers: list[str] | None = None) -> int:
    """How many reportable rows are waiting. The gate the cron wrapper branches on.

    Deliberately a count and not a payload: answering "is anything waiting" must
    not require loading, clustering or scoring anything. Suppressed rows are
    excluded, so an afternoon of nothing but listicles leaves this at zero and
    the agent is never invoked.
    """
    clause = "notified_at IS NULL AND suppressed = 0"
    if tickers:
        marks = ",".join("?" * len(tickers))
        sql = f"SELECT COUNT(*) AS n FROM news WHERE {clause} AND ticker IN ({marks})"
        return int(conn.execute(sql, tickers).fetchone()["n"])
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM news WHERE {clause}").fetchone()["n"])


def _score_stories(
    stories: list[Story], floor: int, held: frozenset[str] | set[str]
) -> list[Story]:
    """Re-judge each cluster over all of its sources, then rank and cut.

    Scoring here rather than reusing the per-row verdict is the whole point of
    doing it twice: a story carried by one aggregator and a story carried by
    Reuters, Bloomberg and the FT are the same headline and not the same news.
    """
    scored: list[Story] = []
    for story in stories:
        first = story.items[0]
        verdict = materiality.assess(
            story.title,
            story.sources,
            held=first.ticker in held,
            peer_of=first.peer_of,
        )
        if verdict.score >= floor:
            scored.append(replace(story, verdict=verdict))
    scored.sort(key=lambda s: (-s.verdict.score, s.items[0].item_id or 0))
    return scored


def pending(
    conn: sqlite3.Connection,
    threshold: float = 0.6,
    tickers: list[str] | None = None,
    limit: int = 200,
    floor: int = 0,
    held: frozenset[str] | set[str] = frozenset(),
) -> list[Story]:
    """Unreported items, clustered, scored and ranked. An empty list is normal."""
    clause = "notified_at IS NULL AND suppressed = 0"
    if tickers:
        marks = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"""SELECT * FROM news WHERE {clause} AND ticker IN ({marks})
                ORDER BY first_seen_at, id LIMIT ?""",
            (*tickers, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM news WHERE {clause} ORDER BY first_seen_at, id LIMIT ?",
            (limit,),
        ).fetchall()
    return _score_stories(cluster(_rows_to_items(rows), threshold), floor, held)


def recent(
    conn: sqlite3.Connection, ticker: str, since_iso: str, threshold: float = 0.6
) -> list[Story]:
    """Everything seen for a ticker since a timestamp, reported or not.

    The on-demand read path -- answering "what has been going on with this" does
    not consume anything the next scheduled alert would have carried. Suppressed
    rows stay out: this is a question about the news, not about the filter.
    """
    rows = conn.execute(
        """SELECT * FROM news WHERE ticker = ? AND first_seen_at >= ? AND suppressed = 0
           ORDER BY first_seen_at, id""",
        (ticker, since_iso),
    ).fetchall()
    return _score_stories(cluster(_rows_to_items(rows), threshold), 0, frozenset())


def mark_notified(conn: sqlite3.Connection, stories: list[Story], now: datetime) -> int:
    """Stamp every item in every story. Call **after** the message is sent.

    Stamping the whole cluster matters: the four outlets that carried one story
    were reported once, so all four must be marked, or the next poll surfaces the
    same event again under a different byline.
    """
    ids = [item.item_id for story in stories for item in story.items if item.item_id]
    if not ids:
        return 0
    conn.executemany(
        "UPDATE news SET notified_at = ? WHERE id = ? AND notified_at IS NULL",
        [(now.isoformat(), item_id) for item_id in ids],
    )
    return len(ids)
