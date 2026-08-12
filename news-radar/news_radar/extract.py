# Copied from education-radar/education_radar/extract.py (2026-08-11).
# Mechanically renamed site -> source; the logic is untouched. Keep fixes in sync.
"""Turning a page into candidate items.

Three kinds, in the order you should reach for them:

``rss``    the source publishes a feed. Take it. Nearly every news outlet does,
           a feed cannot be broken by a redesign, and it dates its own entries.
           For this skill it should be the overwhelming default.
``html``   ``list_selector`` names the repeated element, ``fields`` name what to
           pull out of each one. Only for outlets with no feed.
``regex``  the escape hatch, for a page whose items are not a repeated element
           that any selector can name.

Everything here is a pure function of (source, page text). No network, no clock,
no database -- which is what lets ``scan --dry-run`` show exactly what the
selectors caught, and lets the tests pin extraction against captured pages.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ElementTree
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .errors import ConfigError
from .models import Candidate

# Query parameters that identify the click, not the page. Two links differing
# only in these are the same item, and keeping them would mean re-reporting the
# whole feed the day an outlet starts tagging its own newsletter. Stripping them
# also lets cluster.py recognise syndication by exact URL match.
TRACKING_PARAMS = ("fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src")
TRACKING_PREFIXES = ("utm_",)

# A detail page is read for its age line, not stored as an archive.
DETAIL_TEXT_LIMIT = 4000

_WHITESPACE = re.compile(r"\s+")
# Flattening markup leaves a space wherever a tag was, including in front of the
# punctuation that followed it: "<strong>P4-P6</strong>." reads as "P4-P6 .".
# These summaries are read by a person in a chat message, so close the gap.
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?)\]}%）」』，。；：！？])")


def canonical_url(url: str, base: str | None = None) -> str:
    """Absolute, fragment-free, and stripped of click tracking."""
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in TRACKING_PARAMS and not key.startswith(TRACKING_PREFIXES)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _clean(value: str | None) -> str | None:
    """Flatten whitespace, close the gap before punctuation, and decode entities.

    The unescape matters more than it looks. Feeds routinely double-escape, so
    the XML parser hands back a title still containing a literal ``&#8217;``
    where an apostrophe belongs. Left alone that reaches the reader as mojibake,
    and worse, it changes the token set the clusterer works from -- the same
    story from a tidy feed and a double-escaped one would no longer look alike.
    """
    if value is None:
        return None
    flat = _WHITESPACE.sub(" ", html.unescape(value))
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", flat).strip() or None


def soup(html: str) -> BeautifulSoup:
    """Parse with the standard library's parser -- no lxml, no html5lib."""
    return BeautifulSoup(html, "html.parser")


def _field(element, selector: str) -> str | None:
    """One field out of one listing element.

    ``a.title`` takes the element's text; ``a.title@href`` takes an attribute.
    An empty selector means the listing element itself, which is how a bare
    ``<li>Title</li>`` list is read.
    """
    selector, _, attribute = selector.partition("@")
    selector = selector.strip()
    target = element
    if selector:
        try:
            target = element.select_one(selector)
        except Exception as exc:  # soupsieve raises on a malformed selector
            raise ConfigError(f"bad selector {selector!r}: {exc}") from exc
        if target is None:
            return None
    if attribute:
        value = target.get(attribute)
        if isinstance(value, list):  # e.g. class="a b"
            value = " ".join(value)
        return _clean(value)
    return _clean(target.get_text(" ", strip=True))


def _extract_html(source, html: str, base_url: str) -> list[Candidate]:
    document = soup(html)
    try:
        elements = document.select(source.list_selector)
    except Exception as exc:
        raise ConfigError(f"source {source.name!r}: bad list_selector {source.list_selector!r}: {exc}") from exc

    candidates: list[Candidate] = []
    for element in elements[: source.max_items]:
        title = _field(element, source.fields.get("title", ""))
        if not title:
            continue
        link = _field(element, source.fields["link"]) if "link" in source.fields else None
        if not link:
            anchor = element.select_one("a[href]")
            link = anchor.get("href") if anchor else None
        candidates.append(Candidate(
            source=source.name,
            title=title,
            url=canonical_url(link, base_url) if link else canonical_url(base_url),
            summary=_field(element, source.fields["summary"]) if "summary" in source.fields else None,
            date_text=_field(element, source.fields["date"]) if "date" in source.fields else None,
        ))
    return candidates


def _tag(element) -> str:
    """Local tag name, with any namespace dropped."""
    return element.tag.rsplit("}", 1)[-1].lower()


def _find_text(entry, names: tuple[str, ...]) -> str | None:
    for child in entry:
        if _tag(child) in names:
            return _clean("".join(child.itertext()))
    return None


def _find_link(entry) -> str | None:
    for child in entry:
        if _tag(child) != "link":
            continue
        return _clean(child.get("href") or "".join(child.itertext()))
    return None


def _extract_rss(source, xml_text: str, base_url: str) -> list[Candidate]:
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError as exc:
        raise ConfigError(f"source {source.name!r}: kind is 'rss' but the response is not XML: {exc}") from exc

    entries = [node for node in root.iter() if _tag(node) in ("item", "entry")]
    candidates: list[Candidate] = []
    for entry in entries[: source.max_items]:
        title = _find_text(entry, ("title",))
        if not title:
            continue
        link = _find_link(entry)
        summary = _find_text(entry, ("description", "summary", "content", "encoded"))
        if summary and "<" in summary:
            summary = _clean(soup(summary).get_text(" ", strip=True))
        candidates.append(Candidate(
            source=source.name,
            title=title,
            url=canonical_url(link, base_url) if link else canonical_url(base_url),
            summary=summary,
            date_text=_find_text(entry, ("pubdate", "published", "updated", "date")),
        ))
    return candidates


def _extract_regex(source, text: str, base_url: str) -> list[Candidate]:
    try:
        pattern = re.compile(source.item_pattern, re.IGNORECASE | re.DOTALL)
    except re.error as exc:
        raise ConfigError(f"source {source.name!r}: bad item_pattern: {exc}") from exc
    if "title" not in (pattern.groupindex or {}):
        raise ConfigError(f"source {source.name!r}: item_pattern needs a named group (?P<title>...)")

    candidates: list[Candidate] = []
    for match in list(pattern.finditer(text))[: source.max_items]:
        groups = match.groupdict()
        title = _clean(groups.get("title"))
        if not title:
            continue
        link = _clean(groups.get("link"))
        candidates.append(Candidate(
            source=source.name,
            title=title,
            url=canonical_url(link, base_url) if link else canonical_url(base_url),
            summary=_clean(groups.get("summary")),
            date_text=_clean(groups.get("date")),
        ))
    return candidates


def extract(source, text: str, base_url: str | None = None) -> list[Candidate]:
    """Candidates from one page, capped at the source's ``max_items``."""
    base_url = base_url or source.url
    if source.kind == "rss":
        return _extract_rss(source, text, base_url)
    if source.kind == "regex":
        return _extract_regex(source, text, base_url)
    return _extract_html(source, text, base_url)


def detail_text(html: str, selector: str | None = None) -> str:
    """The readable text of a listing's own page, for the age line it may carry.

    Scripts, styles and navigation chrome are dropped; the rest is flattened and
    truncated. This is fed to the matcher and stored for the review queue, so it
    is text a person would read, never markup.
    """
    document = soup(html)
    for node in document(["script", "style", "noscript", "nav", "header", "footer"]):
        node.decompose()
    root = document
    if selector:
        chosen = document.select_one(selector)
        if chosen is not None:
            root = chosen
    flat = _WHITESPACE.sub(" ", root.get_text(" ", strip=True))
    return flat[:DETAIL_TEXT_LIMIT]
