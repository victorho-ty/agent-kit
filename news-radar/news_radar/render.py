# Copied verbatim from education-radar/education_radar/render.py (2026-08-11).
# It carries no news- or education-specific content; keep fixes in sync by hand.
"""The browser path, for sites that build their listings in JavaScript.

Reached only for ``"render": "browser"``, and imported lazily, so a static-only
scan, ``--dry-run`` and the whole test suite never need Chromium. Conditional
GET does not apply here -- there is no ETag to send -- so a browser site is
re-read on every scan and its dedupe rests entirely on ``item_key``. That is the
real cost of the mode, and the reason it is opt-in per site rather than the
default.

One context is opened per scan and shared by every browser site in it, because
starting Chromium is much more expensive than loading a page in one already
running.
"""

from __future__ import annotations

from .errors import BrowserError
from .fetch import USER_AGENT, Response

# Give the page a moment past "load" to finish whatever it fetches for its own
# listings; a hard wait is cruder than a selector wait but works without knowing
# each site's markup, which is exactly what the config is allowed to change.
SETTLE_MS = 1500


class Browser:
    """A Chromium context, opened on first use and closed with the scan.

    Used as a context manager; entering it does *not* start a browser, so a scan
    whose sites all turn out to be static or unchanged pays nothing.
    """

    def __init__(self, *, headless: bool = True, timeout: float = 20.0):
        self.headless = headless
        self.timeout = timeout
        self._playwright = None
        self._browser = None

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _start(self):
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError(
                "this site needs \"render\": \"browser\" but playwright is not installed in this venv; "
                "run: uv sync && uv run playwright install chromium"
            ) from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
        except Exception as exc:  # playwright raises its own Error type
            raise BrowserError(
                f"could not start Chromium: {exc}. The playwright package does not ship the browser; "
                "run: uv run playwright install chromium"
            ) from exc

    def get(self, url: str) -> Response:
        """Load ``url`` and return its rendered HTML."""
        self._start()
        page = self._browser.new_page(user_agent=USER_AGENT)
        try:
            page.set_default_timeout(self.timeout * 1000)
            page.goto(url, wait_until="load")
            page.wait_for_timeout(SETTLE_MS)
            html = page.content()
        except Exception as exc:
            raise BrowserError(f"rendering {url} failed: {exc}", url=url) from exc
        finally:
            page.close()
        return Response(url=url, status=200, text=html)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
