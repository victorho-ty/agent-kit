# Sources, categories, clustering, and the traps

`news_radar/config/sources.json` is the whole of what the radar watches.
Whole-line `//` comments are stripped before parsing, so disabled examples can
live in the file as documentation.

## Global keys

| key | default | meaning |
|---|---|---|
| `timezone` | `Asia/Hong_Kong` | how timestamps are written |
| `categories` | required | the digest's sections, **in display order** |
| `exclude` | none | drops an item outright, whatever else it says |
| `request_delay_seconds` | `2.0` | pause between requests within one scan |
| `detail_budget` | `10` | most detail pages one source may be asked for per scan |
| `cluster_threshold` | `0.7` | how alike two headlines must be to be one story |

There is deliberately **no `scan_window` and no `scan_interval`**. The scan
messages nobody, so it has no quiet hours to keep, and the cron entry is the one
statement of how often it runs — restating it here would be a second source of
truth that drifts. The one cadence knob cron cannot express is per-source, below.

## Categories

```json
"categories": [
  { "name": "ai", "label": "AI" },
  { "name": "world", "label": "World" }
]
```

`name` is what a source refers to; `label` is what a reader sees. A bare string
(`"ai"`) is accepted when no separate label is wanted.

**The order is the order of sections in the digest.** Put what matters first;
alphabetical would bury it. A category with nothing new is omitted from a
digest rather than printed empty.

A source naming a category that is not declared here is a **load-time error**,
not a new section. A typo silently creating a one-line section nobody ordered is
exactly the sort of quiet wrongness this repo refuses.

## Per-source keys

| key | default | meaning |
|---|---|---|
| `name` | required | unique; the dedupe key is scoped by it |
| `url` | required | http(s) |
| `category` | **required** | which section its stories appear under |
| `kind` | `rss` | `rss` \| `html` \| `regex` |
| `render` | `static` | `static` (urllib) \| `browser` (Chromium) |
| `list_selector` | required for `html` | the repeated element, one per item |
| `fields` | required for `html` | field name → CSS selector, evaluated inside each element |
| `item_pattern` | required for `regex` | a Python regex with a named `title` group |
| `follow_detail` | `false` | fetch each new item's own page |
| `detail_selector` | none | narrow the detail page before reading it |
| `min_interval_minutes` | `0` | a floor on how often this source is fetched |
| `max_items` | `40` | items taken from one page |
| `enabled` | `true` | `false` pauses a source without losing its history |

### Prefer a feed

`kind: "rss"` handles RSS 2.0 and Atom, needs no selectors, dates its own
entries, and **cannot be broken by a redesign** — which is the failure mode
everything else here defends against. Nearly every outlet publishes one. Reach
for `html` only when there is genuinely no feed.

For `html`, `fields` values are CSS selectors resolved *inside* each matched
element, and a trailing `@attr` takes an attribute instead of text:

```json
"fields": { "title": "h2 a", "link": "h2 a@href", "summary": "p.standfirst", "date": "time" }
```

Relative links are resolved against the page URL, and `utm_*`, `fbclid`,
`gclid`, `ref` and the fragment are stripped before storage.

Set `follow_detail: false` for news. A feed's `description` is already the
summary, and fetching every article body is expensive and pointless for a
headline digest.

### min_interval_minutes is a floor, not a schedule

The hourly cron still runs. A source inside its floor is reported `throttled`
and skipped; the others in that run scan normally. Use it for slow personal
blogs, and for anything with `"render": "browser"` — a browser source starts
Chromium and has no ETag to send, so it is the one thing that genuinely costs
something to scan often.

`--ignore-throttle` overrides it for a manual test.

## Clustering

Two items become one story when either holds:

1. **their canonical URLs are identical *and they come from different
   sources*** — straight syndication, the strongest signal there is. The
   same-source case is deliberately excluded: a page that gives its items no
   links of their own leaves every one of them holding the page's own URL, and
   treating that as syndication collapses the whole source into one story;
2. **their headlines overlap enough** — shared significant words over the
   *smaller* headline's word count, at or above `cluster_threshold`.

The second is measured with the overlap coefficient rather than Jaccard on
purpose. Jaccard divides by the union, which punishes a headline for being long
even when it contains the other whole: "OpenAI releases GPT-X" against "OpenAI
Releases GPT-X, Its Biggest Model Yet" scores 0.5 and would split at any sane
threshold. Dividing by the smaller set asks the question that matters — is the
shorter headline essentially contained in the longer one — and scores 1.0.

Headlines are folded for case and width, stripped of a short stopword list, and
tokenised keeping internal hyphens, so `GPT-X` stays one distinctive token
instead of becoming `gpt` plus a discarded `x`.

A headline with fewer than two significant words left is never clustered: with
almost nothing to compare, any two thin headlines look identical.

**Raising `cluster_threshold`** splits rewordings apart (more duplicate lines);
**lowering it** merges stories that merely share a subject (worse — the digest
starts implying connections that do not exist). `tests/test_cluster.py` pins
both edges.

The default is `0.7` because of a false merge found against live feeds at `0.6`:
"Introducing Muse Glimmer" and "Introducing Muse Code and Muse Spark 1.2" are
two different product posts sharing `{introducing, muse}`, which scores 2/3 =
0.667. A short headline is easily contained in a longer one, and that is the
shape the mistake takes in the wild.

### Two limits, stated rather than discovered

- **Clustering is within a category.** Sections are the reader's own taxonomy; a
  story carried by sources in two categories is genuinely relevant to both, and
  merging across would force an arbitrary choice about which section loses it.
  Expect such a story to appear in both sections.
- **Clustering is within a single digest.** Nothing is stored, because
  membership depends on which items happen to be pending together. A story that
  breaks today and is picked up tomorrow appears in two digests.

## Traps

**`source_domain` is the domain of the article link, not of the feed.** For most
outlets these are the same. For an aggregator like Hacker News they are not: the
link goes to the article, so the label reads `effort.news`, not
`news.ycombinator.com`. That is the more useful label — it says where you would
be reading — and it is also what lets an aggregator's copy of a story cluster
with the outlet's own by identical URL. `items` still shows the configured
`source` name for triage.

**Feeds double-escape.** A title arrives as `Zuckerberg&#8217;s` because the
value was escaped twice before the XML parser saw it. Entities are decoded on
extraction; left alone they reach the reader as mojibake *and* change the tokens
the clusterer compares, so the same story from a tidy feed and a sloppy one
would stop matching.

**A moved feed looks exactly like a quiet week.** Both yield nothing. That is
why `site_state.recent_yield` exists: a source that returned items on its last
successful scan and returns none now is reported `zero_yield`. A source that has
*never* yielded anything is treated as quiet, so a wrong URL on a brand new
source is only caught by `--dry-run`.

**A cold start is not news.** A source's first successful scan stores everything
already published with `digested_at` pre-stamped. `--seed` does the same on
demand and ignores the stored ETag so it actually re-reads.

**Dedupe is per source.** The same story from two outlets is two items on
purpose — recognising them as one story is the digest's job, and doing it at
scan time would mean a rewording on one outlet suppressed the story everywhere.

**`exclude` is the only filter, and it is substring-matched** on title, summary
and date after case folding. Keep the terms specific: `"ad"` would match
`"advance"`, `"Adelaide"` and `"broadband"`.
