# Command surface and JSON shapes

Every command prints one JSON object on stdout and exits with a code from the
table at the bottom. `pending --count` is the single exception: it prints a bare
integer so a shell can gate on it.

All commands accept `-h`.

---

## `hk-supply check`

The daily cron entry. Reads the index page; if the published quarter is not in
the history CSV, downloads the PDF, extracts the figures and writes the row.
Draws nothing, sends nothing.

```
--no-download    report what is published without downloading or parsing the PDF
```

```json
{
  "ok": true,
  "checked_at": "2026-08-21T00:15:36+08:00",
  "published_quarter": "2026/Jun",
  "latest_in_history": "2026/Jun",
  "new_quarter": false,
  "appended": false,
  "figures": null,
  "printed_total": null,
  "total_matches_printed": null,
  "pending": 0,
  "pending_quarters": [],
  "overdue": false,
  "seeded": false,
  "history_file": "…/data/hk_units_supply_history.csv",
  "source": {
    "index_url": "https://www.hb.gov.hk/tc/publications/housing/private/pshpm/index.html",
    "pdf_url": "https://…/stat202606.pdf",
    "published_label": "2026年6月",
    "publisher": "Housing Bureau, HKSAR Government"
  }
}
```

When a quarter is new, `figures` is populated and `next_command` appears:

```json
{
  "new_quarter": true,
  "appended": true,
  "figures": {"land_ready": 16000, "being_built": 61000,
              "built_not_sold": 19000, "total": 96000},
  "printed_total": 96000,
  "total_matches_printed": true,
  "pending": 1,
  "pending_quarters": ["2026/Jun"],
  "next_command": "hk-supply report --commit"
}
```

| field | meaning |
|---|---|
| `published_quarter` | what the index page is offering right now |
| `new_quarter` | a row was written this run |
| `printed_total` | the total the PDF states in prose, or `null` if the sentence was not found. This is what gets stored in the CSV's Total column when it is within 3,000 of the sum of the components |
| `total_matches_printed` | `false` is **not** an error — each component is independently rounded to the nearest thousand, so the sum and a separately rounded total can differ. Worth a clause if you are quoting the total. Note `figures.total` in this payload is always the *sum*; the stored row carries the Bureau's printed figure |
| `overdue` | the next quarter is more than 100 days past its quarter end |
| `seeded` | this run was the first ever and absorbed the existing history silently |

---

## `hk-supply report`

Renders the three PNGs and returns everything needed to send them.

```
--quarter 2026/Jun   a specific quarter (default: the newest in the history)
--commit             stamp the quarter as delivered, clearing it from pending
--quarters 8         rows in the table (default 12, minimum 1)
--out-dir PATH       where to write the PNGs (default: the profile state dir)
```

**The table and the charts both end at `--quarter`, not at today.** A report
about 2023/Mar shows the twelve quarters up to and including 2023/Mar, with
2023/Mar as the highlighted top row. QoQ is still computed against the whole
history, so the oldest row in a short window keeps the percentage it earns from
the quarter before it.

```json
{
  "ok": true,
  "quarter": "2026/Jun",
  "prior_quarter": "2026/Mar",
  "figures": {"land_ready": 16000, "being_built": 61000,
              "built_not_sold": 19000, "total": 96000},
  "qoq": {
    "land_ready":     {"from": 19000, "to": 16000, "delta": -3000,
                       "pct": -15.789, "direction": "down", "basis": "prior_quarter"},
    "being_built":    {"…": "…"},
    "built_not_sold": {"…": "…"},
    "total":          {"…": "…"}
  },
  "summary_lines": [
    "HK private residential primary-market supply — 2026/Jun (香港私人住宅一手市場供應)",
    "Total 未來三至四年潛在供應: 96,000 units, -4.95% QoQ",
    "Land ready (可隨時動工): 16,000 units, -15.79% QoQ",
    "Being built (建築中未售): 61,000 units, -1.61% QoQ",
    "Completed unsold (現樓貨尾): 19,000 units, -5.00% QoQ",
    "QoQ is against 2026/Mar."
  ],
  "table": [{"quarter": "2026/Jun", "land_ready": 16000, "…": "…",
             "qoq": {"…": "…"}}],
  "table_quarters": 12,
  "images": [
    {"kind": "table", "path": "…/hk_supply_table_2026-Jun.png", "caption": "…"},
    {"kind": "chart_built_not_sold", "path": "…", "caption": "…"},
    {"kind": "chart_being_built", "path": "…", "caption": "…"}
  ],
  "cjk_font": "Noto Sans TC",
  "committed": true,
  "previously_reported": false,
  "history_file": "…",
  "source": {"…": "…"}
}
```

`direction` is a closed enum: `up`, `down`, `flat`, `none`. It is derived from
the raw delta, and it — not the sign of `pct` — is what decides a cell's colour.

`basis` is `prior_quarter` or `unavailable`. `unavailable` means the preceding
calendar quarter is not in the file, so no percentage is offered rather than a
six-month change being printed under a QoQ heading.

`cjk_font: null` means no font on this machine can draw Chinese, and the images
fell back to English-only labels. The report is still correct; the headings are
just monolingual. Fix it with `apt install fonts-noto-cjk`, or point
`HK_SUPPLY_FONT` at a font that is installed.

---

## `hk-supply pending`

```
--count    print a bare integer instead of JSON, for the cron gate
```

```json
{
  "ok": true,
  "pending": 1,
  "pending_quarters": ["2026/Jun"],
  "latest_in_history": "2026/Jun",
  "reported": {"2026/Mar": "2026-05-14T09:02:11+08:00"}
}
```

A quarter is pending when it is in the history CSV and has never been stamped by
`report --commit`. On the very first run the existing history is absorbed
silently, so a fresh install reports zero pending rather than eighteen.

---

## `hk-supply history`

The recorded quarters with their QoQ blocks. Draws no images and touches no
network.

```
--limit 12    how many quarters, newest first
```

```json
{
  "ok": true,
  "path": "…/data/hk_units_supply_history.csv",
  "quarters": 18,
  "latest": "2026/Jun",
  "columns": [{"key": "land_ready", "zh": "可隨時動工", "en": "Land ready"}],
  "rows": [{"quarter": "2026/Jun", "…": "…", "qoq": {"…": "…"}}]
}
```

---

## `hk-supply runs`

The liveness record: one row per `check`, failures included.

```
--limit 10
```

```json
{
  "ok": true,
  "path": "…/hk_supply_runs.jsonl",
  "runs": [{"at": "2026-08-21T00:15:36+08:00", "status": "ok",
            "quarter": "2026/Jun", "pending": 0, "overdue": false}],
  "consecutive_failures": 0,
  "latest_in_history": "2026/Jun",
  "next_expected": "2026/Sep",
  "overdue": false
}
```

`status` is `ok`, `new_quarter` or `error`; an `error` row carries `message`.

---

## `hk-supply source`

What the index page says right now, without touching the history or the ledger.
Use it to answer "has anything been published yet" without writing anything.

```json
{
  "ok": true,
  "href": "stat202606.pdf",
  "url": "https://…/stat202606.pdf",
  "quarter": "2026/Jun",
  "published_label": "2026年6月",
  "in_history": true,
  "latest_in_history": "2026/Jun"
}
```

---

## Errors

Every failure prints the same shape and exits with the matching code:

```json
{"ok": false, "error": "ERR_PARSE", "exit_code": 21,
 "message": "no 'stat<YYYYMM>.pdf' link found on the index page",
 "detail": {"heading_found": true, "remedy": "…"}}
```

| code | error | meaning |
|---|---|---|
| 0 | — | fine |
| 10 | `ERR_CONFIG` | a path or an environment override points somewhere unusable, or a numeric one will not parse. `detail.variable` names it |
| 11 | `ERR_HISTORY` | the CSV is missing, empty or malformed. `detail.line` names the row |
| 20 | `ERR_FETCH` | the Housing Bureau could not be reached. Transient; say nothing |
| 21 | `ERR_PARSE` | reached, but the expected link or figure is not there. **Report this one** |
| 22 | `ERR_RENDER` | an image could not be drawn, or the image directory is unwritable |
| 30 | `ERR_NOT_FOUND` | no such quarter in the history; `detail.available` lists recent ones |

`ERR_FETCH` and `ERR_PARSE` are split deliberately. A site that is down for an
hour is not news. A page whose layout changed is the failure that would otherwise
look exactly like a quarter nobody published — for a year.

---

## Environment

| variable | default |
|---|---|
| `HK_SUPPLY_HISTORY` | `data/hk_units_supply_history.csv` in the bundle |
| `HK_SUPPLY_STATE` | `~/.local/share/hermes-estates-analyst/hk_supply_state.json` |
| `HK_SUPPLY_RUNS` | `~/.local/share/hermes-estates-analyst/hk_supply_runs.jsonl` |
| `HK_SUPPLY_IMAGE_DIR` | `~/.local/share/hermes-estates-analyst/hk_supply_images` |
| `HK_SUPPLY_QUARTERS` | `12` rows in the report table |
| `HK_SUPPLY_IMAGE_RETENTION` | `30` days before a rendered PNG is swept |
| `HK_SUPPLY_TZ` | `Asia/Hong_Kong` |
| `HK_SUPPLY_TIMEOUT` | `30` seconds per request |
| `HK_SUPPLY_RETRIES` | `2`, on transport failure only — a 4xx is never retried |
| `HK_SUPPLY_FONT` | auto-detected; set to force a CJK font family by name |
| `HK_SUPPLY_INDEX_URL` | the Housing Bureau index page |
