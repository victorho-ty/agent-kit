# `estates.json`

Lives at `hk_transaction_tracker/config/estates.json`. Whole-line `//` comments
are stripped before parsing, so notes and disabled examples can stay in the
file. A `https://` inside a value is safe — only lines whose first non-space
characters are `//` are removed.

Validate after every edit:

```bash
hk-tx estates
```

A malformed file exits `10` with the offending field named. It is never a crash
and never a partial load.

## Top level

```json
{
  "timezone": "Asia/Hong_Kong",
  "request_delay_seconds": 2.0,
  "fetch_size": 100,
  "trend": {
    "window_days": 90,
    "min_samples": 3,
    "chart_months": 24,
    "chart_min_points": 3
  },
  "estates": [ … ]
}
```

| key | default | meaning |
|---|---|---|
| `timezone` | `Asia/Hong_Kong` | local clock for dates and the run stamp |
| `request_delay_seconds` | 2.0 | seconds between estates. Keep at 1 or above. |
| `fetch_size` | 100 | records per estate. **Capped at 100**; see below. |
| `trend.window_days` | 90 | the recent window, compared against the one before it |
| `trend.min_samples` | 3 | below this in either window, no percentage is reported |
| `trend.chart_months` | 24 | months of monthly medians behind a line chart |
| `trend.chart_min_points` | 3 | fewest months with data before a chart is drawn |

**`fetch_size` above 100 is refused.** Centanet answers a larger request with
HTTP 200 and an empty list rather than an error, which would read as a quiet
estate for ever. The validator refuses it so the mistake cannot be made silently.

## One estate

```json
{
  "name": "泓都",
  "label": "泓都 Island Harbourview",
  "url": "https://hk.centanet.com/findproperty/list/transaction/%E6%B3%93%E9%83%BD_2-SSPPWPPYPS?q=8prsheylr1o5h",
  "bedrooms": [2, 3],
  "size_ranges": [[500, 700]],
  "track": ["sale", "rental"],
  "enabled": true
}
```

| field | required | meaning |
|---|---|---|
| `name` | yes | **the archive's key.** Must be unique. Never rename it. |
| `url` | yes | a Centanet `/list/transaction/` URL |
| `label` | no | display name; falls back to `name` |
| `bedrooms` | no | 間隔 to report. Empty or omitted means no constraint. |
| `size_ranges` | no | bands of 面積(實) in square feet. Empty means no constraint. |
| `track` | no | `["sale"]`, `["rental"]` or both. Defaults to both. |
| `enabled` | no | `false` pauses the estate without losing its history |

### `name` is the key

Every stored transaction, every piece of estate state and every trend is filed
under `name`. Renaming it orphans the lot — the old rows stay in the archive
under a name nothing reads, and the new name seeds from scratch and goes silent
for a run. Change `label` instead; it is what appears in every message.

### `bedrooms`

Whole numbers, matching Centanet's own 間隔 filter:

| value | means |
|---|---|
| `0` | 開放式 |
| `1`, `2`, `3` | 1房, 2房, 3房 |
| `4` | **4房或以上** — a five- or six-bedroom flat matches a configured `4` |

### `size_ranges`

Bands of **saleable** area, inclusive at both ends. Two spellings, both valid:

```json
"size_ranges": [[500, 700], {"low": 900, "high": null}, [null, 400]]
```

`null` at either end opens that end: `900呎以上`, `400呎以下`. A band with both
ends null is refused — it means nothing that omitting the field does not say
more clearly.

Overlapping bands are allowed, and the **first** band containing a transaction
wins, so nothing is reported twice under two headings. Order them accordingly.

### The two criteria are ANDed

A transaction is reported when it satisfies **both** dimensions:

| `bedrooms` | `size_ranges` | reports |
|---|---|---|
| `[2, 3]` | `[[500, 700]]` | 2房 and 3房 flats of 500–700 saleable feet |
| `[2, 3]` | *omitted* | every 2房 and 3房 flat, any size |
| *omitted* | `[[500, 700]]` | every flat of 500–700 feet, any layout |
| *omitted* | *omitted* | **every transaction in the estate** |

Everything else in the estate is still fetched and stored — it feeds the
estate-wide trend — but is never announced.

### What an unpublished dimension does

A dimension the source did not publish **cannot reject** a transaction; it is
skipped, and the other dimension still has to pass.

| 間隔 | 面積(實) | with `[2,3]` + `[[500,700]]` |
|---|---|---|
| 2房 | 507呎 | reported, in the 2房 / 500-700呎 group |
| 2房 | not published | reported, in the estate's **面積待補** group |
| 1房 | not published | not reported — the published dimension failed |
| not published | 507呎 | reported, in the 面積待補 group |
| not published | not published | **not reported** — nothing to judge it on |

The last row is the floor: at least one configured dimension must actually have
been checked and passed, so absence never matches by default.

## Adding an estate safely

1. Find the estate's 成交 page on `hk.centanet.com` and copy the URL whole.
2. Add the entry with `"enabled": false`.
3. `hk-tx estates` — does it validate, and do the criteria read correctly?
4. `hk-tx check --estate <name> --dry-run` — this fetches and judges without
   writing anything. Read `candidates` back to the user. Wrong units, an empty
   list, or every single transaction matching means the URL or the criteria are
   wrong.
5. Set `"enabled": true`. The next `check` seeds it silently, absorbing the back
   catalogue as the trend baseline.

Step 4 is not optional. An entry enabled on an untested URL looks healthy in
`estates` and reports nothing for ever.

## Pausing and removing

Set `"enabled": false` to pause. The archive, the trend and the dedupe memory
all survive, and re-enabling picks up where it left off without re-announcing
anything.

Deleting an entry leaves its transactions in the archive — nothing in this
package deletes a row — and `history`, `trend` and `transactions` all still
reach them, falling back to the stored `name` because there is no longer a
`label` to resolve. What is lost is the criteria: re-adding the entry later
seeds nothing new, but nothing was tracked in the meantime. Prefer disabling.
