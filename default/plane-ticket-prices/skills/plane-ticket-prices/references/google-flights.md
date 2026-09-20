# Google Flights crawl mechanics and traps

Measured against live Google Flights on 2026-08-02 (headless Chromium,
`hl=en`, `curr=HKD`, timezone Asia/Hong_Kong). Re-measure whenever the crawler
starts returning `no outbound options parsed` — Google changes this UI without
notice.

## The search URL

`https://www.google.com/travel/flights/search?tfs=<base64 protobuf>&hl=en&curr=HKD`

The `tfs` parameter is built by `fast_flights.querying.create_query` with two
`FlightQuery` legs (outbound + return), `trip="round-trip"`, seat, passengers,
stops and currency. The test suite decodes a generated `tfs` back through
`fast_flights.pb.flights_pb2.Info` and asserts `trip == ROUND_TRIP` with two
legs — this catches the worst silent bug (accidentally crawling one-way fares
and recording half-price "round trips").

## The aria-label schema (2025+)

Each option card's `aria-label` is prose, not structured data:

```
From 2514 Hong Kong dollars round trip total. Nonstop flight with
Hong Kong Express. Leaves Hong Kong International Airport at 11:55 AM on
Friday, December 18 and arrives at Penang International Airport at 3:40 PM
on Friday, December 18. Total duration 3 hr 45 min.   Select flight
```

Parser handles: `From <amount> <currency words> round trip total` (currency
words mapped to ISO codes); `Nonstop|<n> stop flight with <carrier>` (a
connecting option says "Cathay Pacific and Emirates" — the FIRST carrier is the
outbound airline); airport NAMES resolved to IATA codes via the city-code map
learned from the page's `Where from? Hong Kong HKG` labels; `Total duration
<X> hr <Y> min` (authoritative, including red-eyes across midnight).

Same flight appears three times in the DOM: the priced card (`From ...`), a
`Flight details.` variant, and a bare `Leaves ...` row. Only the priced card is
actionable — the crawler drops unpriced labels before selection. Headers,
price chips (`2514 Hong Kong dollars`), and filter controls parse to nothing
and are skipped.

Legacy schema (pre-2025, `9:35 AM HKG to DXB, 3h 45m, Cathay Pacific ...`)
is still parsed as a fallback.

## Clicking an option

The card is a `div[role=link]` whose children (`Total duration 3 hr 45 min`
etc.) intercept pointer events, so Playwright's actionability check fails on
`.click()`. Use keyboard activation instead — focus the row and press Enter —
which activates the `role=link` without touching the pointer.

## Page state and pacing

- The search-box labels (`Where from? ...`) are present from first paint and
  are the source of the airport-code map.
- Results render async; the crawler polls every second until an outbound
  option parses (45 s timeout).
- One page load per outbound selection (the crawl re-navigates to the search
  URL fresh for each card) — deterministic, no stale DOM, but page-load
  budget is real: a scope with 9 date pairs and 3 outbound cells each is ~30
  loads. `TICKET_PRICES_DELAY` paces loads (default 2.0 s). Per-scope
  `max_searches_per_run` caps the run and reports `budget_exhausted`.
- Consent: the persisted Chromium profile (`.browser_profile/` next to the
  DB) absorbs consent dialogs once; a best-effort "Accept all" clicker covers
  fresh profiles. The profile is disposable — delete it freely.

## Data-quality notes

- Prices are the round-trip total for **all passengers**, as displayed.
- Buckets are 3-hour local windows of the displayed departure time
  (`00-03`, `03-06`, ..., `21-24`), in the browser timezone
  (Asia/Hong_Kong). A red-eye departing 23:55 lands in `21-24`.
- With `max_stops: 1`, Google can surface mixed-cabin options ("Business
  Class + Economy Layover ...") priced accordingly — they are stored as
  shown. The monitored scopes are nonstop (`max_stops: 0`), where this is
  rare.
- `min_price` per cell is the min across return options in that return
  bucket; return carrier is stored but not part of the cell key, so a
  day-over-day series survives Google swapping return partners.
- No flights / no results for a pair is recorded as a pair failure in the
  run's `detail` — it is not an error state for the run as a whole.

## Re-capturing fixtures

If the parser stops matching, the label schema has changed. Re-run:

```bash
cd ~/projects/hermes/plane-ticket-prices
.venv/bin/python scripts/capture_fixtures.py
```

This rewrites `tests/fixtures/gf_labels_2026.json` with fresh labels, then
update the parser and run `uv run pytest -q` until green. Never hand-edit the
fixture expectations.
