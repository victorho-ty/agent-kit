"""Fetching dated 30-Day Fed Funds futures prices.

Only the *latest* price of each contract is read. Nothing here keeps a price
series -- the history this bundle builds is its own snapshots, in SQLite.

``yfinance`` is imported inside the function. It pulls in pandas and takes a
noticeable moment to load, and the maths, the history reads and the whole test
suite never fetch anything.

**The last one-minute print, not the daily bar.** Yahoo's daily ``Close`` and
the final print of its own one-minute series disagree for the same session --
96.2675 against 96.2700 for ZQU26 on 11 September 2026. That is 0.25bp, which
is about a percentage point of probability, so the intraday series is preferred
and the daily bar is only a fallback for when it is empty. A five-day window
survives a long weekend; only the final row is used.

Either way the price is a **traded print, not CME's settlement mid**, and that
is worth roughly another percentage point against CME's published table. Every
payload carries the source so it never travels unlabelled.

Yahoo serves CME futures on a delay. This is not a real-time feed, and Fed Funds
futures are thin outside US hours -- consecutive snaps overnight will legitimately
return the same price.
"""

from __future__ import annotations

from datetime import datetime

from .errors import FetchError
from .models import Quote

INTRADAY_SOURCE = "yfinance 1m last trade (not CME settlement mid)"
DAILY_SOURCE = "yfinance daily close (not CME settlement mid)"


def _latest(frame) -> tuple[float, datetime | None] | None:
    """The final row of a yfinance frame as (price, timestamp)."""
    if frame is None or frame.empty:
        return None
    stamp = frame.index[-1]
    return (
        float(frame["Close"].iloc[-1]),
        stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else None,
    )


def fetch(symbols: list[str]) -> tuple[dict[str, Quote], dict[str, str]]:
    """Quotes by symbol, plus a reason for each symbol that did not resolve.

    One missing contract never aborts the others. A delisted or not-yet-listed
    month is an ordinary outcome -- ZQ contracts vanish from Yahoo once they
    settle -- and the caller decides whether the ones that did arrive are
    enough.
    """
    import warnings

    import yfinance

    resolved: dict[str, Quote] = {}
    failures: dict[str, str] = {}

    for symbol in dict.fromkeys(symbols):
        ticker = yfinance.Ticker(symbol)
        latest, source = None, INTRADAY_SOURCE
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                latest = _latest(ticker.history(period="5d", interval="1m"))
                if latest is None:
                    latest, source = _latest(ticker.history(period="5d")), DAILY_SOURCE
        except Exception as exc:  # yfinance raises bare Exception on a bad symbol
            failures[symbol] = f"{type(exc).__name__}: {exc}"
            continue

        if latest is None:
            failures[symbol] = "no data (contract not listed, or already settled)"
            continue

        price, as_of = latest
        resolved[symbol] = Quote(symbol=symbol, price=price, as_of=as_of, source=source)

    if not resolved:
        raise FetchError(
            "no Fed Funds futures contract could be read",
            symbols=list(symbols),
            failures=failures,
        )
    return resolved, failures


def as_of_label(quote: Quote) -> str | None:
    return quote.as_of.isoformat() if isinstance(quote.as_of, datetime) else None


def describe(used: list[Quote]) -> str:
    """One line naming the price source actually used across a snapshot.

    Named per contract rather than assumed, because a fallback to the daily bar
    for one month and not another is exactly the kind of difference that should
    not disappear into a constant.
    """
    fallbacks = sorted(q.symbol for q in used if q.source == DAILY_SOURCE)
    if not fallbacks:
        return INTRADAY_SOURCE
    if len(fallbacks) == len(used):
        return DAILY_SOURCE
    return f"{INTRADAY_SOURCE}; daily close fallback for {', '.join(fallbacks)}"
