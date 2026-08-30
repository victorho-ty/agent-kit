"""One HTTP GET per estate, and the one query parameter that matters.

Centanet's list renders 24 records by default and honours ``size``. It caps it
at exactly 100: ask for 101 and the response is still HTTP 200, still a valid
page, and its ``transactionList`` is an empty object. That silent failure is why
:func:`hk_transaction_tracker.settings.fetch_size` clamps rather than trusts, and
why :mod:`hk_transaction_tracker.extract` treats an empty ``transactionList`` as
ERR_PARSE instead of "no transactions".

No offset, page, skip, start, pageIndex or currentPage parameter is honoured --
all of them come back at offset 0 -- so the newest hundred is the entire visible
window and there is no way to page behind it. The archive in SQLite is the only
thing that remembers further back than that.

``urllib`` rather than ``requests``: this is one GET with one header, and the
whole test suite runs without a network stub library.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from . import settings
from .errors import FetchError

# Centanet serves the list to an unknown agent, but a default Python UA gets a
# challenge page rather than the payload. This is the browser string the page
# is built for; nothing here evades a rate limit or a paywall.
_HEADERS = {
    "User-Agent": settings.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.7",
}


def with_size(url: str, size: int) -> str:
    """``url`` asking for ``size`` records.

    Appended textually rather than through ``parse_qsl``/``urlencode``: the
    ``q=8prsheylr1o5h`` on a Centanet URL is an opaque saved-search key, and a
    round trip through a query encoder is a needless chance to change it. A URL
    that already names a size is left alone, so an operator can pin one.
    """
    if "size=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}size={size}"


def get(url: str, *, timeout: float | None = None, retries: int | None = None) -> str:
    """The page as text, or :class:`FetchError` after the retries are spent."""
    timeout = settings.http_timeout() if timeout is None else timeout
    retries = settings.http_retries() if retries is None else retries

    attempts = max(1, retries + 1)
    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            charset = "utf-8"
            return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last = exc
            # 4xx will say the same thing next time; only a 5xx is worth a retry.
            if exc.code < 500:
                raise FetchError(
                    f"HTTP {exc.code} from {url}", url=url, status=exc.code,
                ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(min(2.0 * (attempt + 1), 5.0))

    raise FetchError(
        f"could not retrieve {url}: {last}", url=url, attempts=attempts, reason=str(last),
    )


def page(url: str, *, size: int | None = None, **kwargs) -> str:
    """The list page for one estate, asking for as many records as it will give."""
    return get(with_size(url, settings.fetch_size() if size is None else size), **kwargs)
