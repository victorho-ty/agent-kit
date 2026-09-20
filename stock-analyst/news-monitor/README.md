# news-monitor

Polls a database of finance RSS feeds, hands the agent every headline it has not
seen before, and grows its own source list.

Part of the `stock-analyst` profile. Deploys to
`~/projects/hermes/profile-stock-analyst/news-monitor`.

## What it does

- **Keeps the source list in SQLite**, not in a config file — because discovery
  adds sources at run time. `enabled` is a column, and `enable` / `disable` /
  `add` are the whole editing surface.
- **Reports each headline exactly once.** Identity is the canonical article url,
  so two wires carrying one story are one item. "Not yet reported" is a column,
  not a time window, so a missed cron run costs nothing.
- **Asks the agent to go looking for new feeds**, then refuses to take its word
  for it: every candidate is fetched, parsed and gated before it becomes a row.
- **Attaches sector and signal hints** from a keyword taxonomy, so the agent can
  bucket forty headlines without inferring "this is about rates" from the word
  "FOMC" forty times an hour.

## Install

```bash
uv venv && uv pip install -e .
ln -s "$PWD/.venv/bin/news-monitor" ~/.local/bin/news-monitor
```

Optionally set a contact address. Nothing seeded needs it, but US government
data hosts require an identifiable requester — **bls.gov answers 403 to any
User-Agent with no email address in it** — so a `.gov` feed added by a discovery
sweep will need it:

```bash
export NEWS_MONITOR_CONTACT="you@example.com"
```

## Run

```bash
news-monitor check                  # the cron entry
news-monitor feeds                  # the source list and its health
news-monitor add --url <url> --category markets
news-monitor mark --item <id>       # after the item has been reported
news-monitor runs --limit 3         # liveness
```

One cron entry:

```
5 * * * *    news-monitor check
```

## Layout

```
news_monitor/
  cli.py          JSON-in / JSON-out command surface
  check.py        one run: fetch, store, hand back what is unseen
  discover.py     the quality gate a candidate feed must clear
  feed.py         RSS 2.0, RSS 1.0 and Atom, through one code path
  fetch.py        conditional GET, backoff, the User-Agent that matters
  classify.py     keyword hints, and nothing more
  db.py           feed, item, runs, meta
  config/         seeds.json (bootstrap only) and taxonomy.json (the vocabulary)
skills/news-monitor/
  SKILL.md        what the agent does with all this
  references/     command surface, and the source list in detail
```

## Tests

```bash
.venv/bin/python -m pytest
```

Nothing in the suite touches the network: `tests/conftest.py` builds a stand-in
for `fetch.get` from fixtures on disk, which is what lets a whole run —
conditional GET, seeding, dedupe, the ledger — execute at a fixed instant.

## Design notes

`docs/DESIGN.md`.
