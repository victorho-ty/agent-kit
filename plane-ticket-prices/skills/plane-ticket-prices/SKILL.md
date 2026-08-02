---
name: plane-ticket-prices
description: Track Google Flights round-trip price trends for monitored trips. Use when asked how fares are trending ("is HKG-DXB getting cheaper"), whether to book now, or for the daily price report image. Reads the SQLite series via the CLI -- never drives a browser.
---

# Plane ticket prices

Deterministic Python tools crawl Google Flights once a day (via Hermes cron),
store a daily price series in SQLite, and render a trend report PNG. The trips
monitored live in `plane_ticket_prices/config/scope.json` — travel plans change
there, never in code.

## Setup

Bundle root: `~/projects/hermes/plane-ticket-prices`.

Prefer the installed console script — it works from any working directory on the
project's own uv venv:

```bash
plane-ticket-tracker <command> [options]
```

`plane-ticket-tracker` is a symlink at `~/.local/bin/plane-ticket-tracker`
pointing at the project's `.venv/bin/plane-ticket-tracker`. If missing, run from
the bundle root instead:

```bash
cd ~/projects/hermes/plane-ticket-prices
.venv/bin/python -m plane_ticket_prices <command> [options]
```

Every command prints one JSON object on stdout. Parse it — never guess at
prices, never do arithmetic yourself.

Environment overrides: `TICKET_PRICES_DB` (default
`~/.local/share/hermes-ticket-prices/ticket_prices.db`), `TICKET_PRICES_TZ`
(default `Asia/Hong_Kong`), `TICKET_PRICES_SCOPE_FILE`, `TICKET_PRICES_REPORT_DIR`
(default `<bundle>/reports`), `TICKET_PRICES_DELAY` (page pacing, default 2.0s).

## The daily run

A Hermes cron job runs every day at 20:00 Asia/Hong_Kong:

1. `plane-ticket-tracker collect` — crawls every date pair in scope.json,
   upserts today's grid cells, writes a `runs` row.
2. `plane-ticket-tracker report` — renders one portrait PNG per scope into
   `reports/`.
3. The report PNGs are sent to the home Telegram chat with a 2–3 line summary
   (cheapest overall per scope, biggest week-over-week movers).

If `collect` reports a non-`ok` status (`partial`, `blocked`, `error`), say so
in the summary instead of presenting the numbers as complete. A `partial` run
is normal when Google returns no results for some pairs; the upsert leaves
yesterday's cells intact for the pairs the run skipped.

## Answering price questions

Check freshness first — the series is only as new as the last run:

```bash
plane-ticket-tracker runs --limit 3
```

Then query the series. Prices are **round-trip totals for all passengers**,
in the scope's currency, per grouping `(airline, departure 3h-bucket) x return
3h-bucket` and per (departure, return) date pair.

```bash
plane-ticket-tracker latest                          # cheapest per grouping, latest run
plane-ticket-tracker latest --scope HKG-DXB-Winter
plane-ticket-tracker trend --scope HKG-DXB-Winter --since 2026-07-01
```

Answer with the numbers as returned. State the currency. For "should I book
now" style questions, compare the latest price against the series low/high —
use `trend` and compute the range from the returned rows, or just run
`plane-ticket-tracker report` and send the image.

## The report image

```bash
plane-ticket-tracker report --out /tmp/prices.png
```

`report` returns `images` (one PNG per scope) plus `scopes.<name>.cheapest`
(top 5 cells) and `biggest_drops` / `biggest_rises` (week-over-week movers).
Send the PNGs back with a one-line summary per scope — do not describe the
charts at length. One PNG contains:

- daily trend lines for the cheapest groupings, ranked by latest price,
- a week-over-week table (green = cheaper than 7 days ago, red = risen),
- the "cheapest right now" ranking table.

With fewer than two run days the WoW panel says so — that is expected for the
first week.

## Rules

- Never write to the database except through `collect`.
- Never invent a price or a trend the tools did not return.
- Never drive the browser yourself; the crawler owns the Google Flights
  session. If `collect` fails repeatedly, run it once manually and read the
  `detail`/`pair_failures` in the JSON.
- Trips are edited in `plane_ticket_prices/config/scope.json` (fields:
  from/to airports, depart/return windows, `max_stops`, `seat`, nights,
  currency). After editing, run `plane-ticket-tracker collect --dry-run` to
  see the resulting date pairs.

Full command surface and JSON shapes: `references/cli.md`.
Google Flights crawl mechanics and traps: `references/google-flights.md`.
