# The source list, the gate, and what a feed does not tell you

## The database is the source of truth

Every sibling bundle keeps what-it-watches in a JSON file. This one does not,
and the reason is discovery: sources are added at run time, so a file the tools
also wrote would be a second truth that drifts within a week.

`news_monitor/config/seeds.json` bootstraps an **empty** database and is then
inert. Editing a url there after the first run changes nothing. Add the new url
with `add` and `disable` the old row.

The five seeded sources:

| name | category | what it is |
|---|---|---|
| `federal-reserve` | `central-bank` | every Fed press release — FOMC statements, minutes, speeches, enforcement, regulatory |
| `dj-markets` | `markets` | Dow Jones markets wire |
| `marketwatch-top` | `markets` | MarketWatch top stories |
| `cnbc-finance` | `markets` | CNBC finance |

**Seeds bypass the gate**, which is the right answer for a source a human chose:
a feed that publishes one release at a time would be rejected as `thin`, and a
seeded list is not a place the gate's opinion is wanted.

There is no seeded `economic-data` source. The statistical agencies are exactly
what a discovery sweep should be finding — see the note on `.gov` feeds below
before adding one.

## `NEWS_MONITOR_CONTACT`, and `.gov` feeds

Optional. Nothing seeded needs it, and it can stay unset indefinitely.

It matters when a sweep adds a government source. US data hosts publish an
access policy requiring an identifiable requester: **bls.gov answers 403 to any
User-Agent with no email address in it** — including a browser's — and serves
the feed immediately with one. `federalreserve.gov` does not currently check.

So a `.gov` candidate that fails `add` as `unreachable` with a 403, or a `.gov`
feed that starts failing every run, is this until proven otherwise. Set it and
retry:

```bash
export NEWS_MONITOR_CONTACT="you@example.com"
```

It is read from the environment rather than shipped because the address belongs
to whoever runs this.

## The gate, test by test

`add` fetches the candidate and measures it. The first three decide whether
there is a feed at all; the last three decide whether it goes live.

| test | verdict | outcome | what it actually catches |
|---|---|---|---|
| already tracked | `already_tracked` | `ERR_CANDIDATE`, nothing stored | the url, or what it redirects to, is on the list. The expected answer most of the time. |
| reachable | `unreachable` | `ERR_CANDIDATE`, nothing stored | fetch failed after its retries |
| is a feed | `not_a_feed` | `ERR_CANDIDATE`, nothing stored | an HTML page, a parked domain, a login wall, a rate-limit notice served with a 200 |
| `min_items` (3) | `thin` | **stored, disabled** | a feed just set up, or abandoned — one fetch cannot tell which |
| `max_age_days` (30) | `stale` | **stored, disabled** | a dead section nobody took down |
| `min_finance_hits` (3) | `off_topic` | **stored, disabled** | the "finance only" rule, made mechanical |

**A candidate that reaches the last three is always stored.** Failing means
disabled with the reason on the row, not discarded — a discarded url would be
re-proposed by the next sweep, forever.

The finance test counts distinct terms from `taxonomy.json` across the feed's
title, its description and up to 25 recent headlines. It is crude on purpose,
and it is why `off_topic` is a disagreement worth having: `news-monitor enable
--feed <name>` overrides it in one call.

**Undated items are not called stale.** A feed whose items carry no parseable
date is using a formatting convention, not showing evidence of abandonment.

## What the feed does not tell you

- **Nothing says how important a story is.** `sector_hints` and `signals` are
  keyword matches over the headline and the header paragraph. No weighting, no
  score, no model. Absent hints mean absent keywords.
- **Nothing gives you the article.** `summary` is the publisher's own teaser,
  capped at 600 characters. Everything past it is behind the link, and you have
  not been there.
- **The date is the publisher's string.** It is stored verbatim and never
  parsed, because ordering is by when the tools first saw an item. A wire that
  back-dates a correction does not reorder anything.
- **Which feed an item is attributed to is which feed saw it first.** Identity
  is the canonical article url, so a story on both the Dow Jones wire and
  MarketWatch is one item under one feed name. That is the dedupe working.

## Adding a source well

1. Read `discovery.tracked_urls` from the last `check`. Most candidates are
   already there.
2. `news-monitor add --url <url> --category <bucket> --note "<one line>"`.
3. Read the `gate` block back. A `pass` on a feed whose `sector_hints` are all
   wrong is a feed you should `disable` yourself.
4. `news-monitor check --feed <name> --dry-run` and read the titles. A feed
   enabled on an unverified url looks healthy and reports nothing forever.

Categories in use: `central-bank`, `economic-data`, `markets`, and `general` for
anything `add` was not told about. They are free text — a new one costs nothing
— but they are what the digest is grouped by, so reuse before inventing.

## Retiring a source

`disable`, never delete. A dropped row lets discovery re-find the url a week
later and re-absorb its back catalogue as news. There is no delete command for
exactly this reason.
