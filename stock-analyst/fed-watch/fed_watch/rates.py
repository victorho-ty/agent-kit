"""Where policy is right now, from the New York Fed.

One keyless public endpoint carries both things the calculation needs: the
published effective federal funds rate, and the target range it sits inside.
Neither is guessed and neither is configuration -- a target range typed into a
file goes stale on the afternoon it matters most.

The effective rate is not the midpoint of the range and must not be replaced by
it; ``probabilities`` explains what that substitution costs.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date

from . import settings
from .errors import FetchError
from .models import PolicyRate

ENDPOINT = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
USER_AGENT = "hermes-fed-watch/0.1 (+personal FOMC probability tracker)"


def _get(url: str, timeout: float, retries: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # A 4xx means the endpoint moved or the shape changed. Retrying will
            # not make it right, and it is a config error wearing a network
            # error's clothes.
            if exc.code < 500:
                raise FetchError(
                    f"GET {url} -> HTTP {exc.code} {exc.reason}", url=url, status=exc.code
                ) from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(1.0 * (attempt + 1))
    raise FetchError(f"GET {url} failed after {retries + 1} attempt(s): {last_error}", url=url)


def current(url: str = ENDPOINT) -> PolicyRate:
    """The most recent EFFR print and the target range around it."""
    body = _get(url, settings.http_timeout(), settings.http_retries())
    try:
        rows = json.loads(body).get("refRates") or []
        row = rows[0]
        return PolicyRate(
            effr=float(row["percentRate"]),
            as_of=date.fromisoformat(row["effectiveDate"]),
            target_low=float(row["targetRateFrom"]),
            target_high=float(row["targetRateTo"]),
        )
    except (json.JSONDecodeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise FetchError(
            f"the New York Fed rate feed did not have the expected shape: {exc}", url=url
        ) from exc
