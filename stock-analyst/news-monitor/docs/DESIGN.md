# news-monitor — design notes

What was decided, and what it cost.

## The source list lives in the database, not in JSON

Every sibling bundle (`news-radar`, `video-summary`, `stock-desk`) keeps
what-it-watches in a package-data JSON file and its fetch health in SQLite. This
one merges them into one `feed` table.

The reason is `add`. The operator can add a source at any time, so a JSON
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

## No self-discovery: sources are added on the operator's request

The first version asked the agent to search the web for new finance feeds on
every run: `check` carried a `discovery` block with `due` and the list of
tracked urls, and a `meta` table timed the prompts. That was removed at the
operator's direction. The source list is a decision about what the desk reads,
and it is theirs — an agent adding sources on its own initiative changes the
desk's inputs without anyone having chosen to.

What remains is the mechanism, not the initiative:

- `seeds.json` bootstraps the table, as before.
- `add` exists for a source the operator asks for, and is the only way a row is
  created after the first run. `SKILL.md` forbids running it unasked, and
  `check` no longer gives the agent any prompt to.
- Rows created by `add` carry `origin: added`. Rows written by the first
  version say `discovered`; nothing reads the value, so they were left alone.

The gate stays exactly as it was, because the reason for it does not depend on
who picked the source: a url someone typed can still be an HTML page, a parked
domain or a mistyped path. The agent cannot add one of those, because they fail
outright. And the code cannot silently refuse what the operator asked for: a
candidate that is a real feed is always stored — failing the quality tests
means `enabled: false` with the reason on the row, reported back, one `enable`
away from live if the operator disagrees.

The module that does this is `gate.py`. It was `discover.py` while the agent
searched; the old name described a job the bundle no longer does.

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

The knob stays, and is not scaffolding: the operator can `add` a government
source at any time — three US ones are seeded already, none of which currently
check. Without it a `.gov` feed added later fails with an opaque 403 and no remedy — the agent would
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
- **No delete.** Sources are disabled. The row keeps the record of why a source
  went quiet, and its items still name it.
- **No per-feed throttle.** `news-radar` has one because it mixes hourly wires
  with quarterly sources. Every feed here is a wire on an hourly cron, and
  conditional GET already makes an unchanged feed nearly free.
