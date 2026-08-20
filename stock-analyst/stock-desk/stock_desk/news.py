"""News intake: fetch on a schedule, alert on an event.

The split this module exists to enforce:

* :func:`poll` runs on a timer, writes rows, and tells nobody. It is pure Python
  and costs no model tokens however often it runs.
* :func:`pending` returns only rows whose ``notified_at`` is null, clustered so
  one story carried by four outlets is one entry.

So polling frequency and alert frequency are independent knobs. Poll every
fifteen minutes if you like; a quiet afternoon still wakes nobody, because
:func:`pending_count` returns 0 and the cron wrapper never invokes the agent.

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
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import settings
from .db import normalize
from .errors import FetchError
from .models import NewsItem, Story, TickerConfig

GOOGLE_NEWS = "https://news.google.com/rss/search"

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
    """Google appends " - Outlet" to every headline. It is not part of the title
    and, left in, becomes a shared token that inflates every similarity score."""
    if outlet and title.endswith(f" - {outlet}"):
        return title[: -(len(outlet) + 3)].strip()
    return title.strip()


def _parse_pub_date(raw: str | None) -> tuple[str | None, str | None]:
    """``(ISO8601, the source's own wording)``.

    The original text is kept and passed through unconverted, the same rule
    news-radar follows: the agent relays what the publisher wrote rather than
    computing how long ago it was.
    """
    if not raw:
        return None, None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat(), raw.strip()
    return None, raw.strip()


def query_for(ticker: str, company_name: str | None = None) -> str:
    """What to actually search for.

    The company name when known, because a bare symbol is a terrible query --
    ``T``, ``ALL`` and ``KEY`` are real tickers, and ``0700.HK`` matches nothing
    a journalist ever typed.
    """
    if company_name:
        return f'"{company_name}" stock'
    bare = ticker.split(".")[0]
    return f'"{bare}" stock' if bare.isalpha() else f"{ticker} stock"


def fetch_google_news(
    query: str, ticker: str, peer_of: str | None = None, limit: int = 20
) -> list[NewsItem]:
    """Google News RSS for one query. Raises :class:`FetchError`; never partial."""
    params = urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    request = urllib.request.Request(
        f"{GOOGLE_NEWS}?{params}", headers={"User-Agent": "hermes-stock-desk/0.1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.http_timeout()) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"google news could not be reached for {query}", query=query) from exc

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FetchError(f"google news returned unparseable XML for {query}", query=query) from exc

    items: list[NewsItem] = []
    for node in list(root.iterfind(".//item"))[:limit]:
        link = (node.findtext("link") or "").strip()
        raw_title = (node.findtext("title") or "").strip()
        if not link or not raw_title:
            continue
        source_node = node.find("source")
        outlet = (source_node.text or "").strip() if source_node is not None else None
        source_url = source_node.get("url") if source_node is not None else None
        published_at, published_text = _parse_pub_date(node.findtext("pubDate"))
        items.append(
            NewsItem(
                ticker=ticker,
                title=_strip_outlet_suffix(raw_title, outlet),
                url=link,
                source=outlet or (domain_of(source_url) if source_url else domain_of(link)),
                published_at=published_at,
                published_text=published_text,
                peer_of=peer_of,
                url_hash=url_hash(link),
            )
        )
    return items


def queries_for(entry: TickerConfig) -> list[tuple[str, str, str | None]]:
    """``(query, ticker, peer_of)`` for a watchlist entry and each of its peers.

    Peer stories are filed under the *watched* ticker with ``peer_of`` naming the
    competitor. That is what makes "AMD launches a rival part" show up in NVDA's
    section, which is the whole point of the competitor analysis.
    """
    plan = [(query_for(entry.ticker, entry.company_name), entry.ticker, None)]
    for peer in entry.competitors:
        plan.append((query_for(peer), entry.ticker, peer))
    for keyword in entry.sector_keywords:
        plan.append((keyword, entry.ticker, None))
    return plan


# ------------------------------------------------------------------------- store


def poll(
    conn: sqlite3.Connection,
    entries: list[TickerConfig],
    now: datetime,
    limit_per_query: int = 20,
    delay: float | None = None,
) -> dict:
    """Fetch every configured query and store what has not been seen before.

    Writes rows and returns counts. Tells nobody: this is the half of the design
    that runs on a schedule.
    """
    pause = settings.request_delay() if delay is None else delay
    plan: list[tuple[str, str, str | None]] = []
    for entry in entries:
        if entry.wants("competitor"):
            plan.extend(queries_for(entry))

    # A ticker's *first* poll is silent by design: whatever is already published
    # gets stored and stamped as reported without anybody being told. A back
    # catalogue is not news, and without this a newly added ticker floods its
    # first report with two hundred stories and teaches the operator to skim.
    #
    # Computed once, before anything is inserted -- otherwise the first query for
    # a ticker seeds it and the second query for the same ticker reports as new.
    already_seeded = {
        row["ticker"] for row in conn.execute("SELECT DISTINCT ticker FROM news")
    }

    inserted = 0
    absorbed = 0
    seen = 0
    failures: list[dict] = []
    for index, (query, ticker, peer_of) in enumerate(plan):
        if index and pause:
            time.sleep(pause)
        try:
            fetched = fetch_google_news(query, ticker, peer_of, limit=limit_per_query)
        except FetchError as exc:
            failures.append({"query": query, "ticker": ticker, "error": exc.message})
            continue
        seen += len(fetched)
        absorb = ticker not in already_seeded
        stored_now = store(conn, fetched, now, absorb=absorb)
        if absorb:
            absorbed += stored_now
        else:
            inserted += stored_now

    if not plan:
        status = "skipped"
    elif len(failures) == len(plan):
        status = "error"
    elif failures:
        status = "partial"
    else:
        status = "ok"

    return {
        "status": status,
        "queries": len(plan),
        "seen": seen,
        "new": inserted,
        "absorbed": absorbed,
        "seeded_tickers": sorted({t for _, t, _ in plan} - already_seeded),
        "failures": failures,
    }


def store(
    conn: sqlite3.Connection, items: list[NewsItem], now: datetime, absorb: bool = False
) -> int:
    """Insert what is new. The UNIQUE url_hash does the across-poll dedupe.

    ``absorb`` stamps the rows as already reported on the way in -- the silent
    first poll for a ticker.
    """
    stamped = now.isoformat() if absorb else None
    inserted = 0
    for item in items:
        digest = item.url_hash or url_hash(item.url)
        cursor = conn.execute(
            """
            INSERT INTO news (ticker, peer_of, url_hash, url, title, source,
                              published_at, published_text, first_seen_at, notified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (url_hash) DO NOTHING
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
            ),
        )
        inserted += cursor.rowcount if cursor.rowcount > 0 else 0
    return inserted


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
        )
        for row in rows
    ]


def pending_count(conn: sqlite3.Connection, tickers: list[str] | None = None) -> int:
    """How many unnotified rows exist. The gate the cron wrapper branches on.

    Deliberately a count and not a payload: answering "is anything waiting"
    must not require loading, clustering or rendering anything.
    """
    if tickers:
        marks = ",".join("?" * len(tickers))
        sql = f"SELECT COUNT(*) AS n FROM news WHERE notified_at IS NULL AND ticker IN ({marks})"
        return int(conn.execute(sql, tickers).fetchone()["n"])
    return int(
        conn.execute("SELECT COUNT(*) AS n FROM news WHERE notified_at IS NULL").fetchone()["n"]
    )


def pending(
    conn: sqlite3.Connection,
    threshold: float = 0.6,
    tickers: list[str] | None = None,
    limit: int = 200,
) -> list[Story]:
    """Unnotified items, clustered. An empty list is the normal case."""
    if tickers:
        marks = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"""SELECT * FROM news WHERE notified_at IS NULL AND ticker IN ({marks})
                ORDER BY first_seen_at, id LIMIT ?""",
            (*tickers, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM news WHERE notified_at IS NULL ORDER BY first_seen_at, id LIMIT ?",
            (limit,),
        ).fetchall()
    return cluster(_rows_to_items(rows), threshold)


def recent(
    conn: sqlite3.Connection, ticker: str, since_iso: str, threshold: float = 0.6
) -> list[Story]:
    """Everything seen for a ticker since a timestamp, notified or not.

    The on-demand read path -- answering "what has been going on with this" does
    not consume anything the next scheduled alert would have carried.
    """
    rows = conn.execute(
        "SELECT * FROM news WHERE ticker = ? AND first_seen_at >= ? ORDER BY first_seen_at, id",
        (ticker, since_iso),
    ).fetchall()
    return cluster(_rows_to_items(rows), threshold)


def mark_notified(conn: sqlite3.Connection, stories: list[Story], now: datetime) -> int:
    """Stamp every item in every story. Call **after** the message is sent.

    Stamping the whole cluster matters: the four outlets that carried one story
    were reported once, so all four must be marked, or the next poll surfaces the
    same event under a different byline.
    """
    ids = [item.item_id for story in stories for item in story.items if item.item_id]
    if not ids:
        return 0
    conn.executemany(
        "UPDATE news SET notified_at = ? WHERE id = ? AND notified_at IS NULL",
        [(now.isoformat(), item_id) for item_id in ids],
    )
    return len(ids)
