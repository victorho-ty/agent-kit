# news-monitor — command surface

Every command prints exactly one JSON object on stdout and exits 0 on success.
A failure prints `{"ok": false, "error": "ERR_...", "exit_code": N, "message":
"...", "detail": {...}}` and exits with that code. Branch on the code and on
`error`; never parse the message.

| code | name | means |
|---|---|---|
| 0 | `OK` | |
| 10 | `ERR_CONFIG` | `seeds.json` or `taxonomy.json` is malformed |
| 11 | `ERR_DB` | the database could not be opened or written |
| 20 | `ERR_FETCH` | the only feed asked for could not be reached |
| 21 | `ERR_CANDIDATE` | a url given to `add` is not a usable feed |
| 30 | `ERR_NOT_FOUND` | no feed with that name, no item with that id |

A per-feed failure during a `check` is **not** one of these. It lands in
`feed_failures`, the run finishes `partial`, and the exit code stays 0.

---

## `check`

The cron entry, and the only command that produces something to report.

```bash
news-monitor check
news-monitor check --feed federal-reserve --dry-run
news-monitor check --limit 10
```

| flag | effect |
|---|---|
| `--feed <name\|url>` | repeatable; defaults to every enabled feed. A named feed is checked even if disabled. |
| `--limit N` | items handed over this run. Defaults to `max_per_check` (40). |
| `--dry-run` | fetch and parse, write nothing, return nothing. Shows a 5-item sample per feed. |
| `--seed` | absorb what is published now without reporting it. Implied by a feed's first check. |

```json
{
  "ok": true,
  "status": "ok",
  "run_id": 41,
  "checked_at": "2026-09-19T09:05:00+08:00",
  "dry_run": false,
  "feeds": [
    {"feed": "federal-reserve", "status": "ok", "entries": 20, "new": 2},
    {"feed": "cnbc-finance", "status": "unchanged", "entries": 0, "new": 0}
  ],
  "feed_failures": [],
  "seeded_feeds": [],
  "excluded": 3,
  "items": [ ... ],
  "pending_items": 2
}
```

`status` is `ok` or `partial` (a feed or scope classification failed), or `skipped` with a
`reason` when nothing is enabled.

Per-feed `status`:

| value | meaning |
|---|---|
| `ok` | fetched, parsed, stored |
| `unchanged` | HTTP 304. The normal, cheap case. |
| `zero_yield` | parsed fine, produced nothing, and this feed used to produce. **Report this.** |
| `error` | unreachable or unparseable. Carries `error` and `consecutive_failures`. |

A feed's first successful check reports `absorbed` instead of `new`, and the run
lists it under `seeded_feeds`. Those items are stored pre-stamped: they are
history, not news.

`excluded` — per feed and in total — counts entries dropped by the `exclude`
list in `taxonomy.json` (terms the operator keeps, matched as word stems in the
headline or summary). They are never stored, so a story still in its feed is
counted again each run that re-fetches it; a 304 does not. With `--dry-run`,
each `sample` row carries `excluded_by`: the matching term, or `null`.

### One item

```json
{
  "id": 1183,
  "feed": "marketwatch-top",
  "category": "markets",
  "title": "Why mortgage bonds are set to deteriorate",
  "url": "https://www.marketwatch.com/story/why-mortgage-bonds-...",
  "summary": "A flattening Treasury yield curve is part of the problem for mortgage-backed securities, says Harley Bassman.",
  "published_text": "Fri, 18 Sep 2026 12:40:00 GMT",
  "sector_hints": ["macro-rates"],
  "signals": [],
  "first_seen_at": "2026-09-19T00:13:14+08:00",
  "reported_at": null
}
```

- `summary` is the **publisher's own header paragraph**, stripped of markup and
  capped at 600 characters. It is not the article and not a summary you wrote.
- `published_text` is the publisher's own string, never parsed. Quote it or
  ignore it; do not convert it.
- `sector_hints` and `signals` are keyword matches, not classifications. Empty
  means no keyword matched, not that the item is unimportant.
- `id` is what `mark` takes.

---

## `add`

Add a source **the operator asked for**. Never run it on your own initiative —
`check` does not propose sources and you do not search for them. The url is
fetched, parsed and gated before it becomes a row.

```bash
news-monitor add --url https://example.com/markets.rss --category markets --note "Wire markets desk"
news-monitor add --url https://example.com/markets.rss --dry-run
```

| flag | effect |
|---|---|
| `--url` | required |
| `--name` | defaults to a slug of the feed's own title, uniquified |
| `--category` | the bucket it reports under; defaults to `general` |
| `--note` | one line on what it covers; reaches you verbatim in `feeds` |
| `--enable` / `--disable` | override the gate's verdict |
| `--dry-run` | report the verdict without writing a row |

```json
{
  "ok": true,
  "url": "https://example.com/markets.rss",
  "name": "example-markets",
  "category": "markets",
  "feed_title": "Example Markets",
  "gate": {
    "verdict": "pass",
    "passed": true,
    "items": 25,
    "finance_hits": ["market", "yield", "earnings", "investor"],
    "newest_item_at": "2026-09-19T06:00:00+00:00",
    "sector_hints": ["macro-rates", "banks"],
    "gate": {"min_items": 3, "max_age_days": 30, "min_finance_hits": 3}
  },
  "enabled": true,
  "overridden": false,
  "stored": true
}
```

`verdict` is `pass`, or `thin` / `stale` / `off_topic` — all three of which are
**stored disabled**, not rejected. Unreachable, not-a-feed and already-tracked
are `ERR_CANDIDATE` and store nothing; `detail.reason` says which.

See `references/sources.md` for what each test actually rejects.

---

## `feeds` (alias `list`)

```bash
news-monitor feeds
news-monitor feeds --enabled --category central-bank
```

One object per source: `name`, `url`, `category`, `note`, `enabled`, `origin`
(`seed`, or `added` for one added on request), `gate_verdict`, `last_ok_at`, `consecutive_failures`,
`last_error`, `recent_yield`, `seeded`, `items_seen`, `items_pending`,
`latest_seen_at`.

`enabled: false` is a paused source, not a deleted one. Nothing is ever deleted.

---

## `enable` / `disable`

```bash
news-monitor enable --feed example-markets
news-monitor disable --feed dj-markets
```

Takes a name or a url. Returns `changed: false` when the feed was already in
that state — not an error, and the right answer on a retry.

---

## `mark`

```bash
news-monitor mark --item 1183
news-monitor mark --all
```

Takes a row id or a fingerprint, repeatable. `--all` stamps every pending item —
use it only when the whole batch actually went out.

```json
{"ok": true, "marked": [1183], "already_marked": [], "pending_items": 0}
```

`already_marked` is not an error: a repeated mark after a retried send is
exactly right.

---

## `items`

```bash
news-monitor items --limit 20
news-monitor items --feed federal-reserve --pending
news-monitor items --since 2026-09-01
```

Newest first. `--pending` / `--reported` filter on the ledger; `--since` filters
on `first_seen_at`, not on the publisher's date.

---

## `runs`

```bash
news-monitor runs --limit 5
```

One row per check: `started_at`, `finished_at`, `status`, `feeds_checked`,
`entries_seen`, `items_new`, `items_returned`, `errors`. This is the liveness
surface — check it first when asked why nothing has come up.

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
