# Command surface and JSON shapes

All commands print exactly one JSON object on stdout. Errors print
`{"error": "...", "type": "..."}` and exit non-zero.

## collect

Crawl Google Flights for today's prices and store them.

```bash
plane-ticket-tracker collect [--scope NAME]... [--dry-run] [--max-searches N] [--headful]
```

- `--dry-run` prints the plan (date pairs + filters) without a browser or DB.
- `--max-searches` caps page loads across the run (per-scope
  `max_searches_per_run` also applies via the scope config).
- `--headful` shows the browser (debugging only).

Output:

```json
{
  "run_date": "2026-08-02",
  "status": "ok",
  "scopes": [
    {
      "scope": "HKG-DXB-Winter",
      "status": "partial",
      "run_date": "2026-08-02",
      "pairs_planned": 9,
      "pairs_succeeded": 8,
      "pairs_failed": 1,
      "searches_used": 31,
      "rows_written": 27,
      "detail": {"pair_failures": [{"pair": "2026-12-18 -> 2026-12-22", "reason": "no outbound options parsed"}]}
    }
  ]
}
```

`status` per scope: `ok` (all pairs), `partial` (some failed or search budget
exhausted), `blocked`/`error` (crawler-level). Overall `status` mirrors it;
exit code is 0 only for `ok`.

## report

Render the daily PNG report (one portrait image per scope).

```bash
plane-ticket-tracker report [--scope NAME]... [--out DIR] [--wow-days N] [--top N]
```

Output:

```json
{
  "images": ["~/projects/hermes/plane-ticket-prices/reports/HKG-DXB-Winter_2026-08-02.png"],
  "scopes": {
    "HKG-DXB-Winter": {
      "run_date": "2026-08-02",
      "cheapest": [{"airline": "Cathay Pacific", "depart_date": "2026-12-18", "return_date": "2026-12-22", "dep_bucket": "21-24", "ret_bucket": "03-06", "min_price": 4408.0, "currency": "HKD", "grouping": "Cathay Pacific 21-24\u219203-06"}],
      "biggest_drops": [{"airline": "...", "price": 4408.0, "price_7d_ago": 4800.0, "delta": -392.0, "delta_pct": -8.2}],
      "biggest_rises": []
    }
  }
}
```

`cheapest` is the top-5 cells ranked by `min_price`; `biggest_drops`/`rises`
are week-over-week movers from the latest run. With no data for a scope,
`scopes.<name>` is `{"error": "no data yet"}`.

## latest

Cheapest price per grouping from the most recent run.

```bash
plane-ticket-tracker latest [--scope NAME]... [--top N]
```

```json
{"HKG-DXB-Winter": {"run_date": "2026-08-02", "cheapest": [{"airline": "Cathay Pacific", "depart_date": "2026-12-18", "return_date": "2026-12-22", "dep_bucket": "21-24", "ret_bucket": "03-06", "min_price": 4408.0, "currency": "HKD"}], "cells": 27}}
```

## trend

The daily series per grid cell for agent questions.

```bash
plane-ticket-tracker trend [--scope NAME]... [--since YYYY-MM-DD]
```

```json
{"HKG-DXB-Winter": {"count": 108, "rows": [{"run_date": "2026-07-19", "airline": "Cathay Pacific", "depart_date": "2026-12-18", "return_date": "2026-12-22", "dep_bucket": "21-24", "ret_bucket": "03-06", "min_price": 6400.0, "currency": "HKD"}]}}
```

`min_price` is the round-trip total for all passengers, in the scope's currency
(default HKD). One row per (cell, run_date), oldest first.

## runs

Run history — the freshness check and triage surface.

```bash
plane-ticket-tracker runs [--limit N]
```

```json
{"runs": [{"scope": "HKG-DXB-Winter", "run_date": "2026-08-02", "status": "ok", "pairs_planned": 9, "pairs_succeeded": 9, "pairs_failed": 0, "searches_used": 31, "rows_written": 29, "detail": null}]}
```

## SQLite schema (read-only reference)

`$TICKET_PRICES_DB`, three tables:

- `round_trip_prices` — one row per grid cell per run day. Unique key:
  `(run_date, scope, depart_date, return_date, airline, dep_bucket,
  ret_bucket, out_stops, ret_stops, seat, currency)`. `min_price` is the
  cheapest round-trip total for that cell; `return_airline` is carried, not
  part of the key (Google swaps return carriers day to day).
- `itineraries` — exact per-combination rows (times, durations, stops).
- `runs` — one row per collect run per scope; the agent's freshness check.

Nothing is ever deleted.
