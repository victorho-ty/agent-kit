# Hermes plane ticket price tracker

A Hermes Agent skill plus the deterministic Python tools behind it. A Playwright
crawler walks Google Flights for **non-stop, round-trip, economy** fares and appends
them to a SQLite database; the agent reads that series to answer "how are the Dubai
fares trending" and "should I book now".

```
plane-ticket-prices/
├── plane_ticket_prices/                # the package
│   ├── __init__.py
│   ├── config/
│   │   ├── scope.py                    # TravelScope, date_pairs(), load_scopes()
│   │   └── scope.json                  # the trips being monitored
│   ├── cli.py                          # python -m plane_ticket_prices <command>
│   ├── parsing.py                      # aria-label -> Option (pure, stdlib only)
│   ├── crawler.py                      # Playwright session, click-through, selection
│   ├── db.py                           # SQLite schema, upsert, run summary
│   └── report.py                       # optional: trend chart
├── skills/plane-ticket-prices/
│   ├── SKILL.md                        # what the agent loads
│   └── references/
│       ├── cli.md                      # full command surface and JSON shapes
│       └── google-flights.md           # measured crawl mechanics and traps
├── tests/                              # pytest, no network, no browser
└── pyproject.toml
```

## Install on Ubuntu

```bash
unzip plane-ticket-prices.zip -d ~/hermes
cd ~/hermes/plane-ticket-prices
uv sync
uv run playwright install --with-deps chromium
```

Point Hermes at the skill — either copy it into the agent's skills directory or
symlink it:

```bash
ln -s ~/projects/hermes/plane-ticket-prices/skills/plane-ticket-prices ~/.hermes/skills/plane-ticket-prices
```

The skill runs `python -m plane_ticket_prices …` from the bundle root, so either
start Hermes with that as the working directory or install the package into the
agent's environment (`uv pip install -e .`, which also exposes a console script).

Note that `pyproject.toml` still declares the package and console script under the
old `ticket_price_tracker` / `config` names. Both must be repointed at
`plane_ticket_prices` before `uv sync` produces a working install.

## Configuration

| Variable | Default |
|---|---|
| `TICKET_PRICES_DB` | `~/.local/share/hermes-ticket-prices/ticket_prices.db` |
| `TICKET_PRICES_SCOPE_FILE` | `plane_ticket_prices/config/scope.json` in the bundle |
| `TICKET_PRICES_TZ` | `Asia/Hong_Kong` |
| `TICKET_PRICES_HEADLESS` | `1` |

Trips themselves are **not** environment variables — they live in
`plane_ticket_prices/config/scope.json` so travel plans change without touching
code. Each scope names a route, a departure-date window, a return-date window and
the filters that make a fare relevant (`max_stops`, `seat`, `min_nights`/
`max_nights`).

## How it fits together

Hermes' scheduler runs the crawl once a day. Register the command directly rather
than a prompt — a nightly crawl is fully deterministic, so putting an LLM in that
loop burns tokens for no decision:

```bash
cd ~/hermes/hermes-plane-ticket-prices && .venv/bin/python -m planeticket-prices collect
```

The skill is what Hermes loads when *you ask a question* about prices, or when a run
needs triage. It reads the `runs` table first to check freshness, then queries the
price series — it never drives a browser itself.

The unit of work is a **(departure date, return date) pair**, because a true
round-trip fare only exists for a specific pair of dates.
`plane_ticket_prices/config/scope.json` currently yields 9 pairs for
`HKG-DXB-Winter` and 6 for `HKG-PEN-Winter`; at three distinct
`(airline, departure bucket)` cells each, a full run is roughly 105 page loads and
**~15 minutes**.

## Data model

SQLite at `$TICKET_PRICES_DB`. Three tables.

`round_trip_prices` — one row per grid cell per run day, appended forever:

```sql
CREATE TABLE round_trip_prices (
  id             INTEGER PRIMARY KEY,
  run_date       TEXT    NOT NULL,     -- YYYY-MM-DD, local
  scope          TEXT    NOT NULL,
  origin         TEXT    NOT NULL,
  dest           TEXT    NOT NULL,
  depart_date    TEXT    NOT NULL,
  return_date    TEXT    NOT NULL,
  airline        TEXT    NOT NULL,     -- outbound carrier
  return_airline TEXT,                 -- carried, not part of the key
  dep_bucket     TEXT    NOT NULL,     -- '00-03' … '21-24'
  ret_bucket     TEXT    NOT NULL,
  out_stops      INTEGER NOT NULL,
  ret_stops      INTEGER NOT NULL,
  seat           TEXT    NOT NULL,
  currency       TEXT    NOT NULL,
  min_price      REAL    NOT NULL,     -- round-trip total, all passengers
  n_itineraries  INTEGER NOT NULL,
  created_at     TEXT    NOT NULL,
  UNIQUE (run_date, scope, depart_date, return_date,
          airline, dep_bucket, ret_bucket, out_stops, ret_stops, seat, currency)
);
```

- `min_price` is the **true round-trip total for all passengers**, in `currency` —
  not a one-way fare and not a per-person fare.
- `dep_bucket` / `ret_bucket` are 3-hour local departure windows.
- `airline` is the **outbound** carrier. Google will pair a different carrier on the
  return, so `return_airline` is carried alongside as a non-key attribute — making it
  a key would fragment the grid and break the day-over-day series every time Google
  swaps a pairing.
- Write with `INSERT … ON CONFLICT DO UPDATE` against that unique constraint. Re-running
  a day then updates only the rows actually re-crawled and leaves every other date pair
  untouched — so a partial re-run after a failure cannot delete the pairs it skipped.
  Never implement the write as "delete everything for `(run_date, scope)`, then insert".
- `origin`/`dest`/`seat`/`currency` are stored despite being derivable from the scope:
  the consuming agent does not read `scope.json`, so a row must be self-describing.

`itineraries` — the full per-run detail, one row per (chosen outbound × return
option), with exact departure/arrival timestamps and durations. This is what
`round_trip_prices` is aggregated from; keep it for triage and for re-deriving the
grid if the bucketing rule ever changes.

`runs` — one row per `collect` run per scope: started/finished timestamps, status
(`ok`, `partial`, `blocked`, `error`), pairs planned, pairs succeeded, pairs failed,
searches used, rows written. Written on **every** run including failures. This table
is the skill's freshness check and its entire triage surface; the skill never parses
stdout.

Nothing is ever deleted. The crawler's Chromium profile lives outside the database
(`$TICKET_PRICES_DB`'s directory, `.browser_profile/`) and *is* disposable — delete it
freely to reset consent state.

## Tests

```bash
uv run pytest -q
```

No network and no browser in tests. Capture a real aria-label set once and fixture
it; never commit a full captured Google Flights page (~2 MB of noise carrying
per-session identifiers).

The tests that matter most:

1. the aria-label parser against real captured labels, including a red-eye that
   crosses midnight and the narrow no-break space (`U+202F`) Google puts before
   `AM`/`PM`;
2. upsert idempotency — re-running one date pair must leave the other pairs unchanged
   and add no duplicate rows;
3. the search URL really encodes a **round trip with two legs** — decode the `tfs`
   base64 back through `fast_flights.pb.flights_pb2.Info` and assert
   `trip == ROUND_TRIP`. This catches the worst silent bug available here:
   accidentally building a one-way and recording half-price fares as round trips.
