# Command surface and JSON shapes

Every command prints one indented JSON object on stdout. Success is
`{"ok": true, ...}` and exit 0. Failure is:

```json
{
  "ok": false,
  "error": "ERR_CONFIG",
  "exit_code": 10,
  "message": "source 'verge-ai': category 'Ai' is not declared in 'categories' (['ai', 'world'])",
  "detail": {}
}
```

| code | error | meaning |
|---|---|---|
| 0 | — | fine |
| 10 | `ERR_CONFIG` | `sources.json` is malformed, or names a source or category that does not exist |
| 11 | `ERR_DB` | the database could not be opened or written |
| 20 | `ERR_FETCH` | the only source asked for could not be reached |
| 21 | `ERR_BROWSER` | a source needs `"render": "browser"` and Chromium is not usable |
| 30 | `ERR_NOT_FOUND` | no item with that id |

A *per-source* failure during a scan is none of these. It goes into
`source_failures`, the other sources still run, and the command exits 0.

## scan

Collects. Never reports anything.

```bash
news-radar scan                             # every enabled source
news-radar scan --source verge-ai           # repeatable
news-radar scan --category ai               # repeatable
news-radar scan --dry-run                   # show what was caught; write nothing
news-radar scan --seed                      # absorb what is published now, silently
news-radar scan --ignore-throttle           # fetch even inside min_interval_minutes
```

```json
{
  "ok": true,
  "status": "ok",
  "run_id": 41,
  "dry_run": false,
  "now": "2026-08-11T14:00:00+08:00",
  "sources": [
    {
      "source": "verge-ai",
      "category": "ai",
      "status": "ok",
      "seeding": false,
      "items_seen": 10,
      "items_new": 2,
      "excluded": 0,
      "candidates": []
    }
  ],
  "source_failures": [],
  "totals": {"sources_scanned": 5, "items_seen": 99, "items_new": 4,
             "items_excluded": 1, "errors": 0},
  "pending_items": 4
}
```

Run `status` is `ok` | `partial` | `skipped` | `error`. Per-source `status` is
`ok` | `unchanged` (304) | `throttled` | `zero_yield` | `error`. A throttled
source also carries `next_eligible`.

`candidates` is populated only by `--dry-run`, and carries `title`, `url`,
`summary`, `date_text`, `source_domain` and `would_digest`.

`source_failures` entries carry `source`, `reason` (`fetch_failed` |
`extract_failed` | `zero_yield`) and `message`.

`pending_items` is how a scheduler decides whether running a digest is worth it.

## digest

Reports. This is the only command that produces something to send.

```bash
news-radar digest                    # read-only peek; stamps nothing
news-radar digest --commit           # stamp them as sent, then return them
news-radar digest --category ai      # repeatable
news-radar digest --limit 30
news-radar digest --text             # also include a ready-to-send 'body' string
```

```json
{
  "ok": true,
  "as_of": "2026-08-11T18:00:00+08:00",
  "since": "2026-08-11T09:00:00+08:00",
  "count": 14,
  "stories": 11,
  "committed": 14,
  "sections": [
    {
      "category": "ai",
      "label": "AI",
      "stories": [
        {
          "ids": [88, 91, 94],
          "title": "OpenAI releases GPT-X",
          "url": "https://www.theverge.com/2026/08/11/openai-gpt-x",
          "sources": ["theverge.com", "techcrunch.com", "arstechnica.com"],
          "summary": "The model is available today.",
          "published_text": "Mon, 11 Aug 2026 09:00:00 +0800"
        }
      ]
    }
  ],
  "totals": {"items": 14, "stories": 11, "sources": 6, "categories": 3}
}
```

`sections` is in the order the `categories` list declares. A category with
nothing new is omitted, not printed empty. `since` is the oldest pending item's
`first_seen_at`, or `null` when there is nothing.

A section with `"category": "uncategorised"` and a `note` holds items whose
source has been removed from `sources.json` — they surface instead of vanishing.

`count` is items; `stories` is what a reader sees after clustering. Without
`--commit`, `committed` is 0 and nothing is stamped.

## sources

```bash
news-radar sources
news-radar sources --all      # include disabled
```

Returns the config as loaded — `timezone`, `request_delay_seconds`,
`detail_budget`, `cluster_threshold`, `categories`, `exclude` — plus one entry
per source combining its config with its health: `seeded`, `last_ok_at`,
`consecutive_failures`, `last_error`, `recent_yield`, `next_eligible`.

This is also the config validator. A malformed `sources.json` fails here with
`ERR_CONFIG` and the offending field named.

## items

```bash
news-radar items --limit 20
news-radar items --category ai
news-radar items --source verge-ai
news-radar items --since 2026-08-01
```

Newest first. Each row: `id`, `source`, `source_domain`, `category`, `title`,
`url`, `summary`, `date_text`, `first_seen_at`, `digested_at`. Unclustered —
this is the raw record, not the digest.

## runs

```bash
news-radar runs --limit 5
```

One row per scan, including the failures: `started_at`, `finished_at`, `status`,
`sources_scanned`, `items_seen`, `items_new`, `items_excluded`, `errors`,
`detail`. Plus the current `pending_items`.

## SQLite schema (read-only reference)

```sql
CREATE TABLE site_state (
  source                TEXT PRIMARY KEY,
  first_seen_at         TEXT NOT NULL,
  last_scan_at          TEXT,          -- what the per-source throttle reads
  last_ok_at            TEXT,
  etag                  TEXT,
  last_modified         TEXT,
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  seeded                INTEGER NOT NULL DEFAULT 0,
  recent_yield          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE item (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,
  item_key       TEXT NOT NULL,   -- sha1(source|canonical url|folded title)
  url            TEXT NOT NULL,
  title          TEXT NOT NULL,
  summary        TEXT,
  detail_text    TEXT,
  date_text      TEXT,            -- the source's own words, never parsed
  source_domain  TEXT NOT NULL,   -- the digest's label
  first_seen_at  TEXT NOT NULL,
  digested_at    TEXT,            -- the ledger; NULL means pending
  run_id         INTEGER,
  UNIQUE (source, item_key)
);

CREATE TABLE runs (
  id               INTEGER PRIMARY KEY,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  status           TEXT NOT NULL,   -- ok | partial | skipped | error
  sources_scanned  INTEGER NOT NULL DEFAULT 0,
  items_seen       INTEGER NOT NULL DEFAULT 0,
  items_new        INTEGER NOT NULL DEFAULT 0,
  items_excluded   INTEGER NOT NULL DEFAULT 0,
  errors           INTEGER NOT NULL DEFAULT 0,
  detail           TEXT
);
```

**There is no category column on `item`, and no cluster table.** The category is
read from the source's current config at digest time, so recategorising applies
to everything not yet sent. Clusters are computed per digest and never stored,
because membership depends on which items happen to be pending together.

**There is no `digests` table either.** An item is pending when `digested_at IS
NULL`, which is the whole reason `scan` and `digest` can run on unrelated
schedules. Nothing is ever deleted.
