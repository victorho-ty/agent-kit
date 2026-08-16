"""The fallback: Stooq daily bars over plain CSV.

Exists for one failure mode, which is the likely one: yfinance stops returning
bars after an upstream change, and every part of the desk that matters -- the
setup scan, the charts, the position marks -- goes dark at once. Fundamentals
and the earnings calendar can wait a day; prices cannot.

No dependency and no API key: a CSV over ``urllib``. Coverage is good for US
listings and poor outside them, which is stated rather than hidden -- an HK
ticker raises :class:`FetchError` rather than silently returning nothing, so a
caller does not mistake "unsupported" for "no new bars".
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from datetime import date

from .. import settings
from ..errors import FetchError
from ..markets import market_label
from ..models import Bar

name = "stooq"

BASE_URL = "https://stooq.com/q/d/l/"


def _symbol(ticker: str) -> str:
    """Stooq spells US tickers ``nvda.us``. Dots inside a symbol become dashes."""
    market = market_label(ticker)
    if market != "US":
        raise FetchError(
            f"stooq has no reliable coverage for {market} listings",
            ticker=ticker,
            market=market,
        )
    return f"{ticker.strip().lower().replace('.', '-')}.us"


def daily_bars(ticker: str, start: date | None = None) -> list[Bar]:
    url = f"{BASE_URL}?s={_symbol(ticker)}&i=d"
    request = urllib.request.Request(url, headers={"User-Agent": "hermes-stock-desk/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=settings.http_timeout()) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"stooq could not be reached for {ticker}", ticker=ticker) from exc

    # Stooq answers an unknown symbol with a 200 and the body "No data".
    if not payload or payload.lstrip().lower().startswith("no data"):
        raise FetchError(f"stooq returned no data for {ticker}", ticker=ticker)

    bars: list[Bar] = []
    for row in csv.DictReader(io.StringIO(payload)):
        try:
            day = date.fromisoformat(row["Date"])
            if start and day < start:
                continue
            bars.append(
                Bar(
                    day=day,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume") or 0.0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # one malformed row must not lose the other five hundred

    bars.sort(key=lambda bar: bar.day)
    return bars
