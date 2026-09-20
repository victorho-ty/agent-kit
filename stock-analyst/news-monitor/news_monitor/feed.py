"""Turning a feed document into entries, whatever dialect it is written in.

Three formats reach this bundle and the agent must never care which:

* **RSS 2.0** -- ``channel/item``, no namespace. The Fed, Dow Jones and CNBC all
  serve this.
* **RSS 1.0 / RDF** -- ``rdf:RDF/item``, everything namespaced.
* **Atom** -- ``feed/entry``, and the link is an attribute rather than text.

Rather than three parsers, everything here matches on the element's *local*
name and ignores its namespace, which collapses the three into one path. The
only genuine difference left is where a link lives, and that is four lines.

**Identity is the article url, and it is global.** A story carried by both
MarketWatch top-stories and the Dow Jones markets wire is one story, and handing
it over twice would be a bug rather than a feature -- so the canonical url is
the fingerprint and the second feed to see it simply does not re-insert. Where a
feed gives no usable link the fingerprint falls back to its ``guid``, then to a
hash of the title, both namespaced to the feed because neither is unique
anywhere else.
"""

from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .errors import FetchError
from .models import Entry

ITEM_TAGS = ("item", "entry")
TITLE_TAGS = ("title",)
SUMMARY_TAGS = ("description", "summary", "encoded", "content", "subtitle")
DATE_TAGS = ("pubdate", "published", "updated", "date", "issued")
ID_TAGS = ("guid", "id", "identifier")

# Query parameters that identify the click, not the page. Two links differing
# only in these are the same article, and keeping them would mean re-reporting a
# whole wire the day its publisher starts tagging its own newsletter.
TRACKING_PARAMS = ("fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "__source")
TRACKING_PREFIXES = ("utm_",)

_WHITESPACE = re.compile(r"\s+")
# Flattening markup leaves a space where the tag was, including in front of the
# punctuation that followed it: "<b>Q3</b>." reads as "Q3 .".
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?)\]}%])")


def local(tag: str) -> str:
    """``{http://purl.org/rss/1.0/}item`` -> ``item``, lowercased."""
    return tag.rsplit("}", 1)[-1].lower()


class _TextOnly(HTMLParser):
    """Markup to text, for feed summaries that arrive as HTML.

    Feeds put anything in a ``<description>``: a paragraph, a paragraph wrapped
    in ``<p>``, or a whole teaser card with an image and a read-more link. The
    agent is going to write two lines from it, so all that survives is the text.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("p", "br", "div", "li"):
            self.parts.append(" ")


def clean(value: str | None) -> str | None:
    """Flatten whitespace, close the gap before punctuation, decode entities.

    The unescape matters more than it looks: feeds routinely double-escape, so
    the XML parser hands back a title still carrying a literal ``&#8217;`` where
    an apostrophe belongs. Left alone that reaches the reader as mojibake.
    """
    if value is None:
        return None
    flat = _WHITESPACE.sub(" ", html.unescape(value))
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", flat).strip() or None


def strip_markup(value: str | None) -> str | None:
    if not value:
        return None
    parser = _TextOnly()
    try:
        parser.feed(value)
        parser.close()
    except AssertionError:
        # HTMLParser's own failure mode on markup it cannot recover from. The
        # item is still real; fall back to the raw text rather than losing it.
        return clean(value)
    return clean("".join(parser.parts))


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


def parse_date(value: str | None) -> datetime | None:
    """RFC 822 or ISO 8601 to an aware datetime, or ``None``.

    Used for one thing only -- deciding whether a *candidate* feed is still
    alive. Stored items keep the publisher's own string and are ordered by when
    we first saw them, so nothing downstream depends on this being right.
    """
    if not value:
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _child_text(node, names) -> str | None:
    for child in node:
        if local(child.tag) in names and child.text:
            return child.text
    return None


def _link(node, base: str | None) -> str | None:
    """The article url.

    RSS puts it in the element's text; Atom puts it in ``href`` and may offer
    several, of which ``rel="alternate"`` (or no ``rel`` at all) is the article
    and the rest are comments, enclosures and self-references.
    """
    fallback = None
    for child in node:
        if local(child.tag) != "link":
            continue
        href = child.get("href")
        if href:
            rel = (child.get("rel") or "alternate").lower()
            if rel == "alternate":
                return canonical_url(href, base)
            fallback = fallback or canonical_url(href, base)
        elif child.text and child.text.strip():
            return canonical_url(child.text, base)
    return fallback


def _fingerprint(feed_name: str, url: str | None, guid: str | None, title: str) -> str:
    if url:
        return url
    if guid:
        return f"{feed_name}|{guid.strip()}"
    digest = hashlib.sha256(f"{feed_name}|{title}".encode()).hexdigest()[:32]
    return f"{feed_name}|{digest}"


def _channel_text(document: str, names) -> str | None:
    """A channel-level field, read by stopping at the first item.

    ``<title>`` exists at both levels and the channel's comes first in every
    dialect, so the first one seen before an item boundary is the feed's own.
    """
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        return None
    for node in root.iter():
        if local(node.tag) in ITEM_TAGS:
            return None
        if local(node.tag) in names and node.text:
            return strip_markup(node.text)
    return None


def document_title(document: str) -> str | None:
    """The feed's own title -- for naming a candidate nobody named."""
    return _channel_text(document, TITLE_TAGS)


def document_description(document: str) -> str | None:
    """The feed's own description, from the channel rather than an item."""
    return _channel_text(document, ("description", "subtitle"))


def parse(
    document: str,
    feed_name: str,
    *,
    base_url: str | None = None,
    max_items: int = 40,
    summary_cap: int = 600,
) -> list[Entry]:
    """Entries from a feed document, in the order the publisher put them.

    Raises :class:`FetchError` on a document that is not XML at all -- which in
    practice means a captive portal, a rate-limit page or an error page served
    with a 200, and is a fetch problem wearing a parse problem's clothes.
    """
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise FetchError(f"feed {feed_name!r}: not parseable as XML: {exc}", feed=feed_name) from exc

    entries: list[Entry] = []
    for node in root.iter():
        if local(node.tag) not in ITEM_TAGS:
            continue
        title = clean(_child_text(node, TITLE_TAGS))
        if not title:
            # An item with no title is not a headline we can hand over, and
            # inventing one would put words in a publisher's mouth.
            continue

        url = _link(node, base_url)
        summary = strip_markup(_child_text(node, SUMMARY_TAGS))
        if summary and len(summary) > summary_cap:
            summary = summary[:summary_cap].rstrip() + "..."

        entries.append(
            Entry(
                feed=feed_name,
                title=title,
                url=url or "",
                fingerprint=_fingerprint(feed_name, url, _child_text(node, ID_TAGS), title),
                summary=summary,
                published_text=clean(_child_text(node, DATE_TAGS)),
            )
        )
        if len(entries) >= max_items:
            break
    return entries
