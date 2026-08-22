"""Reaching the Housing Bureau: the index page, and the PDF it points at.

The index page is 3KB of static markup carrying exactly one live link --
``stat<YYYYMM>.pdf`` -- under the heading 私人住宅一手市場供應, plus a link to an
archive page for older quarters. That is small enough that ``urllib`` and a
regex do the whole job, and it keeps the network surface to two GETs a day: one
3KB page, and a 500KB PDF only in the quarter it changes.

Two decisions worth knowing about:

**The newest link wins, not the first one.** The page currently lists one PDF,
but the ordering of a page nobody controls is not a contract, whereas
``stat202606`` sorting after ``stat202603`` is arithmetic.

**The page's own wording is a cross-check, not decoration.** The anchor reads
"2026年6月" next to ``stat202606.pdf``. When both are present and disagree, this
refuses to go further rather than filing a quarter's figures under the wrong
label -- a wrong quarter is far more expensive than a missed one, because it is
believed.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request

from . import settings
from .errors import FetchError, ParseError
from .history import quarter_label
from .models import Publication

# A browser string. The site serves this page to anything, but a default
# `Python-urllib/3.12` is the first thing a WAF drops, and being asked to
# diagnose that once is enough.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

SECTION_HEADING = "私人住宅一手市場供應"
PDF_HREF_RE = re.compile(r"stat(\d{4})(\d{2})\.pdf", re.IGNORECASE)
# The visible label beside the link: "2026年6月".
LABEL_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
# How far past an anchor to look for its own label. The anchor text follows the
# href within the same <a> element, so this only has to span one tag.
LABEL_WINDOW = 240


def _get(url: str) -> bytes:
    """One GET, with retries on transport failure only.

    A 4xx is never retried: it means the URL is wrong, and repeating it will not
    make it right.
    """
    attempts = settings.http_retries() + 1
    timeout = settings.http_timeout()
    last: Exception | None = None

    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise FetchError(
                    f"{url} returned HTTP {exc.code}",
                    url=url, status=exc.code,
                ) from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))

    raise FetchError(f"could not reach {url}: {last}", url=url, attempts=attempts)


def fetch_index(url: str | None = None) -> str:
    """The index page as text. Served UTF-8 with a BOM, hence ``utf-8-sig``."""
    target = url or settings.index_url()
    return _get(target).decode("utf-8-sig", errors="replace")


def find_publication(html: str, *, base_url: str | None = None) -> Publication:
    """The newest ``stat<YYYYMM>.pdf`` on the page, with its quarter parsed out.

    Anchored to the supply section when the heading is present, so a link added
    to some other part of the page cannot be mistaken for this series. Falls back
    to the whole document when the heading has been reworded, because a
    reworded heading is not a reason to stop reporting.
    """
    start = html.find(SECTION_HEADING)
    region_offset = start if start >= 0 else 0
    region = html[region_offset:]

    candidates = [
        (int(match.group(1)), int(match.group(2)), match)
        for match in PDF_HREF_RE.finditer(region)
    ]
    if not candidates:
        raise ParseError(
            "no 'stat<YYYYMM>.pdf' link found on the index page",
            heading_found=start >= 0,
            remedy="open the index page; the publication link has moved or been renamed",
        )

    year, month, match = max(candidates, key=lambda item: (item[0], item[1]))
    href = match.group(0)

    if month not in (3, 6, 9, 12):
        raise ParseError(
            f"{href} is not a quarter-end month",
            href=href, month=month,
            remedy="the series has changed cadence; the quarter labels need revisiting",
        )

    label = None
    window = region[match.end():match.end() + LABEL_WINDOW]
    found = LABEL_RE.search(window)
    if found is not None:
        label = f"{int(found.group(1))}年{int(found.group(2))}月"
        if (int(found.group(1)), int(found.group(2))) != (year, month):
            raise ParseError(
                f"the page says {label} beside {href}",
                href=href, page_label=label, filename_quarter=quarter_label(year, month),
                remedy="the filename and the printed date disagree; read the page before trusting either",
            )

    base = base_url if base_url is not None else settings.pdf_base_url()
    return Publication(
        href=href,
        url=base + href,
        quarter=quarter_label(year, month),
        year=year,
        month=month,
        label=label,
    )


def latest_publication(url: str | None = None) -> Publication:
    """One GET, one answer: what the Housing Bureau is publishing right now."""
    return find_publication(fetch_index(url))


def download_pdf(publication: Publication) -> bytes:
    data = _get(publication.url)
    if not data.startswith(b"%PDF"):
        raise ParseError(
            f"{publication.url} did not return a PDF",
            url=publication.url,
            first_bytes=data[:16].decode("latin-1", "replace"),
        )
    return data
