"""The primary provider: yfinance.

One unauthenticated library answers every question this bundle asks -- daily
OHLCV for US and HK listings, the 52-week range, trailing and forward P/E, the
next earnings date, the next ex-dividend date, and per-ticker news.

It is a scraper of an undocumented endpoint, so it breaks. Three defences:

* every call is wrapped and re-raised as :class:`FetchError`, so a caller sees
  one failure type rather than whatever yfinance leaked this month;
* field lookups go through :func:`_first`, because the key names in ``info``
  and ``calendar`` have moved more than once between releases;
* daily bars have a second source. :mod:`stooq_provider` covers the one thing
  whose absence stops the whole desk working.

Nothing here caches. :mod:`bars` owns the database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from ..errors import FetchError
from ..models import Bar, CorporateEvent, Fundamentals, NewsItem

name = "yfinance"


def _yf():
    try:
        import yfinance
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        raise FetchError(
            "yfinance is not installed in this environment",
            remedy="uv sync in the bundle root",
        ) from exc
    return yfinance


def _first(payload: dict, *keys: str) -> Any:
    """First present, non-null value among ``keys``.

    Yahoo renames fields between releases -- ``trailingPE`` has also appeared as
    ``trailing_pe``, and the calendar's keys change shape entirely. Guessing one
    name and getting ``None`` looks exactly like a company with no earnings.
    """
    for key in keys:
        value = payload.get(key)
        if value is not None and value != {} and value != []:
            return value
    return None


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    if isinstance(value, Iterable):
        for item in value:  # calendar sometimes yields a list of upcoming dates
            parsed = _as_date(item)
            if parsed:
                return parsed
    return None


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # drop NaN


def daily_bars(ticker: str, start: date | None = None) -> list[Bar]:
    """Ascending daily bars. ``start`` inclusive; defaults to two years back."""
    yfinance = _yf()
    begin = start or (date.today() - timedelta(days=730))
    try:
        frame = yfinance.Ticker(ticker).history(
            start=begin.isoformat(), interval="1d", auto_adjust=False, actions=False
        )
    except Exception as exc:
        raise FetchError(f"yfinance could not fetch bars for {ticker}", ticker=ticker) from exc

    if frame is None or frame.empty:
        return []

    bars: list[Bar] = []
    for index, row in frame.iterrows():
        day = index.date() if hasattr(index, "date") else _as_date(index)
        close = _as_float(row.get("Close"))
        if day is None or close is None:
            continue
        high = _as_float(row.get("High"))
        low = _as_float(row.get("Low"))
        opening = _as_float(row.get("Open"))
        volume = _as_float(row.get("Volume"))
        if None in (high, low, opening):
            continue
        bars.append(
            Bar(
                day=day,
                open=opening,
                high=high,
                low=low,
                close=close,
                volume=volume or 0.0,
                adj_close=_as_float(row.get("Adj Close")),
            )
        )
    bars.sort(key=lambda bar: bar.day)
    return bars


def fundamentals(ticker: str) -> Fundamentals | None:
    yfinance = _yf()
    try:
        info = yfinance.Ticker(ticker).info or {}
    except Exception as exc:
        raise FetchError(f"yfinance could not fetch fundamentals for {ticker}", ticker=ticker) from exc
    if not info:
        return None
    return Fundamentals(
        ticker=ticker,
        as_of=date.today(),
        pe=_as_float(_first(info, "trailingPE", "trailing_pe")),
        forward_pe=_as_float(_first(info, "forwardPE", "forward_pe")),
        market_cap=_as_float(_first(info, "marketCap", "market_cap")),
        beta=_as_float(_first(info, "beta", "beta3Year")),
        sector=_first(info, "sector"),
        industry=_first(info, "industry"),
        currency=_first(info, "currency", "financialCurrency"),
    )


def corporate_events(ticker: str) -> list[CorporateEvent]:
    """Next earnings and next ex-dividend, when the vendor knows them.

    Both are *estimates* until a company confirms. The report says so; this
    function only reports what the vendor published.
    """
    yfinance = _yf()
    try:
        calendar = yfinance.Ticker(ticker).calendar or {}
    except Exception as exc:
        raise FetchError(f"yfinance could not fetch the calendar for {ticker}", ticker=ticker) from exc

    if hasattr(calendar, "to_dict"):  # older releases hand back a DataFrame
        try:
            calendar = calendar.to_dict()
        except Exception:
            return []
    if not isinstance(calendar, dict):
        return []

    today = date.today()
    events: list[CorporateEvent] = []

    earnings = _as_date(_first(calendar, "Earnings Date", "earningsDate", "Earnings High"))
    if earnings:
        events.append(
            CorporateEvent(
                ticker=ticker,
                kind="earnings",
                event_date=earnings,
                days_away=(earnings - today).days,
                detail="vendor estimate unless the company has confirmed",
            )
        )

    ex_div = _as_date(_first(calendar, "Ex-Dividend Date", "exDividendDate"))
    if ex_div:
        events.append(
            CorporateEvent(
                ticker=ticker,
                kind="ex_dividend",
                event_date=ex_div,
                days_away=(ex_div - today).days,
            )
        )
    return events


def news(ticker: str, limit: int = 20) -> list[NewsItem]:
    """Yahoo's own per-ticker feed. Complements the Google News RSS in
    :mod:`stock_desk.news`; overlap is expected and deduped downstream."""
    yfinance = _yf()
    try:
        articles = yfinance.Ticker(ticker).news or []
    except Exception as exc:
        raise FetchError(f"yfinance could not fetch news for {ticker}", ticker=ticker) from exc

    items: list[NewsItem] = []
    for article in articles[:limit]:
        # Recent yfinance nests the article under "content"; older is flat.
        body = article.get("content") if isinstance(article.get("content"), dict) else article
        title = _first(body, "title", "headline")
        url = _first(body, "link", "canonicalUrl", "clickThroughUrl")
        if isinstance(url, dict):
            url = _first(url, "url")
        if not title or not url:
            continue
        published = _as_date(_first(body, "providerPublishTime", "pubDate", "displayTime"))
        provider = _first(body, "provider", "publisher")
        if isinstance(provider, dict):
            provider = _first(provider, "displayName", "name")
        items.append(
            NewsItem(
                ticker=ticker,
                title=str(title),
                url=str(url),
                source=str(provider or "yahoo"),
                published_at=published.isoformat() if published else None,
            )
        )
    return items
