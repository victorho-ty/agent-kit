# news-monitor — design notes

What was decided, and what it cost.

## The source list lives in the database, not in JSON

Every sibling bundle (`news-radar`, `video-summary`, `stock-desk`) keeps
what-it-watches in a package-data JSON file and its fetch health in SQLite. This
one merges them into one `feed` table.

The reason is discovery. Sources are added at run time by the agent, so a JSON
file would have to be written by the tools — and a file that is both shipped
package data and mutable runtime state drifts within a week: an upgrade
overwrites it, a hand edit collides with a write, and `enabled` ends up meaning
two different things depending on which you read.

The cost is that `seeds.json` is inert after the first run, which is surprising
enough to be worth the comment at the top of the file. Editing a url there
changes nothing.

## Identity is the article url

`news-radar` hashes source, url and title together, because two outlets
publishing the same story are genuinely two items worth clustering. Here they
are not: the Dow Jones markets wire and MarketWatch top-stories carry the same
articles, and reporting one story twice is a bug.

So the fingerprint is the canonical url — tracking parameters stripped — and it
is globally unique. The second feed to see an article does not re-insert it, and
the item is attributed to whichever feed saw it first. Two fallbacks exist for
feeds that give no usable link: `feed|guid`, then `feed|sha256(title)`, both
namespaced because neither is unique anywhere else.

The visible consequence: an item's `feed` is not "the only feed carrying this".

## Three dialects, one parser

RSS 2.0, RSS 1.0/RDF and Atom differ in namespace and in where a link lives.
Rather than three parsers, `feed.py` matches on each element's *local* name and
ignores its namespace, which collapses the three to one path plus four lines for
Atom's `href`.

The alternative was a feed-reader dependency. It would have been a second
runtime package for about eighty lines of work against three formats that have
not moved in twenty years, and the scheduler would have to keep it alive.

## The ledger is stamped by `mark`, not by `check`

The brief asked for "entries that have not been returned before", which reads as
stamping on return. It is stamped on `mark` instead, after the agent has
actually reported the item.

Stamping on return loses headlines permanently whenever a run dies between the
payload printing and the message going out — and the whole point of a ledger is
that a crash costs nothing. The cost of the chosen design is the opposite
failure: an item reported but not marked comes round again. Duplicated news is
recoverable; lost news is not, and nobody knows it happened.

This is also why `mark` is per-item rather than per-batch, and why marking an
already-marked item succeeds instead of failing.

## The cold start is absorbed silently

A feed's first successful check inserts everything pre-stamped and reports
`absorbed` rather than `new`. Forty stories that were already published when the
feed was added are not news, and delivering them teaches the reader that this
tool reports the past.

`seeded_feeds` is in every payload so the silence is never mistaken for a broken
feed.

## Discovery: the agent searches, the code refuses to believe it

Deciding whether a feed is "useful for capital-markets trading" is judgement,
and no expression turns that into Python. So the search is the agent's — and
every url it comes back with is fetched, parsed and measured before it becomes a
row.

The split matters in both directions. The agent cannot add a parked domain or an
HTML page, because those fail outright. The *code* cannot silently reject a good
feed, because a candidate that is a real feed is always stored — failing the
quality tests means `enabled: false` with the reason on the row, one `enable`
away from live.

Nothing is discarded, which is deliberate: a dropped url is re-proposed by the
next sweep, forever.

`discovery.tracked_urls` ships in every `check` payload for the same reason. It
is the only thing stopping a sweep from re-proposing the five seeded feeds every
hour.

### The interval stamp

`last_discovery_prompt_at` is written when the agent is *asked* to sweep, not
when it finds something. Stamping on success would make any interval above zero
fire every run until a feed was found — which, for a well-covered database, is
never.

Default interval is 0: every run, as specified. The knob exists so it can be
dialled back without a code change.

## The gate is crude on purpose

`min_items`, `max_age_days` and a finance-vocabulary count. All three are
keyword-and-counting, all three are wrong sometimes, and all three produce a
stored row rather than a refusal.

A cleverer gate would be a classifier, and a classifier is a model — at which
point the deterministic half of this bundle has an opinion, which is exactly the
line this repo draws. Crude, transparent, and overridable in one command beats
accurate and unexplainable.

Seeds bypass the gate entirely, which is the right answer for a source a human
chose. A statistical agency's feed that publishes one release at a time would be
rejected as `thin` on its own merits, and that is a judgement nobody asked the
gate to make about a hand-picked list.

## Hints, not classifications

`classify.py` matches keywords on word boundaries over the headline and the
publisher's header paragraph. It scores nothing and weighs nothing.

Word boundaries are the whole design: `ism` inside `mechanism` and `cds` inside
`cdss` would bucket headlines into sectors they have nothing to do with, and the
agent would have no way to know. Terms with punctuation (`s&p`, `chapter 11`)
get a boundary on whichever end accepts one.

The payload calls them `sector_hints` rather than `sectors` so nothing downstream
mistakes a keyword match for a finding.

## `NEWS_MONITOR_CONTACT`

bls.gov answers 403 to any User-Agent without an email address in it — including
a browser's — and 200 with one. This was found by probing, not assumed.

BLS was seeded on the strength of that finding and then removed, because
requiring an email address to run the bundle out of the box is a poor trade for
one feed. Nothing in the seeded list needs the knob now.

The knob stays, and is not scaffolding: the source list grows at run time, and
the statistical agencies are exactly what a sweep should find. Without it a
`.gov` feed added later fails with an opaque 403 and no remedy — the agent would
see `unreachable`, conclude the url was wrong, and disable a working source.
With it, the triage path is one hop, and both `SKILL.md` and
`references/sources.md` name a `.gov` 403 as this.

It is an environment variable rather than a shipped constant because the address
identifies whoever is running the tool, and a placeholder baked into the repo
would put someone else's address on requests they did not make.

## What is deliberately absent

- **No article fetching.** The agent gets the headline, the publisher's teaser
  and a link. Fetching bodies would mean a renderer, a paywall policy and a
  store, for content the reader reaches by tapping the link.
- **No clustering.** `news-radar` clusters because it builds a digest across
  outlets. This hands over items; grouping them is the agent's job and it has
  the category and the hints to do it.
- **No delete.** Sources are disabled. A deleted row is re-discovered and
  re-seeded as news.
- **No per-feed throttle.** `news-radar` has one because it mixes hourly wires
  with quarterly sources. Every feed here is a wire on an hourly cron, and
  conditional GET already makes an unchanged feed nearly free.
