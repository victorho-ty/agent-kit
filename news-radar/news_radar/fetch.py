# Copied from education-radar/education_radar/fetch.py (2026-08-11).
# Identical apart from USER_AGENT below; keep fixes in sync by hand.
"""Getting a page, politely.

Plain ``urllib`` -- these are ordinary public pages and an HTTP stack would be a
dependency the scheduler has to keep alive for no behaviour we need.

Two things here are worth more than they look:

**Conditional GET.** Every source's ``ETag`` and ``Last-Modified`` are kept in
``site_state`` and sent back on the next scan. This is what makes continuous
scanning affordable: a feed that publishes twice a day answers ``304 Not
Modified`` to the other twenty-two hourly scans, which costs the publisher a few
hundred bytes and costs us no parsing at all. It is also the honest answer to
"did anything change" -- far better than diffing text we scraped.

**A 4xx is not retried.** It means the URL is wrong, and hammering it will not
make it right; it is a config error wearing a network error's clothes.
"""

from __future__ import annotations

import dataclasses
import time as _time
import urllib.error
import urllib.request

from . import settings
from .errors import FetchError

USER_AGENT = "hermes-news-radar/0.1 (+personal news digest; hourly, conditional GET)"


@dataclasses.dataclass(frozen=True)
class Response:
    """One page, or the news that it has not changed."""

    url: str
    status: int
    text: str = ""
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def _decode(raw: bytes, charset: str | None) -> str:
    """Bytes to text, preferring the server's charset and never raising.

    A mis-declared charset is common on older sites and must not take down a
    scan: a replacement character in one listing's summary is a far smaller
    problem than a run that reports nothing.
    """
    for candidate in (charset, "utf-8", "big5", "gb18030"):
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def get(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> Response:
    """GET ``url``, returning a 304 ``Response`` when it is unchanged."""
    timeout = settings.http_timeout() if timeout is None else timeout
    attempts = (settings.http_retries() if retries is None else retries) + 1

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en,zh-HK;q=0.9,zh;q=0.8",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(url, headers=headers)

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return Response(
                    url=response.geturl(),
                    status=response.status,
                    text=_decode(raw, response.headers.get_content_charset()),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return Response(url=url, status=304, etag=etag, last_modified=last_modified)
            last_error = exc
            if exc.code < 500:
                raise FetchError(f"GET {url} -> HTTP {exc.code} {exc.reason}", url=url, status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            _time.sleep(1.0 * (attempt + 1))

    raise FetchError(f"GET {url} failed after {attempts} attempt(s): {last_error}", url=url)
