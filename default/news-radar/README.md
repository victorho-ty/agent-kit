# Hermes news radar

An agent skill plus the deterministic Python tools behind it. A scan runs
continuously over a configured list of news sources and stores what is new; a
digest, on its own schedule, reports everything published since the last one —
clustered so a story carried by five outlets reads as one line, and grouped into
the categories the household assigned to each source. The agent turns that into
a message; it never decides what is new.

```
news-radar/
├── news_radar/                         # the package
│   ├── config/
│   │   ├── sources.py                  # Source, Category, load_config()
│   │   └── sources.json                # what is watched, and under which heading
│   ├── cli.py                          # python -m news_radar <command>
│   ├── settings.py                     # env overrides: db path, timeouts, pacing
│   ├── clock.py                        # the one place that reads the wall clock
│   ├── fetch.py                        # urllib + conditional GET (no HTTP stack)
│   ├── render.py                       # Chromium, lazily, for "render": "browser"
│   ├── extract.py                      # page -> candidates (rss / html / regex)
│   ├── scan.py                         # one run: fetch, extract, remember
│   ├── cluster.py                      # many items -> one story
│   ├── digest.py                       # pending items -> sections, per category
│   └── db.py                           # SQLite: source state, items, run log
├── skills/news-radar/
│   ├── SKILL.md                        # what the agent loads
│   └── references/
│       ├── cli.md                      # full command surface and JSON shapes
│       └── source-config.md            # sources, categories, clustering, traps
├── docs/DESIGN.md                      # why it is shaped this way
├── tests/                              # pytest, no network, no clock
└── pyproject.toml
```

Much of the plumbing (`fetch.py`, `render.py`, `extract.py`, `clock.py`, and the
bones of `db.py`, `scan.py` and `settings.py`) is copied from `education-radar`
in this repo, following the convention that every bundle here is
self-contained — its own venv, its own database, installable on its own. Each
copied module says so in a header comment; fixes have to be carried across by
hand.

## Install on Ubuntu

```bash
unzip news-radar.zip -d ~/projects/hermes
cd ~/projects/hermes/news-radar
uv sync
uv run playwright install --with-deps chromium   # only for "render": "browser" sources
```

Point Hermes at the skill — either copy it into the agent's skills directory or
symlink it:

```bash
ln -s ~/projects/hermes/news-radar/skills/news-radar ~/.hermes/skills/news-radar
```

And expose the console script so the skill works from any working directory:

```bash
ln -s ~/projects/hermes/news-radar/.venv/bin/news-radar ~/.local/bin/news-radar
news-radar sources
```

## Two cron entries, two independent cadences

This is the central design decision, so it goes first.

```bash
cd ~/projects/hermes/news-radar && .venv/bin/news-radar scan             # hourly
cd ~/projects/hermes/news-radar && .venv/bin/news-radar digest --commit  # 08:00, 18:00
```

`scan` collects and says nothing — no message, no model call, no tokens. It can
run as often as you like. `digest --commit` is the only thing that produces
something to send.

They are decoupled through the **database**, not through timing: a digest takes
every item where `digested_at IS NULL`, however many scans happened since. That
buys three properties:

- a **missed scan** needs no catch-up — "new" is defined by `item_key` against
  the table, not by a time range;
- a **missed digest** loses nothing; the next one is simply longer;
- **changing either cadence** is a one-line cron edit that cannot desynchronise
  the other, which is why no interval is restated in the config.

Scanning often matters for a reason beyond freshness: **many feeds only expose
the last N entries**. If a busy source publishes 15 items between two scans and
its feed holds 10, five are gone for good — nothing downstream can recover what
was never seen.

`news-radar digest` without `--commit` is a read-only peek for "anything new
right now"; it stamps nothing, so it does not consume the next scheduled digest.

## Configuration

| Variable | Default |
|---|---|
| `NEWS_RADAR_DB` | `~/.local/share/hermes-news-radar/news_radar.db` |
| `NEWS_RADAR_CONFIG` | `news_radar/config/sources.json` in the bundle |
| `NEWS_RADAR_TZ` | `Asia/Hong_Kong` (a `timezone` key in the config wins) |
| `NEWS_RADAR_TIMEOUT` | `20` seconds per request |
| `NEWS_RADAR_RETRIES` | `2` |
| `NEWS_RADAR_DELAY` | overrides `request_delay_seconds` from the config |
| `NEWS_RADAR_HEADLESS` | `1`; set `0` to watch the browser work |

What is watched lives in `news_radar/config/sources.json`. Whole-line `//`
comments are stripped before parsing, so the file can carry disabled examples.

```json
{
  "cluster_threshold": 0.7,
  "categories": [
    { "name": "ai", "label": "AI" },
    { "name": "world", "label": "World" }
  ],
  "exclude": ["sponsored", "newsletter signup"],
  "sources": [
    { "name": "verge-ai", "category": "ai", "kind": "rss",
      "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
      "max_items": 30, "enabled": true },
    { "name": "slow-blog", "category": "ai", "kind": "rss",
      "url": "https://example.dev/feed",
      "min_interval_minutes": 360, "enabled": false }
  ]
}
```

| Field | Meaning |
|---|---|
| `categories` | the digest's sections, **in display order** |
| `category` | **required on every source**; must be one of the declared categories |
| `exclude` | drops an item outright; the only filter there is |
| `kind` | `rss` (no selectors, immune to a redesign), `html`, or `regex` |
| `min_interval_minutes` | a floor on how often *this* source is fetched, not a schedule |
| `follow_detail` | fetch each new item's own page; leave off for news |
| `enabled` | `false` pauses a source without losing its history or dedupe memory |

There is no include list. You already said what a source is about by giving it a
category, so everything it publishes is in scope.

The five sources shipped in `sources.json` were verified live on 2026-08-11.
Replace them with your own, and verify each with
`news-radar scan --source <name> --dry-run` before enabling — full field
reference in `skills/news-radar/references/source-config.md`.

## The three rules that make it work

**Cluster.** Two items are one story when their canonical URLs match, or when
their headlines overlap by at least `cluster_threshold` — shared significant
words over the *smaller* headline's count. The overlap coefficient rather than
Jaccard, because Jaccard divides by the union and so punishes a headline for
being long even when it contains the other whole: "OpenAI releases GPT-X" vs
"OpenAI Releases GPT-X, Its Biggest Model Yet" scores 0.5 under Jaccard and 1.0
here. Two limits, both deliberate: clustering happens **within a category** and
**within one digest**.

**Dedupe.** An item's identity within a source is
`sha1(source | canonical url | folded title)`. Title *and* URL, because either
alone is wrong. Scoped by source on purpose — the same story from two outlets is
two items, and recognising them as one story is the digest's job. Doing it at
scan time would mean a rewording on one outlet suppressed the story everywhere.

**Seed.** A source's first successful scan stores everything already published
with `digested_at` pre-stamped. Without it, adding a source puts its entire back
catalogue into the next digest.

## Silence has three causes, and they must be distinguishable

- **Not running** — every scan writes a `runs` row, including failures.
  `news-radar runs` is the liveness check.
- **A moved feed or a dead selector** — `site_state.recent_yield` remembers what
  a source returned on its last successful scan, so one that used to yield and
  now yields nothing is reported `zero_yield`, not silence. A source that has
  never yielded is treated as quiet, which is why `--dry-run` before enabling
  is not optional.
- **Genuinely nothing** — the normal case, and the only one that gets no message.

## Being a good guest

Sources are fetched with a conditional GET: each one's `ETag` and
`Last-Modified` go back on the next scan, so a feed that publishes twice a day
answers `304 Not Modified` to the other twenty-two hourly scans. That is what
makes continuous scanning affordable and polite at the same time. Requests are
paced by `request_delay_seconds`, `min_interval_minutes` puts a floor under any
source that deserves one, and a 4xx is never retried — it means the URL is
wrong, and hammering it will not make it right.

## Data model

SQLite at `$NEWS_RADAR_DB`. Full DDL in `references/cli.md`.

`item` — every candidate ever seen. `digested_at` **is** the ledger, which is
what lets the two cron entries run on unrelated schedules. `date_text` is stored
as the source wrote it and never parsed: parsing would let us sort by recency,
at the price of occasionally announcing a confidently wrong date.

There is no `category` column and no cluster table. Categories are read from the
source's current config at digest time, so recategorising a source moves
everything of its that has not gone out yet. Clusters are computed per digest
and never stored, because membership depends on which items happen to be pending
together — storing it would be recording an accident.

`site_state` — per source: conditional-GET validators, failure streak, seeded
flag, last non-zero yield, and `last_scan_at` (which the throttle reads).

`runs` — one row per scan, written even when everything fails. The agent's whole
triage surface; the skill never parses stdout.

There is no Telegram module anywhere in this package. Hermes owns the channel.
Nothing is ever deleted.

## Tests

```bash
uv run pytest -q
```

66 tests, no network and no wall clock. `conftest.FakeWeb` serves captured feeds
and honours conditional GET; every function that needs the time is handed it.

The tests that matter most:

1. **clustering, from both sides** — rewordings of one story must merge, and
   stories that merely share a subject ("OpenAI releases GPT-X" vs "OpenAI hires
   a new CFO") must not. Those pull in opposite directions and are the reason
   the threshold is configurable;
2. **the ledger decouples the crons** — three scans then one digest returns
   everything since the last digest, `--commit` is idempotent, and a read
   without `--commit` consumes nothing;
3. **the throttle is a floor, not a schedule** — a source inside its interval is
   skipped while the others in the same run scan normally;
4. **sections follow the config** — order matches the `categories` list, empty
   categories are omitted, clustering does not reach across sections, and a
   deleted source's items surface under `uncategorised` instead of vanishing;
5. **a first scan reports nothing**, and a feed that goes empty is `zero_yield`
   rather than silence;
6. **entities are decoded** — feeds double-escape, and `Zuckerberg&#8217;s`
   left alone is both mojibake and a broken clustering token.

Fixtures are shaped around specific traps and each says so at the top:
`alpha.xml` and `beta.xml` carry the same story reworded (and a sponsored post,
and a double-escaped entity); `beta.xml` also carries a different story sharing
a subject with it; `gamma.xml` repeats that story in another category to prove
sections stay apart.

## Limitations

- Clustering is within one digest and within one category (see above).
- Headlines are compared, not article bodies. Two outlets writing genuinely
  different headlines for one story will not merge.
- `date_text` is never parsed, so stories cannot be ordered by publication time
  — they appear in the order they were first seen.
