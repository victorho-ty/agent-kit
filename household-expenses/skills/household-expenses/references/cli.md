# CLI reference

All commands are `expense-tracker <command>` (console script on the project
venv; fallback: `python -m expense_tracker` from the bundle root).
All output is a single JSON object on stdout. Exit code is `0` unless noted.

## `init`

Creates the database and seeds the starter keyword mapping. Safe to re-run; it
never overwrites a learned entry. Every other command does this implicitly.

```json
{"ok": true, "db": "/home/hermes/.local/share/hermes-expenses/expenses.db", "categories": ["Food & Drinks", "..."]}
```

## `add`

| Option | Required | Notes |
|---|---|---|
| `--member` | yes | Sender name, handle or id. Resolved through the alias table. |
| `--text` | yes | Raw message. Items split on `;`, newlines, `、` and commas that are not thousands separators. |
| `--timestamp` | no | ISO8601 (with or without offset) or unix epoch. Defaults to now. |
| `--message-id` | no | Makes the call idempotent per item. |
| `--currency` | no | Defaults to `HOUSEHOLD_EXPENSES_CURRENCY` / `HKD`. |

```json
{
  "member": "Alice",
  "timestamp": "2026-08-02T19:14:00",
  "currency": "HKD",
  "items": [
    {"id": 1, "description": "haircut", "amount": 300.0, "category": "Beauty"},
    {"id": 2, "description": "dinner", "amount": 50.0, "category": "Food & Drinks"},
    {"id": 3, "description": "Bus", "amount": 4.8, "category": "Transportation"},
    {"id": 4, "description": "MTR", "amount": 5.6, "category": "Transportation"},
    {"id": 5, "description": "Books", "amount": 150.0, "category": "Shopping"}
  ],
  "total": 510.4,
  "unmapped": [],
  "duplicates": [],
  "ignored": []
}
```

Amount parsing: an optional currency marker (`$`, `HK$`, `US$`, `¥`, `€`, `£`,
`元`, `蚊`) plus a number with optional thousands separators and decimals. A
currency-marked number wins; otherwise the last number in the chunk wins, so
`2 coffees 96` stores `96`. Chunks with no number land in `ignored`.

Rows whose description is not in the mapping are stored with category
`Uncategorized` and listed in `unmapped`, and the response then also carries
`valid_categories`.

## `learn`

```bash
python -m expense_tracker learn --map '{"poke bowl": "Food & Drinks"}' [--source llm|user]
```

Upserts keyword → category and re-resolves every still-uncategorized row.

```json
{
  "learned": [{"keyword": "poke bowl", "category": "Food & Drinks"}],
  "rejected": [],
  "recategorized": [{"id": 7, "description": "poke bowl", "category": "Food & Drinks"}],
  "ok": true
}
```

A keyword with an unknown category is rejected, `ok` is `false`, and
`valid_categories` is returned. Invalid JSON in `--map` exits `1`.

## `query`

`--month YYYY-MM` (default current month), `--member NAME`, `--top-days N`.

```json
{
  "month": "2026-07", "member": null, "total": 12480.5, "count": 96,
  "by_category": [{"category": "Food & Drinks", "total": 5120.0, "n": 44, "pct": 41.0}],
  "by_member": [{"member": "Alice", "total": 7100.5, "n": 52}],
  "top_days": [{"day": "2026-07-19", "total": 1840.0, "by_category": {"Shopping": 1500.0, "Food & Drinks": 340.0}}]
}
```

## `year`

`--year YYYY` (default current year), `--member NAME`.

```json
{"year": "2026", "member": null, "months": [{"month": "2026-01", "total": 9800.0, "count": 71}], "total": 88120.0}
```

## `list`

`--month`, `--member`, `--limit` (default 50). Newest first.

```json
{"month": "2026-07", "member": "Alice", "expenses": [
  {"id": 5, "ts": "2026-07-31T20:02:00", "member": "Alice", "description": "dinner",
   "category": "Food & Drinks", "amount": 220.0, "currency": "HKD"}]}
```

## `report`

`--month` (default current month), `--member`, `--out PATH`. Writes one PNG with
the category pie, the top-5-days table and the year-to-date monthly bars.

```json
{"ok": true, "image_path": "/tmp/expenses_2026-08.png", "month": "2026-08", "member": null, "total": 5210.4}
```

## `categories`

```json
{"categories": ["Food & Drinks", "..."], "mapping": {"haircut": "Beauty", "dinner": "Food & Drinks"}}
```

## `unmapped`

Stored rows still awaiting a category, grouped by keyword and ordered by frequency.

```json
{"unmapped": [{"keyword": "poke bowl", "description": "poke bowl", "n": 3, "total": 285.0}],
 "valid_categories": ["Food & Drinks", "..."]}
```

## `alias`

```bash
python -m expense_tracker alias --alias "@alice_hk" --member "Alice"
```

## `delete`

`--id N`. Exits `1` and returns `{"ok": false}` when the id does not exist.

## Schema

`expenses(id, member, description, keyword, category, amount, currency, ts, ts_utc,
message_id, item_index, source_text, created_at)` — `ts` is local time and drives
every day/month bucket. `(message_id, item_index)` is unique when `message_id` is
present.

`keyword_category(keyword, category, source, hits, created_at, updated_at)` —
`source` is `seed`, `llm` or `user`.

`members(alias, member, created_at)` — lowercased alias → display name.
