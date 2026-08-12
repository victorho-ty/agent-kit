"""One story, five outlets.

This is the only genuinely new algorithm in the bundle, and it exists because a
digest that lists the same story once per source reads like spam and gets muted.
It is also the exact inverse of what education-radar does, which treats the same
title on two sites as two listings on purpose -- there, two schools running the
same-named event really are two events with two deadlines. Here they are one
piece of news.

Two items are the same story when either holds:

1. **their canonical URLs are identical** -- straight syndication, and the
   strongest signal there is. ``extract.canonical_url`` has already stripped the
   tracking parameters that would otherwise hide it;
2. **their titles overlap enough** -- the overlap coefficient over normalised,
   stopword-stripped token sets, at or above ``cluster_threshold``.

Exact title matching would not do. Outlets reword: "OpenAI releases GPT-X" and
"OpenAI Releases GPT-X, Its Biggest Model Yet" are one story and share every
significant word, which is precisely the case that has to collapse.

**Why the overlap coefficient and not Jaccard.** Jaccard divides by the *union*,
so it punishes a headline for being long even when it contains the other one
whole: that pair scores 3/6 = 0.5 and would be split at any sensible threshold.
Dividing by the smaller set instead asks the question that actually matters --
"is the shorter headline essentially contained in the longer one" -- and scores
it 3/3. The risk it takes on is that a very short headline is easily contained,
which is what :data:`MIN_SIGNIFICANT_TOKENS` guards.

Two deliberate limits, both documented in the README rather than left to be
discovered:

* **Clustering is within a category.** Sections are the human's own taxonomy; a
  story carried by sources in two categories is genuinely relevant to both, and
  merging across would force an arbitrary choice about which section loses it.
* **Clustering is within a single digest.** Nothing is stored, because
  membership depends on which items happen to be pending together -- persisting
  it would be recording an accident. A story that breaks today and is picked up
  tomorrow appears in two digests.
"""

from __future__ import annotations

import re

from .db import normalize
from .models import Item, Story

# Words that carry no topical weight in a headline. Kept deliberately short:
# every word removed here is a word that can no longer distinguish two stories,
# so this is a list of words that are nearly always noise, not a general
# stopword list.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from by with
is are was were be been being as it its he she they them his her their we our you your
new now says say said report reports amid over after before into out up down about
""".split())

# Drops punctuation but keeps digits, CJK, and internal hyphens. The hyphen
# matters: split on it, "GPT-X" becomes "gpt" plus a one-character "x" that the
# length filter then throws away, and the most distinctive word in the headline
# is gone.
_TOKEN = re.compile(r"[0-9a-z㐀-鿿]+(?:-[0-9a-z㐀-鿿]+)*")

# A headline with almost nothing left after stopword removal ("It begins") gives
# a signature too thin to compare: any other thin headline would look identical.
MIN_SIGNIFICANT_TOKENS = 2


def signature(title: str) -> frozenset[str]:
    """The significant words of a headline, order-insensitive."""
    tokens = _TOKEN.findall(normalize(title))
    significant = {token for token in tokens if token not in STOPWORDS and len(token) > 1}
    return frozenset(significant or tokens)


def similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Overlap coefficient: shared words over the *smaller* set.

    1.0 means one headline's significant words are all present in the other;
    0.0 means they share nothing.
    """
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def same_story(first: Item, second: Item, threshold: float) -> bool:
    # Identical URLs mean syndication -- but only *across* sources.
    #
    # Within one source an identical URL means the opposite thing: the page
    # gives its items no links of their own, so every one of them falls back to
    # the page's own URL. llm-stats.com/ai-news is exactly that -- ten headlines
    # in <button> elements with not one href between them -- and treating it as
    # syndication collapsed the entire source into a single story.
    #
    # Two *distinct* items from one source can never legitimately share a URL
    # anyway: item_key is sha1(source|url|title), so if the source and URL match
    # then the titles differ, which makes them different stories. Falling
    # through to the title comparison is always the right answer here.
    if first.source != second.source and first.url and first.url == second.url:
        return True
    left, right = signature(first.title), signature(second.title)
    if len(left) < MIN_SIGNIFICANT_TOKENS or len(right) < MIN_SIGNIFICANT_TOKENS:
        # Too little to go on -- treat as distinct rather than merge two
        # unrelated one-word headlines into a story that never existed.
        return False
    return similarity(left, right) >= threshold


def cluster(items: list[Item], threshold: float) -> list[Story]:
    """Group ``items`` into stories, preserving first-seen order.

    Single-link agglomeration in one pass: an item joins the first existing
    cluster it matches any member of. The pass is O(n * clusters) which is
    nothing at digest sizes, and the input order is the id order, so the result
    is deterministic and re-running a digest gives the same grouping.

    The first item in a cluster is its primary -- oldest wins, so the outlet
    that broke the story supplies the title and the link.
    """
    clusters: list[list[Item]] = []
    for item in items:
        for members in clusters:
            if any(same_story(member, item, threshold) for member in members):
                members.append(item)
                break
        else:
            clusters.append([item])

    return [
        Story(title=members[0].title, url=members[0].url, items=tuple(members))
        for members in clusters
    ]
