# news-monitor

Polls a database of finance RSS feeds, hands the agent every headline it has not
seen before, and drops the topics the desk has ruled out of scope.

Part of the `stock-analyst` profile. Deploys to
`~/projects/hermes/profile-stock-analyst/news-monitor`.

## What it does

- **Keeps the source list in SQLite**, not in a config file — seeded from
  `seeds.json` on first run, then changed only through `add` / `enable` /
  `disable`, on the operator's request. Nothing searches for sources.
- **Reports each headline exactly once.** Identity is the canonical article url,
  so two wires carrying one story are one item. "Not yet reported" is a column,
  not a time window, so a missed cron run costs nothing.
- **Checks every source the operator adds** before it becomes a row: fetched,
  parsed and gated, so a mistyped url or an HTML page is refused with a reason.
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
User-Agent with no email address in it** — so another `.gov` feed you add may
need it:

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
  gate.py         the quality gate a feed offered to `add` must clear
  feed.py         RSS 2.0, RSS 1.0 and Atom, through one code path
  fetch.py        conditional GET, backoff, the User-Agent that matters
  classify.py     keyword hints, and nothing more
  db.py           feed, item, runs
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

## JEV scope classification

`check` uses TypeSafe JEV before returning news for operator reporting or LLM
processing. Set `TYPESAFE_API_KEY` in the Hermes profile environment inherited
by the shell. The key is read at runtime and never written to the ledger.
`NEWS_MONITOR_SCOPE_MODEL` optionally overrides the default `jev-latest`.

The exact operator prompt is shipped in `news_monitor/config/scope_prompt.txt`
and sent unchanged as Choice instructions. News is supplied separately as title
and RSS summary; no article fetch is performed. JEV is a typed decision model,
so it cannot generate the prompt's free-text explanation. Python maps its
include/exclude choice and confidence into `scope.is_target_scope`,
`scope.reason`, and `scope.confidence_score`. `reason_source` explicitly marks
the reason as a fixed classification label, not a generated topic explanation.
There is no confidence threshold beyond JEV's chosen option.

Existing taxonomy exclusions run first. New stories are deduplicated before
classification. In-scope decisions persist across checks, including 304s.
Newly classified exclusions are discarded from the item table. Brief records
(title, URL, reason and confidence) appear only in `scope_filter.excluded_items`
in the command's JSON output for Hermes. Only excluded headlines are retained
in `runs.detail.scope.excluded_subjects`; excluded URLs, summaries, reasons and
confidence are not persisted there. This list covers JEV exclusions, not the
keyword exclusions that happen before insertion. Older runs are unchanged.
No exclusion fingerprint is retained, so a later fetched document containing
the same excluded story can trigger classification again. Existing stored
history is left untouched. Missing
keys, API failures and invalid answers withhold affected news and leave it
pending for retry. `status: partial` and `scope_filter.failures` expose these
failures; `runs.detail` retains them. Authentication/configuration errors stop
further calls for that run. Errors contain no API response body or key.

Each check examines up to `--limit` pending items (default 40), which bounds
classification work; exclusions can make the returned batch smaller. The SDK
uses a 20-second request timeout, at most three attempts, and a 60-second retry
budget per story. No calls are made for absorbed history or dry runs.
`pending_items` includes news awaiting a decision; `awaiting_scope` counts it.
`items --pending` and `mark --all` cover only classified, in-scope news.
`items` without `--pending` is history and may include unclassified rows
or exclusions retained by older versions: never use it as a reporting batch.

Existing databases gain two nullable columns on connection. History is retained;
previously pending stories receive classification on subsequent checks.

API contract: https://docs.typesafe.ai/api
