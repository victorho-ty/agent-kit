# The source list, the gate, and what a feed does not tell you

## The database is the source of truth

Every sibling bundle keeps what-it-watches in a JSON file. This one does not,
and the reason is `add`: the operator can add a source at any time, and a file
the tools also wrote would be a second truth that drifts within a week.

**Nothing adds sources on its own.** `check` does not propose them and the
agent does not search for them. The list changes only through `add`,
`enable` and `disable`, run on the operator's request.

`news_monitor/config/seeds.json` bootstraps an **empty** database and is then
inert. Editing a url there after the first run changes nothing. Add the new url
with `add` and `disable` the old row.

The seeded sources:

| name | category | what it is |
|---|---|---|
| `federal-reserve` | `central-bank` | every Fed press release — FOMC statements, minutes, speeches, enforcement, regulatory |
| `hkma` | `central-bank` | Hong Kong Monetary Authority |
| `us-bureau-of-economic-analysis` | `economic-data` | Bureau of Economic Analysis |
| `us-sec` | `regulatory` | US Securities and Exchange Commission |
| `dj-markets` | `markets` | Dow Jones markets wire |
| `cnbc-finance` | `markets` | CNBC finance |

**Seeds bypass the gate**, which is the right answer for a source a human chose:
a feed that publishes one release at a time would be rejected as `thin`, and a
seeded list is not a place the gate's opinion is wanted.

## `NEWS_MONITOR_CONTACT`, and `.gov` feeds

Optional. Nothing seeded needs it — `federalreserve.gov`, `bea.gov` and
`sec.gov` do not currently check — and it can stay unset indefinitely.

It matters when the operator adds another government source. US data hosts publish an
access policy requiring an identifiable requester: **bls.gov answers 403 to any
User-Agent with no email address in it** — including a browser's — and serves
the feed immediately with one.

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
| already tracked | `already_tracked` | `ERR_CANDIDATE`, nothing stored | the url, or what it redirects to, is on the list. Tell the operator which feed it already is. |
| reachable | `unreachable` | `ERR_CANDIDATE`, nothing stored | fetch failed after its retries |
| is a feed | `not_a_feed` | `ERR_CANDIDATE`, nothing stored | an HTML page, a parked domain, a login wall, a rate-limit notice served with a 200 |
| `min_items` (3) | `thin` | **stored, disabled** | a feed just set up, or abandoned — one fetch cannot tell which |
| `max_age_days` (30) | `stale` | **stored, disabled** | a dead section nobody took down |
| `min_finance_hits` (3) | `off_topic` | **stored, disabled** | the "finance only" rule, made mechanical |

**A candidate that reaches the last three is always stored.** Failing means
disabled with the reason on the row, not discarded: the operator asked for this
url, and the row keeps the request and the reason together. Report the verdict
to them; `enable` it only if they say so.

The finance test counts distinct terms from `taxonomy.json` across the feed's
title, its description and up to 25 recent headlines. It is crude on purpose,
and it is why `off_topic` is a disagreement worth raising with the operator:
`news-monitor enable --feed <name>` overrides it in one call.

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
  is the canonical article url, so a story carried by two wires is one item
  under one feed name. That is the dedupe working.

## Adding a source — on request only

Only when the operator asks. See `SKILL.md` for the steps; in short:

1. Use the url they gave. If they named a publisher but no url, look up that
   publisher's feed url only, and confirm it with them before adding.
2. `news-monitor add --url <url> --category <bucket> --note "<one line>"`.
3. Report the `gate` block back. A `pass` on a feed whose `sector_hints` are all
   wrong is worth pointing out; whether to `disable` it is their call.
4. `news-monitor check --feed <name> --dry-run` and read back a few titles. A
   feed enabled on an unverified url looks healthy and reports nothing forever.

Categories in use: `central-bank`, `economic-data`, `regulatory`, `markets`, and
`general` for anything `add` was not told about. They are free text — a new one
costs nothing — but they are what the digest is grouped by, so reuse before
inventing.

## Retiring a source

`disable`, on the operator's request, never delete. The row keeps the record of
why a source went quiet, and its items still name it. There is no delete
command for exactly this reason.
