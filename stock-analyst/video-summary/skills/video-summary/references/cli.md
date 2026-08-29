# Command surface and JSON shapes

Every command prints one indented JSON object on stdout. Success is
`{"ok": true, ...}` and exit 0. Failure is:

```json
{
  "ok": false,
  "error": "ERR_CONFIG",
  "exit_code": 10,
  "message": "feed 'rates-desk': channel_id '@somebody' does not look like a channel id ...",
  "detail": {}
}
```

| code | error | meaning |
|---|---|---|
| 0 | — | fine |
| 10 | `ERR_CONFIG` | `feeds.json` is malformed, or names a feed that does not exist |
| 11 | `ERR_DB` | the database could not be opened or written |
| 20 | `ERR_FETCH` | no feed could be reached at all |
| 22 | `ERR_TRANSCRIPT` | `transcript --refresh` was asked for one video and could not produce it |
| 30 | `ERR_NOT_FOUND` | no video with that id, no feed with that name |

A *per-feed* failure during a check is none of these. It goes into
`feed_failures`, the other feeds still run, and the command exits 0.

## check

The cron entry, and the only command that produces something to send.

```bash
video-summary check                       # every enabled feed
video-summary check --feed rates-desk     # repeatable
video-summary check --limit 3             # hand over at most 3 this run
video-summary check --dry-run             # show what a feed yields; write nothing
video-summary check --seed                # absorb what is published now, silently
video-summary check --no-transcript       # skip caption fetching this run
video-summary check --ignore-throttle     # fetch even inside min_interval_minutes
```

```json
{
  "ok": true,
  "status": "ok",
  "run_id": 41,
  "dry_run": false,
  "now": "2026-08-23T14:00:00+08:00",
  "summary_char_cap": 800,
  "feeds": [
    {
      "feed": "diamond-nestegg",
      "status": "ok",
      "seeding": false,
      "entries_seen": 15,
      "videos_new": 1,
      "excluded": 0,
      "shorts_excluded": 2,
      "candidates": []
    }
  ],
  "feed_failures": [],
  "totals": {
    "feeds_checked": 1, "entries_seen": 15, "videos_new": 1, "videos_excluded": 2,
    "videos_excluded_keyword": 0, "videos_excluded_shorts": 2,
    "errors": 0, "transcripts_ok": 1, "transcripts_failed": 0, "transcripts_attempted": 1
  },
  "videos": [
    {
      "id": 16,
      "video_id": "XDw4kSwQfP4",
      "feed": "diamond-nestegg",
      "feed_note": "US Treasuries, bond ladders, CDs, money market and fixed income mechanics",
      "channel": "Diamond NestEgg",
      "title": "A \"Golden\" Moment For Guaranteed Lifetime Income?",
      "url": "https://www.youtube.com/watch?v=XDw4kSwQfP4",
      "thumbnail_url": "https://i1.ytimg.com/vi/XDw4kSwQfP4/hqdefault.jpg",
      "kind": "video",
      "published_text": "2026-08-22T20:00:37+00:00",
      "first_seen_at": "2026-08-23T14:00:08+08:00",
      "summarised_at": null,
      "transcript": {
        "status": "ok",
        "path": "/home/you/.local/share/hermes-video-summary/transcripts/XDw4kSwQfP4.txt",
        "chars": 38211,
        "language": "en",
        "attempts": 1,
        "error": null
      }
    }
  ],
  "held_for_transcript": 0,
  "pending_videos": 0
}
```

Run `status` is `ok` | `partial` | `skipped` | `error`. Per-feed `status` is
`ok` | `unchanged` (304) | `throttled` | `zero_yield` | `error`. A throttled
feed also carries `next_eligible`; a `zero_yield` one carries a `message`.

`videos` is what to send, oldest first, at most `max_per_check` (5). It is
**never stamped by `check`** — see `mark`.

`thumbnail_url` is returned for triage and is **not sent**. One `sendMessage`
carrying the `url` is the whole delivery: Telegram renders its own preview card
from the link. There is no `sendPhoto` step.

`held_for_transcript` counts pending videos whose captions have not appeared
yet. They are not in `videos` and need no comment.

`candidates` is populated only by `--dry-run`, and carries `video_id`, `title`,
`url`, `thumbnail_url`, `published_text`, `kind`, `excluded_as_short` and
`would_send`. A dry run resolves `kind`, so it tells the truth about what the
Shorts filter would drop — which is the point of running it before enabling a
feed.

`excluded` counts the keyword filter; `shorts_excluded` counts Shorts dropped by
`exclude_shorts`. Both are dropped **before** storage, so neither appears in
`videos` and neither costs a transcript fetch. `totals.videos_excluded` is the
sum of the two, matching the `runs` column; the two `videos_excluded_*` keys
break it down.

`feed_failures` entries carry `feed`, `reason` (`fetch_failed` | `parse_failed`
| `zero_yield`) and `message`.

## mark

The ledger. Run it after a video has actually reached Telegram.

```bash
video-summary mark --video XDw4kSwQfP4          # repeatable; row id also accepted
video-summary mark --all                        # every pending video
```

```json
{"ok": true, "marked": ["XDw4kSwQfP4"], "already_marked": [], "pending_videos": 0}
```

`already_marked` is not an error: a repeated mark after a retried send is
exactly right. `--all` is for the case where the whole batch demonstrably went
out; prefer one call per video.

## feeds (alias: list)

```bash
video-summary feeds
video-summary list --all      # include disabled
```

Returns the config as loaded — `timezone`, `max_per_check`, `summary_char_cap`,
`transcript_languages`, `transcript_grace_minutes`, `exclude_shorts`,
`exclude` —
plus one entry per feed combining its config (`name`, `url`, `channel_id`,
`note`, `transcript`, `min_interval_minutes`, `max_items`, `enabled`) with its
health: `seeded`, `last_ok_at`, `consecutive_failures`, `last_error`,
`recent_yield`, `videos_seen`, `videos_pending`, `latest_seen_at`,
`next_eligible`.

This is also the config validator. A malformed `feeds.json` fails here with
`ERR_CONFIG` and the offending field named.

## videos

```bash
video-summary videos --limit 20
video-summary videos --pending
video-summary videos --summarised
video-summary videos --feed diamond-nestegg
video-summary videos --since 2026-08-01
```

Newest first. Each row is the same shape as an entry in `check`'s `videos`,
without `feed_note`.

## transcript

```bash
video-summary transcript --video XDw4kSwQfP4              # where it is, and how it went
video-summary transcript --video XDw4kSwQfP4 --refresh    # fetch again; fails loudly
video-summary transcript --video XDw4kSwQfP4 --text --max-chars 8000
```

`--refresh` is the one place a transcript failure is fatal (`ERR_TRANSCRIPT`),
because the caller asked for exactly this one thing. Prefer the `path` over
`--text`: the file is on the same machine and reading it costs nothing.

## runs

```bash
video-summary runs --limit 5
```

One row per check, including the failures: `started_at`, `finished_at`,
`status`, `feeds_checked`, `entries_seen`, `videos_new`, `videos_excluded`,
`transcripts_ok`, `transcripts_failed`, `errors`, `detail`. Plus the current
`pending_videos`.

## SQLite schema (read-only reference)

```sql
CREATE TABLE feed_state (
  feed                  TEXT PRIMARY KEY,
  first_seen_at         TEXT NOT NULL,
  last_check_at         TEXT,          -- what the per-feed throttle reads
  last_ok_at            TEXT,
  etag                  TEXT,
  last_modified         TEXT,
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  seeded                INTEGER NOT NULL DEFAULT 0,
  recent_yield          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE video (
  id                   INTEGER PRIMARY KEY,
  video_id             TEXT NOT NULL UNIQUE,   -- YouTube's own id
  feed                 TEXT NOT NULL,          -- the feed that saw it first
  channel              TEXT,
  channel_url          TEXT,
  title                TEXT NOT NULL,
  url                  TEXT NOT NULL,
  thumbnail_url        TEXT,
  kind                 TEXT NOT NULL DEFAULT 'unknown',   -- short | video | unknown
  published_text       TEXT,                   -- YouTube's own words, never parsed
  description          TEXT,
  first_seen_at        TEXT NOT NULL,
  transcript_status    TEXT NOT NULL DEFAULT 'pending',
  transcript_path      TEXT,
  transcript_chars     INTEGER,
  transcript_lang      TEXT,
  transcript_error     TEXT,
  transcript_attempts  INTEGER NOT NULL DEFAULT 0,
  summarised_at        TEXT,                   -- the ledger; NULL means pending
  run_id               INTEGER
);

CREATE TABLE runs (
  id                  INTEGER PRIMARY KEY,
  started_at          TEXT NOT NULL,
  finished_at         TEXT,
  status              TEXT NOT NULL,           -- ok | partial | skipped | error
  feeds_checked       INTEGER NOT NULL DEFAULT 0,
  entries_seen        INTEGER NOT NULL DEFAULT 0,
  videos_new          INTEGER NOT NULL DEFAULT 0,
  videos_excluded     INTEGER NOT NULL DEFAULT 0,   -- keyword + Shorts, both dropped pre-storage
  transcripts_ok      INTEGER NOT NULL DEFAULT 0,
  transcripts_failed  INTEGER NOT NULL DEFAULT 0,
  errors              INTEGER NOT NULL DEFAULT 0,
  detail              TEXT
);
```

**`video_id` is globally unique, not scoped by feed.** A channel feed and a
playlist feed carrying the same upload are the same upload, and the second feed
to see it simply does not insert it. That is the difference from `news-radar`,
where two outlets carrying one story are genuinely two items.

**There is no summaries table.** The summary is written by the model and sent
over Telegram; storing a copy would make this bundle the owner of something it
cannot check. `summarised_at IS NULL` is the whole ledger, which is why a missed
cron run costs nothing and a caught-up one repeats nothing. Nothing is ever
deleted.
