"""Market data providers: yfinance primary, Stooq as the bar-only fallback."""

from __future__ import annotations

from . import stooq_provider, yfinance_provider

PRIMARY = yfinance_provider
FALLBACK = stooq_provider

__all__ = ["PRIMARY", "FALLBACK", "yfinance_provider", "stooq_provider"]
