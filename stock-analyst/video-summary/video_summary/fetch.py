# Adapted from news-radar/news_radar/fetch.py (2026-08-23).
# `head` is new; so is the retry policy below (news-radar still fails fast on
# a 4xx, and its feeds have not given us a reason to change that). Otherwise
# identical apart from USER_AGENT. Keep fixes in sync by hand.
"""Getting a document, politely.

Plain ``urllib`` -- these are ordinary public documents and an HTTP stack would
be a dependency the scheduler has to keep alive for no behaviour we need.

Two things here are worth more than they look:

**Conditional GET.** Every feed's ``ETag`` and ``Last-Modified`` are kept in
``feed_state`` and sent back on the next check. YouTube honours both, so a
channel that posts twice a week answers ``304 Not Modified`` to the other eighty
two-hourly checks, which costs YouTube a few hundred bytes and costs us no
parsing at all.

**Every HTTP error is retried, 404 included.** This used to be the opposite --
a 4xx was treated as a config error wearing a network error's clothes and failed
on the spot. YouTube disproved it: ``/feeds/videos.xml?channel_id=...`` answers
404 for channels that plainly exist, in bursts, and the same url a few seconds
later returns the feed. A url that is genuinely wrong still fails, just one
backoff ladder later, and it still arrives as a readable feed failure carrying
the last status. Paying ~30 seconds on a dead channel is the cheaper mistake
than dropping a live one's videos.

The ladder is exponential with jitter, and a ``Retry-After`` on a 429 or 503 is
honoured over it -- when the server says how long to wait, arguing is rude.
"""

from __future__ import annotations

import dataclasses
import email.utils
import random
import time as _time
import urllib.error
import urllib.request

from . import settings
from .errors import FetchError

USER_AGENT = "hermes-video-summary/0.1 (+personal video digest; two-hourly, conditional GET)"

# The backoff ladder: 1s, 2s, 4s ... capped, plus up to 25% jitter so ten feeds
# that all tripped over the same hiccup do not retry in lockstep.
BACKOFF_BASE = 1.0
BACKOFF_CAP = 30.0


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


def _backoff(attempt: int) -> float:
    """Seconds to wait after a failed ``attempt`` (0-based), with jitter."""
    delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** attempt))
    return delay + random.uniform(0.0, delay * 0.25)


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    """The server's own answer to "how long?", in seconds, or ``None``.

    Accepts both spellings -- a bare number of seconds and an HTTP date -- and
    is clamped to ``BACKOFF_CAP``, so a server asking for an hour cannot park a
    two-hourly check for one.
    """
    raw = ((exc.headers.get("Retry-After") if exc.headers else None) or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        seconds = parsed.timestamp() - _time.time()
    return max(0.0, min(BACKOFF_CAP, seconds))


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
    last_status: int | None = None
    for attempt in range(attempts):
        wait: float | None = None
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
            last_error, last_status = exc, exc.code
            wait = _retry_after(exc)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error, last_status = exc, None
        if attempt + 1 < attempts:
            _time.sleep(_backoff(attempt) if wait is None else wait)

    detail = f"HTTP {last_status}" if last_status is not None else str(last_error)
    raise FetchError(
        f"GET {url} -> {detail}, failed after {attempts} attempt(s)",
        url=url,
        status=last_status,
    )


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
