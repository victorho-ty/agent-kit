# news-radar — design

Why the bundle is shaped this way. Written alongside the implementation; where
this document and the code disagree, the code is right and this file is a bug.

## 1. The scan and the digest are two programs

`scan` collects and never speaks. `digest` speaks and never collects. They are
wired to separate cron entries, and the coupling between them is a **column**,
not a clock: `digest` takes every item with `digested_at IS NULL`.

Everything good follows from that:

- a missed scan needs no catch-up, because "new" is `item_key` against the table
  rather than a time range;
- a missed digest loses nothing and merely makes the next one longer;
- either cadence can change without touching the other, which is exactly why no
  interval appears in the config.

The freshness argument for scanning often is not really about freshness. **Many
feeds only expose the last N entries.** A source publishing 15 items between two
scans of a feed that holds 10 loses five permanently — they were never seen, so
nothing downstream can recover them. That is the strongest reason the scan runs
on its own frequent schedule.

## 2. No window, no interval, one floor

Quiet hours are a policy about when to bother a person, and a cron expression
states that badly — but the scan bothers nobody, so it needs no such policy at
all. The digest's own cron entries decide when anyone is interrupted.

A global `scan_interval` was considered and rejected: the cron entry already is
the cadence, and restating it in config creates two sources of truth that drift
silently. Conditional GET already makes an extra scan cost a 304.

What cron genuinely cannot express is per-source politeness, so that is the one
knob: `min_interval_minutes`, a **floor** on how often a single source may be
fetched, checked against `last_scan_at`. A source under its floor is reported
`throttled` and the run carries on — the failure to avoid is one slow blog
stalling the whole scan, and it is tested.

It reads `last_scan_at` rather than `last_ok_at` so that a failing source is
backed off too, not retried at full speed.

## 3. No matcher at all

There is nothing to infer about what an item is or who it is for. The human
answers that by assigning a category to the source, so everything from a source
in `ai` is AI news.

What survives is a single global `exclude` list for feed furniture — sponsored
posts, newsletter signups — which is a dozen lines inside `scan.py`, not a
module. There is deliberately **no include list**: an item that a source
published is in scope by definition.

## 4. Identity, and the two different questions it answers

`item_key = sha1(source | canonical url | folded title)` is identity **within
one source**. Title and URL together, because either alone is wrong: a hub page
that links every headline to itself collapses on URL alone, and an outlet
re-titling a story in place looks new on title alone.

Recognising that two *different* sources carry the same story is a separate
question with a separate answer, and it deliberately happens later, at digest
time. Conflating them — clustering at scan time and storing the result — would
mean a rewording on one outlet silently suppressed the story everywhere.

## 5. Clustering

Two items are one story when their canonical URLs match *and they come from
different sources* (syndication — the same-source case means the page simply has
no per-item links, and merging on it would collapse the source), or when
their headlines overlap by at least `cluster_threshold`.

**The overlap coefficient, not Jaccard.** Jaccard divides by the union, so it
punishes a headline for being long even when it contains the other one whole:
"OpenAI releases GPT-X" against "OpenAI Releases GPT-X, Its Biggest Model Yet"
scores 3/6 = 0.5 and splits at any sensible threshold. Dividing by the smaller
set asks the question that matters — is the shorter headline essentially
contained in the longer — and scores 3/3. This was found by the test suite, not
by reasoning, which is the argument for having written those tests first.

Supporting details, each with a reason:

- tokens keep internal hyphens, so `GPT-X` stays one distinctive token rather
  than becoming `gpt` plus a one-character `x` the length filter discards;
- a short stopword list only, because every word removed is a word that can no
  longer distinguish two stories;
- a headline with fewer than two significant tokens is never clustered — with
  almost nothing to compare, any two thin headlines look identical;
- single-link agglomeration in input (id) order, so the grouping is
  deterministic and a re-run of a digest cannot reshuffle it;
- the oldest item is the primary, so the outlet that broke the story supplies
  the title and the link.

Two limits are stated in the README rather than left to be discovered:
clustering is **within a category** (sections are the reader's taxonomy, and
merging across would force an arbitrary choice about which section loses the
story) and **within one digest** (membership depends on what happens to be
pending together, so storing it would be recording an accident).

## 6. Categories live on the source, not on the item

There is no `category` column. The category is read from the source's *current*
config when a digest is built, so recategorising a source moves everything of
its that has not gone out yet — which is what a person expects after the edit. A
source deleted from the config lands its pending items under `uncategorised`
rather than dropping them.

A source naming an undeclared category is a load-time error. A typo quietly
inventing a one-line section at the bottom of the digest is the kind of wrongness
nobody notices for a month.

## 7. Dates are not parsed

`published_text` is the source's own wording, relayed verbatim. Parsing would
buy ordering by recency and a "3 hours ago", at the price of occasionally
stating a confidently wrong time. The whole value of this skill is that its
facts can be trusted without checking. The cost is real and is listed under
Limitations: stories appear in first-seen order, not publication order.

## 8. The digest payload

Sections, each with stories — no flat list. The agent writes an intro and relays
titles, outlet domains and links. It is told explicitly not to summarise an
article it has only seen the headline of, because that is the one way this skill
could confidently say something false.

`source_domain` is the domain of the *article* link, not of the feed. For most
outlets they are the same; for an aggregator they are not, and the article's
domain is both the more useful label and the thing that lets an aggregator's
copy cluster with the outlet's own by identical URL.

Commit-then-send, not send-then-commit. A failed send leaves items marked as
sent, which `items --since` can recover; the other order re-announces the whole
backlog after a crash, which nothing can undo.

## 9. Telegram

There is no Telegram module: no bot token, no `sendMessage` wrapper, no HTTP
client for one. `digest.format_digest` returns a ready-to-send body and stops.
The agent owns the channel, as in every other bundle here.

## Out of scope

- Article-body fetching and per-story summarisation.
- Cross-digest and cross-category clustering (§5).
- Parsing dates into structured timestamps (§7).
- Any model call on the scan path.
- URL shorteners — links stay the publisher's own.
