"""Where headlines come from, and which of them are actually about us.

Two MCP servers, with different strengths and the same weakness.

**yahoo-finance `get_news`** is free and uncapped, so it carries the breadth --
every watched ticker, every declared competitor, every sector member. It returns
ten items per symbol in about 6,400 tokens of JSON, of which the four fields
stored here are roughly 15%.

**alphavantage `news_sentiment`** is capped at 25 calls a day across the whole
profile, and is the only source of a scored sentiment reading. It also accepts
``time_from``, which is a genuine "since last run" primitive rather than a
window guessed from a lookback.

Neither is targeted. A live NVDA query returned a TSSI earnings transcript, a
Blackstone think-piece and thirty items of 13F churn about entirely different
companies; Yahoo's NVDA feed opened with a Micron video and an IREN story. So
every item passes a subject gate before it is stored, and the survivors pass
:mod:`stock_desk.materiality` before they can wake anybody.

## The subject gate

*Is this article about the ticker I asked for?*

Alpha Vantage answers it well if you ask the right question. Its
``relevance_score`` is useless -- all fifty items in that NVDA response scored
between 0.52 and 1.00 -- but its per-article ``ticker_sentiment`` list is
*ranked*, and the top-ranked ticker is reliably the article's real subject. On
that sample, zero of the 43 off-topic items had NVDA at the top, and the four
on-topic items that did not name NVDA in the title were caught by the alias
check instead. Combined, the gate made no errors either way.

Yahoo gives us nothing to work with -- its ``stockTickers`` array is empty on
every item observed -- so there the gate is the alias check alone. That is
deliberately strict: a Micron story reaching the desk under NVDA is worse than
losing it, because if Micron matters it is a sector member or a competitor and
arrives under its own symbol.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .errors import FetchError
from .models import NewsItem, TickerConfig
from .providers import mcp_client

YAHOO = "yahoo-finance"
ALPHAVANTAGE = "alphavantage"

# Words that identify a legal wrapper rather than a company. Stripped so that
# "NVIDIA Corporation" also matches a headline that just says "Nvidia".
_LEGAL_SUFFIX = re.compile(
    r"\b(corporation|corp|incorporated|inc|limited|ltd|plc|holdings?|group|"
    r"company|co|n\.?v\.?|s\.?a\.?|ag|se|systems?|technologies|technology|"
    r"international|worldwide)\b\.?",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s-]")


def aliases(ticker: str, company_name: str | None = None) -> tuple[str, ...]:
    """Lower-cased strings whose presence in a headline means "this is us".

    A bare symbol is only trusted at three characters or more. ``T``, ``ALL``
    and ``KEY`` are real tickers and two-letter symbols match inside ordinary
    words; below the threshold only the ``$SYM`` and ``(SYM)`` forms count, and
    those are handled by :func:`mentions` rather than here.
    """
    found: list[str] = []

    bare = ticker.split(".")[0].strip().lower()
    # A purely numeric root is never a usable alias. HK symbols are digits, and
    # "0700" matches a year, a price or a time as readily as it matches Tencent
    # -- which is also why no journalist ever writes it. Such a listing is found
    # by its name, or by the explicit $SYM and (SYM) forms in :func:`mentions`.
    if len(bare) >= 3 and any(c.isalpha() for c in bare):
        found.append(bare)

    name = (company_name or "").strip()
    if name:
        cleaned = _PUNCT.sub(" ", name).strip().lower()
        if len(cleaned) >= 3:
            found.append(cleaned)
        short = _PUNCT.sub(" ", _LEGAL_SUFFIX.sub("", name)).strip().lower()
        short = re.sub(r"\s+", " ", short)
        if len(short) >= 3 and short not in found:
            found.append(short)

    seen: dict[str, None] = {}
    for entry in found:
        seen.setdefault(entry, None)
    return tuple(seen)


def mentions(title: str, ticker: str, names: tuple[str, ...]) -> bool:
    """Does this headline name the company, by name or by symbol?"""
    text = (title or "").lower()
    if not text:
        return False
    bare = ticker.split(".")[0].strip().lower()
    # The explicit symbol forms are safe at any length -- "$T" and "(T)" are
    # unambiguous where a bare "t" is not.
    if bare and (f"${bare}" in text or f"({bare})" in text):
        return True
    return any(re.search(rf"\b{re.escape(name)}\b", text) for name in names if name)


def _iso(value) -> str | None:
    """Normalise the two vendors' timestamps to ISO-8601 UTC, or give up.

    Yahoo sends ``2026-08-21T14:47:00Z``; Alpha Vantage sends ``20260821T144700``.
    A stamp that cannot be parsed becomes None rather than today -- a story
    silently dated *now* sorts to the top of the report forever.
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ yahoo


def _yahoo_item(raw: dict, ticker: str, peer_of: str | None, names) -> NewsItem | None:
    content = raw.get("content") or raw
    # VIDEO items carry a title and a player, and nothing readable. They are a
    # meaningful share of the feed -- two of ten on a typical NVDA response.
    if content.get("contentType") not in (None, "STORY"):
        return None
    title = (content.get("title") or "").strip()
    url = ((content.get("canonicalUrl") or {}).get("url")) or (
        (content.get("clickThroughUrl") or {}).get("url")
    )
    if not title or not url:
        return None
    if not mentions(title, ticker, names):
        return None
    return NewsItem(
        ticker=ticker,
        title=title,
        url=url,
        source=((content.get("provider") or {}).get("displayName") or "").strip()
        or "Yahoo Finance",
        published_at=_iso(content.get("pubDate")),
        peer_of=peer_of,
        feed="yahoo",
        summary=(content.get("summary") or "").strip() or None,
    )


def yahoo_news(requests: list[tuple[str, str, str | None, tuple[str, ...]]]) -> tuple[
    list[NewsItem], list[dict]
]:
    """Fetch one Yahoo feed per request tuple ``(symbol, file_under, peer_of, aliases)``.

    All requests share one server session -- spawning ``uvx`` costs far more
    than the calls do. A symbol that fails is recorded and the rest continue:
    one delisted competitor must not stop the watchlist being polled.
    """
    if not requests:
        return [], []
    items: list[NewsItem] = []
    failures: list[dict] = []
    calls = [("get_news", {"symbol": symbol}) for symbol, _, _, _ in requests]
    try:
        payloads = mcp_client.call_batch(YAHOO, calls)
    except FetchError as exc:
        return [], [{"server": YAHOO, "error": exc.message}]

    for (symbol, file_under, peer_of, names), payload in zip(requests, payloads):
        if not isinstance(payload, list):
            failures.append({"symbol": symbol, "error": "unexpected payload shape"})
            continue
        for raw in payload:
            built = _yahoo_item(raw, file_under, peer_of, names)
            if built is not None:
                items.append(built)
    return items, failures


# ----------------------------------------------------------- alpha vantage


def _av_subject(raw: dict, ticker: str, names) -> tuple[bool, dict | None]:
    """Is this article about ``ticker``, and what did the vendor score it?

    Returns the matching ``ticker_sentiment`` entry when one exists, so the
    caller can record the score without searching the list twice.
    """
    scored = raw.get("ticker_sentiment") or []
    ranked = sorted(scored, key=lambda t: -(_float(t.get("relevance_score")) or 0.0))
    mine = next((t for t in ranked if t.get("ticker") == ticker), None)
    is_subject = bool(ranked) and ranked[0].get("ticker") == ticker
    return (is_subject or mentions(raw.get("title", ""), ticker, names)), mine


def _av_item(raw: dict, ticker: str, peer_of: str | None, names) -> NewsItem | None:
    title = (raw.get("title") or "").strip()
    url = (raw.get("url") or "").strip()
    if not title or not url:
        return None
    keep, scored = _av_subject(raw, ticker, names)
    if not keep:
        return None
    return NewsItem(
        ticker=ticker,
        title=title,
        url=url,
        source=(raw.get("source") or "").strip() or "Alpha Vantage",
        published_at=_iso(raw.get("time_published")),
        peer_of=peer_of,
        feed="alphavantage",
        summary=(raw.get("summary") or "").strip() or None,
        # Ticker-level, not article-level: the vendor's view of what this story
        # means *for this company*, which is the only version worth storing.
        sentiment_score=_float((scored or {}).get("ticker_sentiment_score")),
        sentiment_label=((scored or {}).get("ticker_sentiment_label") or None),
        relevance=_float((scored or {}).get("relevance_score")),
    )


def alphavantage_news(
    subjects: list[tuple[str, tuple[str, ...]]],
    time_from: str | None = None,
    limit: int = 50,
) -> tuple[list[NewsItem], list[dict], int]:
    """One call per ticker, and the count of calls actually spent.

    The spend is returned rather than logged because the caller owns the daily
    budget: 25 calls covers the whole profile including macro, and a poller that
    quietly exceeded it would take the macro readings down with it.

    ``time_from`` is the vendor's own since-parameter, in ``YYYYMMDDTHHMM``.
    Passing it is what stops every run re-reading the same back catalogue.
    """
    if not subjects:
        return [], [], 0
    calls = []
    for ticker, _names in subjects:
        arguments: dict = {"tickers": [ticker], "limit": limit, "sort": "LATEST"}
        if time_from:
            arguments["time_from"] = time_from
        calls.append(("news_sentiment", arguments))

    try:
        payloads = mcp_client.call_batch(ALPHAVANTAGE, calls)
    except FetchError as exc:
        return [], [{"server": ALPHAVANTAGE, "error": exc.message}], 0

    items: list[NewsItem] = []
    failures: list[dict] = []
    for (ticker, names), payload in zip(subjects, payloads):
        if not isinstance(payload, dict):
            failures.append({"ticker": ticker, "error": "unexpected payload shape"})
            continue
        # The free tier answers an exhausted quota with prose in an
        # "Information" key and HTTP 200. Treated as an error, it is visible;
        # treated as an empty feed, the desk silently stops reporting sentiment.
        note = payload.get("Information") or payload.get("Note") or payload.get("Error Message")
        if note:
            failures.append(
                {"ticker": ticker, "error": mcp_client.redact(str(note))[:200], "quota": True}
            )
            continue
        for raw in payload.get("feed") or []:
            built = _av_item(raw, ticker, None, names)
            if built is not None:
                items.append(built)
    return items, failures, len(calls)


def av_time_from(moment: datetime) -> str:
    """Alpha Vantage's ``time_from`` format: ``YYYYMMDDTHHMM``, UTC."""
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")


def yahoo_requests(entry: TickerConfig) -> list[tuple[str, str, str | None, tuple[str, ...]]]:
    """Every Yahoo feed worth pulling for one watchlist entry.

    The entry's own symbol, then each declared competitor. Peer stories are
    filed under the *watched* ticker with ``peer_of`` naming the competitor,
    which is what puts "AMD launches a rival part" in NVDA's section.

    Competitor aliases are built from the peer symbol alone -- the config
    carries no company name for peers -- so a peer whose symbol is under three
    characters contributes nothing and is skipped rather than matching noise.
    """
    plan: list[tuple[str, str, str | None, tuple[str, ...]]] = [
        (entry.ticker, entry.ticker, None, aliases(entry.ticker, entry.company_name))
    ]
    for peer in entry.competitors:
        names = aliases(peer)
        if names:
            plan.append((peer, entry.ticker, peer, names))
    return plan
