"""Turning a YouTube channel feed into entries.

The document is Atom with two YouTube namespaces bolted on, and its shape is
maintained by YouTube rather than by a theme -- which is why this file has no
selectors, no configuration, and no escape hatch. If it stops parsing, YouTube
changed the feed for everybody and the fix belongs here, once.

What the feed gives, and what it does not:

* ``yt:videoId`` is the identity, and it is global. Two feeds carrying the same
  video are one video, which is why the database keys on it rather than on
  ``(feed, url)``.
* ``media:thumbnail@url`` is a real, hotlinkable image url. It is handed to
  Telegram as a string; nothing here downloads an image.
* ``published`` is the source's own words, kept as a string and never parsed
  into a datetime. Nothing in this bundle needs the value -- ordering is by the
  order we first saw things -- so parsing it would only create a way to be
  wrong.
* **Nothing says how long a video is, and nothing says whether it is a Short.**
  Those live behind the watch page. See :func:`video_summary.fetch.resolved_url`.
"""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET

from .errors import FetchError
from .models import Entry

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"

WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
SHORTS_URL = "https://www.youtube.com/shorts/{video_id}"
# YouTube always serves this, for every video, at a fixed url. It is the
# fallback when an entry somehow arrives without a media:thumbnail.
FALLBACK_THUMBNAIL = "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _text(node, path: str) -> str | None:
    found = node.find(path)
    if found is None or found.text is None:
        return None
    # Feeds double-escape: a title arrives as "Zuckerberg&#8217;s" because the
    # value was escaped twice before the XML parser saw it. Left alone it
    # reaches the reader as mojibake.
    return html.unescape(found.text).strip() or None


def parse(document: str, feed_name: str, *, max_items: int = 15) -> list[Entry]:
    """Entries from a channel feed, newest first as YouTube orders them.

    Raises :class:`FetchError` on a document that is not XML at all -- which in
    practice means a captive portal or an error page served with a 200, and is a
    fetch problem wearing a parse problem's clothes.
    """
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise FetchError(f"feed {feed_name!r}: not parseable as XML: {exc}", feed=feed_name) from exc

    channel = _text(root, f"{ATOM}title")
    channel_url = None
    author = root.find(f"{ATOM}author")
    if author is not None:
        channel = _text(author, f"{ATOM}name") or channel
        channel_url = _text(author, f"{ATOM}uri")

    entries: list[Entry] = []
    for node in root.findall(f"{ATOM}entry")[:max_items]:
        video_id = _text(node, f"{YT}videoId")
        title = _text(node, f"{ATOM}title")
        if not video_id or not title:
            # An entry with no id or no title is not a video we can act on, and
            # guessing either would put a wrong link in front of a reader.
            continue

        group = node.find(f"{MEDIA}group")
        thumbnail = None
        description = None
        if group is not None:
            thumb = group.find(f"{MEDIA}thumbnail")
            if thumb is not None:
                thumbnail = thumb.get("url")
            description = _text(group, f"{MEDIA}description")

        entries.append(
            Entry(
                feed=feed_name,
                video_id=video_id,
                title=title,
                url=WATCH_URL.format(video_id=video_id),
                channel=channel,
                channel_url=channel_url,
                thumbnail_url=thumbnail or FALLBACK_THUMBNAIL.format(video_id=video_id),
                published_text=_text(node, f"{ATOM}published"),
                description=description,
            )
        )
    return entries


def kind_of(video_id: str, resolver) -> str:
    """``short``, ``video`` or ``unknown``.

    ``/shorts/<id>`` stays put for a Short and redirects to ``/watch`` for
    anything else. ``resolver`` is injected so the tests never touch the
    network; it returns the final url or ``None``.
    """
    final = resolver(SHORTS_URL.format(video_id=video_id))
    if not final:
        return "unknown"
    if "/shorts/" in final:
        return "short"
    if "/watch" in final:
        return "video"
    return "unknown"
