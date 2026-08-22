# Command surface and JSON shapes

Every command prints one JSON object on stdout and exits with a code from the
closed set below. The single exception is `pending --count`, which prints a bare
integer so a shell can test it without a JSON parser.

## Exit codes

| code | name | meaning |
|---|---|---|
| 0 | `OK` | |
| 10 | `ERR_CONFIG` | `watchlist.json` is malformed or names something unknown |
| 11 | `ERR_DB` | the database could not be opened or written |
| 20 | `ERR_FETCH` | the only requested ticker could not be reached |
| 21 | `ERR_INSUFFICIENT` | too few bars for what was asked |
| 22 | `ERR_CHART` | the renderer failed, or the chart directory is unwritable |
| 30 | `ERR_NOT_FOUND` | no such ticker, position or run |

A failure payload is `{"ok": false, "error", "exit_code", "message", "detail"}`.

`ERR_FETCH` and `ERR_INSUFFICIENT` are deliberately distinct: the first means the
network failed and retrying may help, the second means the listing is genuinely
young and only waiting will.

**Per-ticker failures are not exit codes.** One unreachable ticker must never
abort a scan of the other nine, so those collect into `failures` and the run
finishes `partial`.

## Run status

`ok` every ticker answered · `partial` some did not, see `failures` ·
`error` none did · `skipped` nothing matched the request.

---

## Silent commands

These write to the database and report to nobody. Safe to run on any cadence.

### `stockctl sync [--ticker T]... [--full] [--fundamentals]`

Incremental daily bars. Only days after the newest stored bar are fetched, so a
first sync pulls two years and every sync after it pulls one bar per ticker. The
newest stored bar is always re-fetched, because a bar written while the market
was open is provisional.

Without `--ticker`, syncs the enabled watchlist plus every open position, and
refreshes fundamentals too.

```json
{"status": "ok", "tickers": 2, "stored": 3, "degraded": [], "failures": [],
 "results": [{"ticker": "NVDA", "status": "ok", "stored": 2, "source": "yfinance",
              "latest_bar": "2026-08-14", "degraded": false, "error": null}]}
```

`degraded` lists tickers served by the Stooq fallback because yfinance failed.
Prices are usable; a persistent entry is worth reporting.

### `stockctl news poll [--av-budget N]`

Reads the Yahoo Finance feed for every enabled ticker and each declared
competitor, then — if `--av-budget` allows it — Alpha Vantage `news_sentiment`
for the watchlist tickers. Both come through MCP servers that **Python** drives;
nothing here crosses model context. Every item passes a subject gate and the
materiality classifier before it is stored.

`--av-budget` is a hard ceiling, not a hint. The free tier allows 25 calls a day
for the whole profile, shared with macro. Default 0 means Yahoo only, which is a
complete poll carrying no sentiment scores.

```json
{"status": "ok", "feeds": ["yahoo", "alphavantage"], "yahoo_requests": 14,
 "alphavantage_calls": 6, "seen": 210, "new": 24, "suppressed": 22,
 "suppressed_by_class": {"noise": 36, "price_move": 13, "unclassified": 13},
 "absorbed": 0, "seeded_tickers": [], "failures": []}
```

`seen` minus `new` is the dedupe working, not a fault. `suppressed` counts rows
stored but never reportable; `suppressed_by_class` is the audit trail for what
the filter swallowed. An `unclassified` count climbing run over run means a
pattern is missing, not that the news went quiet.

`absorbed` counts rows stored **and stamped as reported on the way in** — a
ticker's first poll, which is silent by design. `seeded_tickers` names the
tickers that got that treatment. On a cold start expect `new: 0` and a large
`absorbed`: a back catalogue is not news.

### `stockctl events refresh`

Earnings and ex-dividend dates for the watchlist and open positions.

---

## The gate

### `stockctl pending [--ticker T]... [--count]`

How many unreported items exist. Counts only — it does not load, cluster or
render anything, because answering "is there anything" must be cheap.

```bash
stockctl pending --count   # -> 238
```

```json
{"ok": true, "pending": 238, "news": 238, "events": 0}
```

---

## The scheduled runs

### `stockctl run {morning-hkt|pre-us-open} [--commit]`

**The only command cron invokes.** Runs sync, events, news, macro and report in
order and returns the whole payload. The sequencing lives here rather than in a
skill instruction so it cannot be followed differently on a Tuesday.

```json
{"ok": true, "quiet": false, "setups": [...], "status_lines": [...],
 "fresh_news": [...], "sectors": [...], "macro": {...}, "events": [...],
 "run": {"profile": "morning-hkt", "label": "09:00 HKT — the daily digest",
         "alphavantage_calls_spent": 9, "alphavantage_budget": 9,
         "news_since": "2026-08-21T09:00:00+08:00",
         "steps": {"sync": {...}, "events": {...}, "news": {...}, "macro": {...}},
         "degraded": false, "failures": []}}
```

`--commit` stamps news, events and macro as reported. Run it, then send.

`run.degraded` true means a feed failed; `run.failures` names which step and
why. Credentials are scrubbed from those strings — Alpha Vantage quotes the API
key back inside its own quota message, and that text would otherwise travel into
the report and out to Telegram.

Budgets, per profile:

| profile | news calls | macro calls |
|---|---|---|
| `morning-hkt` | 6 | 3 |
| `pre-us-open` | 6 | 2 |

Seventeen of the daily 25, leaving eight for whatever is asked by hand.

---

## Reporting commands

### `stockctl report [--commit]`

The daily watchlist report. See SKILL.md for how to read it.

```json
{"ok": true, "generated_at": "...", "as_of": "2026-08-14", "committed": true,
 "watched": 3, "setups": [ ... ], "status_lines": ["ASML — basing 9d, ..."],
 "fresh_news": [{"title": "...", "url": "...", "sources": ["Reuters"],
                 "published_text": "Fri, 14 Aug 2026 ...",
                 "about_competitor": "AMD", "carried_by": 3}],
 "events": [{"ticker": "NVDA", "kind": "earnings", "date": "2026-08-20",
             "days_away": 6, "line": "NVDA — earnings in 6 days (2026-08-20), ..."}],
 "quiet": false}
```

### `stockctl alerts [--ticker T]... [--commit]`

Portfolio alerts. Defaults to every open position. Same `fresh_news` and `events`
shapes; `quiet: true` means send nothing.

### `stockctl scan [--commit]`

The detector alone, no news, every enabled ticker including ones that did not
earn a paragraph. `--commit` stores today's verdicts, which is what lets tomorrow
recognise a failed breakout.

---

### `stockctl macro [--refresh] [--budget N]`

Current level of every tracked series plus the 2s10s curve. Reads; **stamps
nothing**, so asking where rates are does not consume the next scheduled macro
section. `--refresh` fetches first and spends the daily quota.

```json
{"ok": true,
 "levels": {"ust_10y": {"label": "US 10-year", "as_of": "2026-08-20",
                        "value": 4.69, "unit": "percent"}},
 "curve": {"as_of": "2026-08-20", "spread_bp": 50.0, "inverted": false,
           "two_year": 4.19, "ten_year": 4.69,
           "basis": "derived: 10-year minus 2-year, same session"}}
```

### `stockctl sector`

Every configured sector measured over the default horizon, plus each watched
ticker's standing within its groups. Reads; stamps nothing.

```json
{"ok": true,
 "sectors": [{"name": "AI infrastructure", "median_return": -8.8,
              "dispersion": 6.1, "cohesion": "mixed", "breadth": "1/3",
              "leaders": ["NVDA"], "laggards": ["AMD"], "missing": [],
              "line": "AI infrastructure: median -8.8% over 30d, 1/3 up, mixed; leading NVDA; lagging AMD.",
              "basis": "derived: close-to-close over the horizon, from cached bars"}],
 "standings": {"NVDA": [{"sector": "AI infrastructure", "position": "leading",
                         "gap_pct_points": 7.4, "cohesion": "mixed",
                         "carried": false}]}}
```

`carried: true` is the field worth reading: the group moved as a bloc and this
name went with it, so the move is the sector's rather than the company's.

---

## On demand

### `stockctl brief --ticker T [--lookback N] [--no-charts]`

Both charts plus every metric, in one call. Default lookback 90 days.

```json
{"ok": true, "ticker": "NVDA", "as_of": "2026-08-14", "lookback_days": 90,
 "sessions_in_window": 62,
 "key_metrics": {"last_close": 225.16, "week52_high": 236.54, "week52_low": 164.07,
                 "week52_position_pct": 84.3, "average_volume": 145977437.1,
                 "pe": null, "forward_pe": null, "ratios_note": "..."},
 "technical": { ... the full Setup ... },
 "status_line": "NVDA — basing 11d, ...",
 "charts": [{"path": "...png", "kind": "candles", "bars": 62, "from": "...", "to": "..."},
            {"path": "...png", "kind": "lines", "series": ["close","sma20","sma50"]}],
 "news": [ ... ], "position": null}
```

`position` is null unless the ticker is held.

### `stockctl chart {candles|lines} --ticker T [--lookback N]`

One image. Charts older than `STOCK_DESK_CHART_RETENTION` days (default 7) are
swept on every render.

---

## Configuration and state

### `stockctl watch {list|add|remove|update}`

```bash
stockctl watch add TSLA --name Tesla --competitor RIVN --competitor LCID --horizon 30
stockctl watch update TSLA --disable
stockctl watch remove TSLA
```

`add` flags: `--name`, `--competitor` (repeatable),
`--analysis {technical,competitor}` (repeatable), `--horizon`,
`--min-dollar-volume`, `--disabled`.

`update` takes the same, plus `--enable` / `--disable`. Repeatable flags
**replace** the list rather than appending, so pass the full set.

An unknown field name is an error, not a silent no-op — a typo must not look like
a successful edit.

### `stockctl positions {add|list|delete}`

```bash
stockctl positions add NVDA --side buy --quantity 100 --price 120.50 --date 2026-07-01 --fee 5
stockctl positions list [--ticker T] [--all]
stockctl positions delete 4
```

`list` shows open positions; `--all` includes closed ones. Values are per
position in its own currency and are never summed.

### `stockctl schedule`

Next report time per market group.

```json
{"ok": true, "now": "2026-08-16T13:57:04+08:00",
 "schedule": [{"market": "US", "tickers": ["NVDA", "ASML"],
               "next_open": "2026-08-17T09:30:00-04:00",
               "report_due_at": "2026-08-17T09:00:00-04:00",
               "minutes_before_open": 30}]}
```

A watchlist spanning two markets returns two entries with two different times.
Re-run after a DST change.

### `stockctl runs [--limit N] [--kind K]`

Recent run health. `kind` is `sync`, `news_poll` or `events_refresh`.
