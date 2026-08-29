# Video summary

An agent skill plus the deterministic Python tools behind it. A check runs every
X hours over a configured list of YouTube channel feeds, stores what is new,
and fetches the transcript of anything it has not sent yet. The agent reads that
transcript and writes what a swing trader needs to know from it — instruments,
levels, claims and who made them — then sends it over Telegram as one message
with the link. It never decides what is new, and it never computes a number.

```
video-summary/
├── video_summary/                      # the package
│   ├── config/
│   │   ├── feeds.py                    # Feed, FeedConfig, load_config()
│   │   └── feeds.json                  # what is watched
│   ├── cli.py                          # python -m video_summary <command>
│   ├── settings.py                     # env overrides: paths, timeouts, pacing, proxy
│   ├── clock.py                        # the one place that reads the wall clock
│   ├── fetch.py                        # urllib + conditional GET (no HTTP stack)
│   ├── feed.py                         # YouTube Atom -> entries; short vs long
│   ├── transcript.py                   # caption track -> prose on disk
│   ├── check.py                        # one run: fetch, store, transcribe, release
│   └── db.py                           # SQLite: feed state, videos, run log
├── skills/video-summary/
│   ├── SKILL.md                        # what the agent loads
│   └── references/
│       ├── cli.md                      # full command surface and JSON shapes
│       └── feed-config.md              # feeds, transcripts, traps
├── docs/DESIGN.md                      # why it is shaped this way
├── tests/                              # pytest, no network, no clock
└── pyproject.toml
```

Some of the plumbing (`fetch.py`, `clock.py`, and the bones of `db.py`,
`check.py`, `settings.py` and `config/feeds.py`) is copied from `news-radar` in
this repo, following the convention that every bundle here is self-contained —
its own venv, its own database, installable on its own. Each copied module says
so in a header comment; fixes have to be carried across by hand.

## Install on Ubuntu

```bash
unzip video-summary.zip -d ~/projects/hermes/profile-stock-analyst
cd ~/projects/hermes/profile-stock-analyst/video-summary
uv sync
```

Point Hermes at the skill — either copy it into the agent's skills directory or
symlink it:

```bash
ln -s ~/projects/hermes/profile-stock-analyst/video-summary/skills/video-summary ~/.hermes/skills/video-summary
```

And expose the console script so the skill works from any working directory:

```bash
ln -s ~/projects/hermes/profile-stock-analyst/video-summary/.venv/bin/video-summary ~/.local/bin/video-summary
video-summary feeds
```

## One cron entry

```bash
cd ~/projects/hermes/profile-stock-analyst/video-summary && .venv/bin/video-summary check   # 0 */2 * * *
```

The cadence and the ledger are still independent, which is the property that
matters: `check` returns every video where `summarised_at IS NULL`, however many
runs have happened since. A missed run needs no catch-up, a caught-up run
repeats nothing, and the cron expression can change without touching anything
else — which is why no interval is restated in the config.

Two hours is a deliberate floor, not a limit: a conditional GET against an
unchanged feed costs a few hundred bytes, so the real constraint is how often
someone wants their phone to buzz.

## Configuration

| Variable | Default |
|---|---|
| `VIDEO_SUMMARY_DB` | `~/.local/share/hermes-video-summary/video_summary.db` |
| `VIDEO_SUMMARY_TRANSCRIPTS` | `~/.local/share/hermes-video-summary/transcripts` |
| `VIDEO_SUMMARY_CONFIG` | `video_summary/config/feeds.json` in the bundle |
| `VIDEO_SUMMARY_TZ` | `Asia/Hong_Kong` (a `timezone` key in the config wins) |
| `VIDEO_SUMMARY_TIMEOUT` | `20` seconds per request |
| `VIDEO_SUMMARY_RETRIES` | `2` |
| `VIDEO_SUMMARY_DELAY` | overrides `request_delay_seconds` from the config |
| `VIDEO_SUMMARY_PROXY` | unset; a proxy for caption fetching only — see below |

What is watched lives in `video_summary/config/feeds.json`. Whole-line `//`
comments are stripped before parsing, so the file can carry disabled examples.

```json
{
  "max_per_check": 5,
  "summary_char_cap": 800,
  "transcript_grace_minutes": 120,
  "exclude_shorts": true,
  "exclude": [],
  "feeds": [
    {
      "name": "diamond-nestegg",
      "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCnexoc6tvesvcCEzZhmI-Ag",
      "note": "US Treasuries, bond ladders, CDs, money market and fixed income mechanics",
      "max_items": 15,
      "enabled": true
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `url` | must be a real feed url; a channel *page* url is rejected at load time |
| `note` | what the channel covers — **passed to the agent**, unlike a `//` comment |
| `max_per_check` | videos handed over per run; the rest stay pending |
| `summary_char_cap` | what the agent is told to write to, per video |
| `transcript_grace_minutes` | how long a caption-less video is held before going out bare |
| `transcript` | `false` sends the title and the link only |
| `exclude_shorts` | `true` by default; `false` on a feed keeps its Shorts |
| `min_interval_minutes` | a floor on how often *this* feed is fetched, not a schedule |
| `enabled` | `false` pauses a feed without losing its history |

**A handle is not a channel id.** `@DiamondNestEgg` can be changed by its owner;
`UCnexoc6tvesvcCEzZhmI-Ag` is permanent, starts `UC`, and is 24 characters. Open
the channel page and read `channelId` out of the source. The config rejects
anything else, with that sentence in the error message.

The feed shipped in `feeds.json` was verified live on 2026-08-23. Verify each new
one with `video-summary check --feed <name> --dry-run` before enabling — full
field reference in `skills/video-summary/references/feed-config.md`.

## The four rules that make it work

**Identity is YouTube's.** `video_id` is the primary key and it is globally
unique, so a channel feed and a playlist feed carrying the same upload store —
and send — it once. This is the exact inverse of `news-radar`, where the same
story from two outlets is deliberately two items.

**The ledger is a column.** `summarised_at IS NULL` means pending. `check` never
stamps; `mark` does, per video, after the send has actually happened. A batch
stamped up front loses every video after a failure; a batch stamped at the end
re-sends the ones that already arrived. `mark` is idempotent, so a retry is safe.

**Seed.** A feed's first successful check stores its whole back catalogue already
stamped. Without it, adding a channel puts fifteen old videos into the next
message.

**Hold, then release.** YouTube generates captions *after* an upload. A video
with none yet is held for `transcript_grace_minutes` rather than sent as a bare
headline, then released regardless with `transcript.status` saying why there is
nothing to summarise. `max_transcript_attempts` stops a video with captions
genuinely disabled from being asked about every two hours forever.

## The transcript is a path, not a payload

A forty-minute video is a forty-thousand-character transcript. Five of those in
one wake-up is two hundred thousand characters of context for a model that will
write four thousand.

So the tools flatten the caption cues into prose, write them to
`$VIDEO_SUMMARY_TRANSCRIPTS/<video_id>.txt`, and hand the agent a **path**. The
agent opens only what it is about to write about. That single decision is what
makes a two-hourly cron entry affordable.

A manual caption track always beats an auto-generated one, across languages:
machine captions mangle exactly the words this desk cares about — tickers, basis
points, "the two-year".

## IpBlocked, and the fallback

`youtube-transcript-api` is the code path for the same material the agent's
`youtube-content` skill would fetch. A skill is instructions for a model, though,
not something a subprocess can call — hence the library.

YouTube refuses caption requests from most cloud provider address space, and from
any address that has asked too often. It arrives as `transcript.status: "error"`
with `IpBlocked` in the message, on *every* video rather than one. The feed
itself is unaffected — different path, public document — so the watcher keeps
working and only the summaries go missing.

Two remedies, in order: set `VIDEO_SUMMARY_PROXY`, which is wired into the
caption client and nowhere else; or let the agent fall back to the
`youtube-content` skill per video, which SKILL.md tells it to do for any
non-`ok` status. Retrying without either changes nothing.

## Silence has three causes, and they must be distinguishable

- **Not running** — every check writes a `runs` row, including failures.
  `video-summary runs` is the liveness check.
- **A dead or moved channel** — `feed_state.recent_yield` remembers what a feed
  returned on its last successful check, so one that used to yield and now
  yields nothing is reported `zero_yield`, not silence. A feed that has never
  yielded is treated as quiet, which is why `--dry-run` before enabling is not
  optional.
- **Genuinely nothing posted** — the normal case, and the only one that gets no
  message.

## Data model

SQLite at `$VIDEO_SUMMARY_DB`. Full DDL in `references/cli.md`.

`video` — every video ever seen, with its transcript state alongside it.
`summarised_at` **is** the ledger. `published_text` is stored as YouTube wrote it
and never parsed: nothing here needs the value, so parsing it would only create a
way to be wrong.

`feed_state` — per feed: conditional-GET validators, failure streak, seeded flag,
last non-zero yield, and `last_check_at` (which the throttle reads).

`runs` — one row per check, written even when everything fails. The agent's whole
triage surface; the skill never parses stdout.

**There is no summaries table**, because the summary is the model's sentence and
this bundle cannot check it. There is no Telegram module either: Hermes owns the
channel, exactly as in `news-radar` and `coupon-tracker`. Nothing is ever
deleted.

## Tests

```bash
uv run pytest -q
```

24 tests, no network and no wall clock. The feed document, the redirect that
distinguishes a Short, and the caption track are all injected; every function
that needs the time is handed it.

The ones that matter most:

1. **a cold start is silent**, and the second check reports exactly what is new;
2. **the ledger stops the repeat** — an unmarked video comes round again, which
   is correct after a failed send; a marked one never does;
3. **hold then release** — a caption-less video is withheld, and is released with
   `unavailable` once the grace period has passed;
4. **one dead feed does not take the others down** — the run finishes `partial`
   and the healthy feed's videos are still stored;
5. **the same video in two feeds is one video**;
6. **entities are decoded** — feeds double-escape, and `Yield&#8217;s` left alone
   reaches the reader as mojibake.

## Limitations

- **No duration, and no Short flag without a request.** The feed carries
  neither, so one redirect per new video resolves it. Failures land as
  `kind: "unknown"`, which is never excluded — a video is never lost to a label
  that could not be resolved.
- **Auto-generated captions are frequently wrong** about tickers and numbers that
  sound alike. The agent is told to say the transcript is unclear rather than
  guess; nothing here can do better.
- **A video with no captions at all gets no summary.** It goes out as a title
  and a link, and says so.
- **Videos are ordered by when they were first seen**, not by publication time,
  because `published_text` is never parsed.
