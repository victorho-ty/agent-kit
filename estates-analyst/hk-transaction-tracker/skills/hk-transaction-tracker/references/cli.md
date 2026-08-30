# Command surface

Every command prints one JSON object on stdout and exits with a code from the
table at the bottom. The only exception is `pending --count`, which prints a
bare integer.

Nothing here updates or deletes a stored transaction. `check` appends,
`report --commit` stamps the delivery ledger, and everything else is a read.

---

## `hk-tx check`

Fetch every enabled estate, decode the page, judge each transaction against the
entry's criteria, and store what is new. Writes; says nothing.

| option | meaning |
|---|---|
| `--estate NAME` | only this estate, by config name. Repeatable. Includes disabled entries. |
| `--dry-run` | fetch and judge without writing anything |

```json
{
  "ok": true,
  "status": "ok",
  "run_id": 12,
  "checked_at": "2026-08-29T00:15:04+08:00",
  "dry_run": false,
  "estates_checked": 3,
  "seen": 293,
  "added": 4,
  "matched": 2,
  "errors": 0,
  "pending": 2,
  "estates": [
    {
      "estate": "泓都",
      "display": "泓都 Island Harbourview",
      "ok": true,
      "seeding": false,
      "parsed": 93,
      "published_count": 286,
      "added": 2,
      "matched": 1,
      "already_known": 91,
      "search": {"day": "Day1095", "size": 100, "offset": 0,
                 "sort": "InsOrRegDate", "order": "Descending", "postType": "Both"},
      "skipped": {"non_residential": 7},
      "warnings": []
    }
  ],
  "estate_failures": [],
  "warnings": []
}
```

- `status` — `ok` (every estate answered), `partial` (at least one failed),
  `error` (none answered).
- `seen` counts records parsed, not records added. On a settled tracker `added`
  is usually 0 and that is the normal case.
- `matched` is 0 during seeding by design: a first check absorbs the back
  catalogue as already-reported.
- `published_count` is Centanet's own total for the search — 286 transactions
  exist, 100 were served. The gap is not an error.
- `skipped` counts what was dropped and why: `non_residential` (車位),
  `unknown_post_type`, `no_price`, `no_date`, `no_id`.
- `zero_yield: true` on an estate means the page parsed and produced nothing
  where the last good check produced records. Report it.

`--dry-run` adds `candidates` (up to ten) to each estate, each a full
transaction with its `match` verdict, and writes nothing.

---

## `hk-tx pending`

How many matched transactions are waiting to be reported.

| option | meaning |
|---|---|
| `--count` | print a bare integer instead of JSON, for the cron gate |

```json
{
  "ok": true,
  "pending": 4,
  "by_bucket": [{"deal_type": "rental", "estate": "泓都", "count": 1}],
  "oldest": "2026-07-14",
  "newest": "2026-08-28"
}
```

---

## `hk-tx report`

The grouped summary of everything matched and not yet delivered.

| option | meaning |
|---|---|
| `--commit` | stamp the transactions as reported before returning them |
| `--limit N` | at most N transactions (default 60) |
| `--out-dir DIR` | write images here instead of the state directory |
| `--no-images` | text only; skips matplotlib entirely |

```json
{
  "ok": true,
  "new_count": 18,
  "pending_total": 18,
  "held_back": 0,
  "groups": [
    {
      "deal_type": "sale",
      "deal_label": "買賣",
      "count": 9,
      "estates": [
        {
          "estate": "泓都",
          "display": "泓都 Island Harbourview",
          "count": 3,
          "bedroom_groups": [
            {
              "bedrooms": 2,
              "bedroom_label": "2房",
              "size_groups": [
                {
                  "size_range": "500-700呎",
                  "size_label": "500-700呎",
                  "items": [
                    {
                      "tx_id": "26082401380100",
                      "ins_date": "2026-08-17",
                      "reg_date": "2026-08-24",
                      "unit": "2座 57樓 A室",
                      "bedrooms": 2,
                      "bedroom_label": "2房",
                      "saleable_area": 507.0,
                      "saleable_area_text": "507呎",
                      "price": 12400000.0,
                      "price_text": "$1,240萬",
                      "saleable_unit_price": 24458.0,
                      "unit_price_text": "$24,458/呎",
                      "area_missing": false,
                      "match_reason": "間隔 2房；面積(實) 507呎 屬 500-700呎",
                      "data_source": "Land",
                      "detail_url": "https://hk.centanet.com/findproperty/transaction-detail/…",
                      "line": "2026-08-17　2座 57樓 A室　2房　507呎　$1,240萬　$24,458/呎"
                    }
                  ]
                }
              ]
            }
          ],
          "area_pending": []
        }
      ]
    }
  ],
  "trends": [ /* one per estate × deal type with news; see `trend` below */ ],
  "summary_lines": ["2026-08-29 新增成交 18 宗（買賣 9 宗、租賃 9 宗）。", "…"],
  "images": [
    {"kind": "table", "deal_type": "sale", "label": "買賣新增成交", "path": "…/table-sale.png"},
    {"kind": "chart", "deal_type": "sale", "estate": "泓都",
     "label": "泓都 Island Harbourview 買賣 呎價(實)走勢", "path": "…/chart-泓都-sale.png",
     "points": 11}
  ],
  "committed": true,
  "committed_rows": 18,
  "cjk_font": "Noto Sans TC"
}
```

- `summary_lines` is the message body, already written. Relay verbatim.
- `area_pending` holds deals whose 面積(實) the source did not publish. They have
  an em dash for area and 呎價 and are absent from every median and chart.
- `held_back` above 0 means the cap was hit; those deals stay pending.
- `cjk_font: null` means no Chinese font was found and the images will show
  boxes where the headings should be. Set `HK_TX_FONT` or install
  `fonts-noto-cjk`.
- With nothing pending: `new_count: 0`, empty `groups`, empty `summary_lines`,
  and a `note` saying no message is needed.

---

## `hk-tx history`

Past numbers for one estate on one side of the market. Read-only.

| option | meaning |
|---|---|
| `--estate NAME` | required; the config name, as listed by `estates` |
| `--deal sale\|rental` | required |
| `--months N` | months of monthly medians (default: the config's `chart_months`) |
| `--limit N` | recent transactions to list (default 20) |
| `--chart` | also draw the line chart |
| `--out-dir DIR` | where to write it |

```json
{
  "ok": true,
  "estate": "泓都",
  "display": "泓都 Island Harbourview",
  "deal_type": "sale",
  "deal_label": "買賣",
  "archive": {"estate": "泓都", "deal_type": "sale", "total": 64, "priced": 63,
              "earliest": "2025-09-25", "latest": "2026-08-17"},
  "trend": { /* as below */ },
  "trend_line": "泓都 Island Harbourview · 買賣 呎價(實)：近90日中位數 …",
  "monthly": [{"month": "2025-10", "median_unit_price": 18444.5, "samples": 4}],
  "transactions": [ /* the same item shape as `report`, each with a `line` */ ],
  "images": []
}
```

`archive.earliest` is where the archive begins, not where the estate's history
begins. There is nothing behind it.

---

## `hk-tx trend`

呎價(實) direction and change for every recorded bucket.

| option | meaning |
|---|---|
| `--estate NAME` | limit to this estate. Repeatable. |
| `--deal sale\|rental` | limit to one side |

```json
{
  "ok": true,
  "as_of": "2026-08-29",
  "trends": [
    {
      "estate": "泓都",
      "label": "泓都 Island Harbourview",
      "deal_type": "sale",
      "deal_label": "買賣",
      "window_days": 90,
      "min_samples": 3,
      "recent":   {"from": "2026-06-01", "to": "2026-08-29",
                   "median_unit_price": 23380.0, "samples": 10},
      "previous": {"from": "2026-03-03", "to": "2026-05-31",
                   "median_unit_price": 23671.0, "samples": 20},
      "pct": -1.23,
      "direction": "down",
      "basis": "ok",
      "archive": {"transactions": 63, "priced": 63,
                  "earliest": "2025-09-25", "latest": "2026-08-17"}
    }
  ],
  "summary_lines": ["泓都 Island Harbourview · 買賣 呎價(實)：近90日中位數 …"]
}
```

- `basis` — `ok`, `insufficient` (either window had fewer than `min_samples`
  transactions), or `no_data` (nothing priced in the bucket). `pct` is `null`
  unless `basis` is `ok`.
- `direction` — `up`, `down`, `flat`, or `none` when there is no comparison.
  **`flat` and `none` are different answers.**
- Every figure is a median of 呎價(實) across **all** residential transactions in
  the estate, matched or not.

---

## `hk-tx transactions`

The archive, filtered. Read-only.

| option | meaning |
|---|---|
| `--estate NAME` | config name |
| `--deal sale\|rental` | |
| `--since ISO` / `--until ISO` | on 成交日期 |
| `--bedrooms N` | exact 間隔 |
| `--all` | include transactions that failed the entry's criteria |
| `--limit N` | default 30 |

Returns `count`, the `filters` applied, and `transactions` in the same item
shape as `report`.

---

## `hk-tx estates`

Validate the config and show each entry's criteria, state and archive.

```json
{
  "ok": true,
  "config_path": "…/config/estates.json",
  "db_path": "…/hk_transactions.db",
  "timezone": "Asia/Hong_Kong",
  "fetch_size": 100,
  "trend": {"window_days": 90, "min_samples": 3,
            "chart_months": 24, "chart_min_points": 3},
  "estates": [
    {
      "name": "泓都",
      "label": "泓都 Island Harbourview",
      "display": "泓都 Island Harbourview",
      "url": "https://hk.centanet.com/findproperty/list/transaction/…",
      "bedrooms": [2, 3],
      "bedroom_labels": ["2房", "3房"],
      "size_ranges": [{"low": 500.0, "high": 700.0, "label": "500-700呎"}],
      "track": ["sale", "rental"],
      "enabled": true,
      "state": {"seeded": 1, "last_ok_at": "…", "consecutive_failures": 0,
                "recent_yield": 93, "published_count": 286, "last_error": null},
      "archive": [{"deal_type": "sale", "total": 64, "priced": 63,
                   "earliest": "2025-09-25", "latest": "2026-08-17"}]
    }
  ]
}
```

A malformed config exits `10` with the offending field named. Run this after
every edit.

---

## `hk-tx runs`

Recent checks — the liveness surface.

| option | meaning |
|---|---|
| `--limit N` | default 5 |

```json
{
  "ok": true,
  "db_path": "…",
  "runs": [{"id": 12, "started_at": "…", "finished_at": "…", "status": "ok",
            "estates_checked": 3, "seen": 293, "added": 4, "matched": 2,
            "errors": 0, "detail": null}],
  "consecutive_failures": 0,
  "pending": 2
}
```

---

## Exit codes

| code | name | meaning | worth a message? |
|---|---|---|---|
| 0 | OK | | |
| 10 | `ERR_CONFIG` | `estates.json` or an env override is unusable; the field is named | yes — it is a fixable mistake |
| 11 | `ERR_DB` | the archive is missing or unreadable | yes — check `HK_TX_DB`, do not start a fresh one |
| 20 | `ERR_FETCH` | Centanet unreachable | no — transient, the next run retries |
| 21 | `ERR_PARSE` | retrieved, but the embedded payload no longer parses | **yes** — otherwise it reads as "no new transactions" for ever |
| 22 | `ERR_RENDER` | an image could not be drawn or the directory is unwritable | yes — send the text summary and say the images failed |
| 30 | `ERR_NOT_FOUND` | no such estate or nothing recorded for the bucket | yes — the payload lists what does exist |
