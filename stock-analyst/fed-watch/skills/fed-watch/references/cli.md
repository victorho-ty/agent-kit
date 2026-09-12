# Command surface and JSON shapes

Every command prints one JSON object on stdout and exits with a code below. The
one exception is `check-changes --quiet`, which prints a bare `1` or `0`.

## Exit codes

| code | name | meaning |
|---|---|---|
| 0 | OK | |
| 10 | `ERR_CONFIG` | `fomc.json` is malformed, or the calendar has run out |
| 11 | `ERR_DB` | the database could not be opened or written |
| 20 | `ERR_FETCH` | no futures price or no effective rate could be read |
| 21 | `ERR_INSUFFICIENT` | too few stored snapshots to answer |
| 22 | `ERR_CHART` | the renderer failed, or the output directory is unwritable |

An error payload is `{"ok": false, "error", "exit_code", "message", "detail"}`.
`detail` sometimes carries a `remedy`; when it does, that is the thing to tell
the operator.

## `fedctl snap`

Read the market now and store one row of history. Wakes nobody.

```
--meetings N     how many upcoming meetings to price (default 2)
```

```json
{
  "ok": true,
  "snapshot_id": 1,
  "taken_at": "2026-09-12T00:46:57+08:00",
  "status": "ok",
  "price_source": "yfinance 1m last trade (not CME settlement mid)",
  "attribution": "computed from 30-Day Fed Funds futures using CME FedWatch's published methodology; these are not CME's published figures",
  "policy": {
    "effr": 3.63,
    "as_of": "2026-09-10",
    "target_low": 3.5,
    "target_high": 3.75,
    "target_range": "3.50-3.75%"
  },
  "meetings": [
    {
      "meeting_date": "2026-09-16",
      "ordinal": 1,
      "contract": "ZQU26.CBT",
      "price": 96.2675,
      "implied_rate": 3.732498,
      "expected_rate": 3.849639,
      "method": "blend_inversion",
      "amplification": 2.14,
      "noisy": false,
      "outcomes": [
        {"step": 0, "basis_points": 0, "label": "no change", "band": "3.50-3.75%", "probability_pct": 12.1},
        {"step": 1, "basis_points": 25, "label": "25bp hike", "band": "3.75-4.00%", "probability_pct": 87.9}
      ]
    }
  ],
  "ahead": 10
}
```

`status` is `ok` or `partial`. On `partial`, `failures` maps each unreadable
contract to why. `calendar_warning` appears when fewer than three FOMC dates
remain ahead of today.

`outcomes` is contiguous across `step` and always contains `step: 0`. A band at
`0.0` was priced at nothing.

`price_source` is the Yahoo series the prices actually came from. Normally the
one-minute series; when that is empty for a contract it falls back to the daily
bar and the string names which contract that happened to. The daily bar is worth
about a percentage point more error, so the difference is reported rather than
hidden.

## `fedctl check-changes`

Snap, then report what moved since the last snapshot that was *reported*.

```
--threshold P    percentage points a probability must move to count (default 1.0)
--limit N        how many reported changes the charts cover (default 10)
--meetings N     how many upcoming meetings to price (default 2)
--no-fetch       diff the stored history without fetching; for replay and outages
--quiet          print a bare 1 or 0 and nothing else
```

Adds to the snapshot shape:

```json
{
  "fetched": true,
  "changes": {
    "changed": true,
    "threshold_pct": 1.0,
    "baseline_taken_at": "2026-09-12T09:00:00+08:00",
    "largest_move_pct": 4.7,
    "policy_changed": false,
    "policy_from": null,
    "meetings": [
      {
        "meeting_date": "2026-09-16",
        "status": "moved",
        "contract": "ZQU26.CBT",
        "implied_rate_from": 3.7312,
        "implied_rate_to": 3.7401,
        "expected_rate_from": 3.8469,
        "expected_rate_to": 3.8586,
        "largest_move_pct": 4.7,
        "outcomes": [
          {"label": "25bp hike", "band": "3.75-4.00%", "from_pct": 86.7, "to_pct": 91.4, "delta_pct": 4.7}
        ]
      }
    ]
  },
  "current": [ ... same shape as snap's `meetings` ... ],
  "charts": [ ... ],
  "charts_skipped": [],
  "reported_changes_plotted": 5
}
```

When nothing cleared the threshold the payload carries `"quiet": true`, no
`charts`, and `changes.changed: false`.

A meeting's `status` is `moved`, `new` (not tracked at the baseline) or
`dropped` (no longer tracked). Only `moved` carries `outcomes` deltas.

**The baseline only moves when something is reported.** A quiet poll leaves it
where it was, so a slow drift is measured from where it started rather than from
an hour ago, and is reported once it accumulates past the threshold.

`--no-fetch` diffs the most recent stored snapshot instead of taking a new one.

## `fedctl history`

The last N reported changes, summarised and charted. Fetches nothing.

```
--limit N        how many reported changes to cover (default 10)
```

```json
{
  "ok": true,
  "now": "2026-09-12T14:00:00+08:00",
  "requested": 10,
  "reported_changes": 5,
  "from": "2026-09-12T09:00:00+00:00",
  "to": "2026-09-12T13:00:00+00:00",
  "policy": {"effr": 3.63, "as_of": "2026-09-10", "target_range": "3.50-3.75%"},
  "latest": [ ... same shape as snap's `meetings` ... ],
  "net_change": { ... same shape as `changes`, computed at threshold 0 ... },
  "charts": [
    {
      "path": "/home/.../fedwatch-charts/fedwatch_2026-09-16_5pts_2026-09-12.png",
      "meeting_date": "2026-09-16",
      "orientation": "portrait",
      "points": 5,
      "series": ["no change", "25bp hike", "50bp hike"],
      "from": "2026-09-12T09:00:00+00:00",
      "to": "2026-09-12T13:00:00+00:00"
    }
  ],
  "charts_skipped": []
}
```

`net_change` spans the whole window at a zero threshold, so it shows every band
that moved at all — not the same as the most recent reported change.

`ERR_INSUFFICIENT` means no changes have been reported yet. `detail` carries
`stored_snapshots` so a run of `snap`s with nothing reported is distinguishable
from an empty database.

## `fedctl meetings`

The calendar, and which contract answers each date. Fetches nothing.

```
--count N        how many upcoming meetings to show (default 2)
```

```json
{
  "ok": true,
  "today": "2026-09-12",
  "known_through": "2027-10-27",
  "meetings_ahead": 10,
  "tracked": [
    {"meeting_date": "2026-09-16", "ordinal": 1, "method": "blend_inversion", "contract": "ZQU26.CBT", "amplification": 2.14},
    {"meeting_date": "2026-10-28", "ordinal": 2, "method": "clean_next_month", "contract": "ZQX26.CBT", "amplification": 1.0}
  ]
}
```

## Environment overrides

| variable | default |
|---|---|
| `FED_WATCH_DB` | `~/.local/share/hermes-stock-analyst/fed_watch.db` |
| `FED_WATCH_CHART_DIR` | `~/.local/share/hermes-stock-analyst/fedwatch-charts` |
| `FED_WATCH_CHART_ORIENTATION` | `portrait` |
| `FED_WATCH_CHART_RETENTION` | `7` (days) |
| `FED_WATCH_CHANGE_THRESHOLD` | `1.0` (percentage points) |
| `FED_WATCH_CALENDAR` | the shipped `fed_watch/config/fomc.json` |
| `FED_WATCH_TZ` | `Asia/Hong_Kong` |
| `FED_WATCH_TIMEOUT` | `20.0` (seconds) |
| `FED_WATCH_RETRIES` | `2` |
| `FED_WATCH_NOW` | unset; pins the clock for tests and replay |
