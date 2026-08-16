"""The provider contract.

Two implementations exist: :mod:`yfinance_provider` for everything, and
:mod:`stooq_provider` for daily bars only, as a fallback when the first breaks.
Both are scrapers of a sort, and the interface exists so that a keyed vendor can
be dropped in later without touching a caller.

Everything returns plain models and raises :class:`FetchError` on failure. A
provider never writes to the database, never retries on its own schedule, and
never logs.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from ..models import Bar, CorporateEvent, Fundamentals, NewsItem


@runtime_checkable
class Provider(Protocol):
    name: str

    def daily_bars(self, ticker: str, start: date | None = None) -> list[Bar]:
        """Ascending daily OHLCV from ``start`` (inclusive) to the latest close."""
        ...


@runtime_checkable
class FullProvider(Provider, Protocol):
    """A provider that also answers the questions bars cannot."""

    def fundamentals(self, ticker: str) -> Fundamentals | None: ...

    def corporate_events(self, ticker: str) -> list[CorporateEvent]: ...

    def news(self, ticker: str, limit: int = 20) -> list[NewsItem]: ...
