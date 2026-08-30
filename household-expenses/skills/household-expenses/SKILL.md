---
name: household-expenses
description: Record, categorise and report household spending. Use when a member reports what they spent ("haircut 300; dinner 50; MTR 5.6"), asks what they or the household spent this month or a past month, wants a breakdown by category or by member, or asks for an expense chart.
---

# Household expenses

Deterministic Python tools own parsing, storage, aggregation and charting. Your
one judgement call is which category an unrecognised keyword belongs to. Every
command prints one JSON object — parse it, never do the arithmetic yourself.

```bash
expense-tracker <command> [options]
```

Console script at `~/.local/bin/expense-tracker`, shebang-pinned to the project
venv, so it works from any directory. If the symlink is missing, run from the
bundle root `~/projects/hermes/hermes-household-expenses` with
`.venv/bin/python -m expense_tracker <command>`.

Optional overrides: `HOUSEHOLD_EXPENSES_DB` (default
`~/.local/share/hermes-expenses/expenses.db`), `HOUSEHOLD_EXPENSES_TZ`
(`Asia/Hong_Kong`), `HOUSEHOLD_EXPENSES_CURRENCY` (`HKD`),
`HOUSEHOLD_EXPENSES_REPORT_DIR`.

## Recording an expense message

Pass the message through verbatim with **the sender**. Do not pre-parse the
text, split the items or convert the amounts.

```bash
expense-tracker add --member "Alice" --message-id "tg:44821" \
  --text "haircut 300; dinner 50; Bus 4.8; MTR 5.6; Books 150"
```

- **Omit `--timestamp`** — the tool stamps the row in `Asia/Hong_Kong`; never
  shell out to `date`. Pass it (ISO8601 or unix epoch) only when the spending
  happened at another time: a queued message drained late, or past spending.
- `--message-id` makes the call idempotent — the same message stores nothing
  twice. Pass it whenever the platform gives you one.
- Items split ONLY on `;`, newlines, `、` and non-thousands commas — **not on
  "and" or spaces**. `MTR 5.9 and bus 4.8` parses as ONE item (last amount
  wins, description mangled). If a member lists items without separators, send
  each as its own `add` call rather than rewording the text.
- Handle ≠ display name: register it once with
  `expense-tracker alias --alias "@alice_hk" --member "Alice"`.

Act on the response: `duplicates` (already stored — say nothing was
double-counted), `ignored` (chunks with no amount — mention them so nothing is
silently dropped), `unmapped` (below).

### The self-improving categorisation loop

`unmapped` lists descriptions the keyword mapping did not recognise; those rows
are stored `Uncategorized`. When it is non-empty, choose a category for each
from `valid_categories` in the same response — judge from the wording, "poke
bowl" is Food & Drinks, "sneakers" is Shopping, "physio" is Health — then
persist them in one call, which also backfills every past row with that keyword:

```bash
expense-tracker learn --map '{"poke bowl": "Food & Drinks", "sneakers": "Shopping"}'
```

Use the item's **normalised description as the keyword** (lowercase, no
punctuation) and the shortest form that generalises: `"physio"`, not `"physio
session for knee"`, so the next message needs no decision. Matching is
longest-keyword-first, so a specific multi-word keyword still beats a generic
one. If a member corrects a categorisation, re-learn it with `--source user`.

Then confirm the saved items in one short reply: the total, plus any newly
learned keyword so the member can object.

## Answering text queries

```bash
expense-tracker query --month 2026-07 [--member "Alice"] [--top-days 5]
expense-tracker year --year 2026
expense-tracker list --month 2026-07 --member "Alice" --limit 20
```

`query` (omit `--month` for the current month) returns `total`, `count`,
`by_category` with `total`/`n`/`pct`, and `by_member`. Answer with the numbers
as returned, state the currency, keep it to a few lines or a short table.

If `by_category` contains `Uncategorized`, run `expense-tracker unmapped`
through the learn loop before answering, so the breakdown is complete.

## Producing the image report

```bash
expense-tracker report --month 2026-08 --out /tmp/expenses_2026-08.png
```

Add `--member "Alice"` for one member. Send the file at `image_path` with a
one-line summary of the month's total. The single PNG already holds all three
panels — category pie with dollar totals and shares on the slices, top-5-days
table by category, and year-to-date monthly bars stacked by category. Do not
rebuild them and do not describe them at length.

## Rules

- Never write to the database except through these commands.
- Never invent an amount, total or category the tools did not return.
- Categories are a closed set — `learn` rejects anything outside it. To add one,
  edit `CATEGORIES` in `expense_tracker/categories.py` and give it a colour slot
  in `expense_tracker/report.py`.
- One currency per row and no conversion: ask before storing a foreign-currency
  amount.

Full command surface and JSON shapes: `references/cli.md`.
