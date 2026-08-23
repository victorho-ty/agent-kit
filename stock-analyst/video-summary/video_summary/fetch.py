# Adapted from news-radar/news_radar/fetch.py (2026-08-23).
# `head` is new; everything else is identical apart from USER_AGENT.
# Keep fixes in sync by hand.
"""Getting a document, politely.

Plain ``urllib`` -- these are ordinary public documents and an HTTP stack would
be a dependency the scheduler has to keep alive for no behaviour we need.

Two things here are worth more than they look:

**Conditional GET.** Every feed's ``ETag`` and ``Last-Modified`` are kept in
``feed_state`` and sent back on the next check. YouTube honours both, so a
channel that posts twice a week answers ``304 Not Modified`` to the other eighty
two-hourly checks, which costs YouTube a few hundred bytes and costs us no
parsing at all.

**A 4xx is not retried.** It means the url is wrong, and hammering it will not
make it right; it is a config error wearing a network error's clothes. A deleted
channel is exactly this, and it should show up as a feed failure the operator
can read, not as a slow retry loop.
"""

from __future__ import annotations

import dataclasses
import time as _time
import urllib.error
import urllib.request

from . import settings
from .errors import FetchError

USER_AGENT = "hermes-video-summary/0.1 (+personal video digest; two-hourly, conditional GET)"


@dataclasses.dataclass(frozen=True)
class Response:
    """One document, or the news that it has not changed."""

    url: str
    status: int
    text: str = ""
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def _decode(raw: bytes, charset: str | None) -> str:
    """Bytes to text, preferring the server's charset and never raising."""
    for candidate in (charset, "utf-8"):
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _request(url: str, *, etag: str | None, last_modified: str | None, method: str) -> urllib.request.Request:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml,application/xml;q=0.9,text/html;q=0.8,*/*;q=0.7",
        "Accept-Language": "en,zh-HK;q=0.9,zh;q=0.8",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return urllib.request.Request(url, headers=headers, method=method)


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
    request = _request(url, etag=etag, last_modified=last_modified, method="GET")

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
                raise FetchError(
                    f"GET {url} -> HTTP {exc.code} {exc.reason}", url=url, status=exc.code
                ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            _time.sleep(1.0 * (attempt + 1))

    raise FetchError(f"GET {url} failed after {attempts} attempt(s): {last_error}", url=url)


def resolved_url(url: str, *, timeout: float | None = None) -> str | None:
    """Where ``url`` ends up after redirects, or ``None`` if it could not be asked.

    Used for one thing only: telling a Short from an ordinary video. There is no
    field in the feed for it and no free API that answers it, but
    ``/shorts/<id>`` stays put for a Short and redirects to ``/watch?v=<id>`` for
    anything else, which is the whole test.

    Returns ``None`` rather than raising, because failing to label a video is
    not a reason to lose it.
    """
    timeout = settings.http_timeout() if timeout is None else timeout
    try:
        with urllib.request.urlopen(
            _request(url, etag=None, last_modified=None, method="HEAD"), timeout=timeout
        ) as response:
            return response.geturl()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
