---
name: household-expenses
description: Record, categorise and report household spending sent over Telegram. Use when a household member reports what they spent ("haircut $300; dinner $50; MTR $5.6"), asks how much they or the household spent this month or in a past month, asks for a breakdown by category or by member, or asks for an expense chart, report or image.
---

# Household expenses

Deterministic Python tools own parsing, storage, aggregation and charting. You own
exactly one judgement call: deciding which category an unrecognised expense keyword
belongs to. Everything else goes through the CLI.

## Setup

Bundle root: `~/projects/hermes/household-expenses`.

Prefer the installed console script — it works from any working directory and runs
on the project's own virtualenv:

```bash
expense-tracker <command> [options]
```

`expense-tracker` is a symlink at `~/.local/bin/expense-tracker` pointing at the
project's uv-managed venv interpreter (`.venv/bin/python`, Python 3.11 — satisfies
`requires-python = ">=3.11"` in `pyproject.toml`). The console script's shebang
pins that interpreter, so the command works regardless of which `python` is on
PATH and does not depend on the Hermes runtime venv. If the symlink is missing,
run from the bundle root instead:

```bash
cd ~/projects/hermes/household-expenses
.venv/bin/python -m expense_tracker <command> [options]
```

Every command prints one JSON object on stdout. Parse it — never guess at totals,
and never do arithmetic yourself.

Optional environment overrides: `HOUSEHOLD_EXPENSES_DB` (default
`~/.local/share/hermes-expenses/expenses.db`), `HOUSEHOLD_EXPENSES_TZ` (default
`Asia/Hong_Kong`), `HOUSEHOLD_EXPENSES_CURRENCY` (default `HKD`),
`HOUSEHOLD_EXPENSES_REPORT_DIR`.

## Recording an expense message

When a member sends spending, pass the message through verbatim along with **the
sender and the inbound message timestamp**. Do not pre-parse the text, do not split
items yourself, do not convert the amounts.

```bash
expense-tracker add \
  --member "Alice" \
  --timestamp "2026-08-02T19:14:00+08:00" \
  --message-id "tg:44821" \
  --text "haircut \$300; dinner \$50; Bus \$4.8; MTR \$5.6; Books \$150"
```

- `--timestamp` accepts ISO8601 or a unix epoch. Always pass the real message
  timestamp; the tool buckets days and months from it, not from the current time.
- `--message-id` makes the call idempotent — re-sending the same message stores
  nothing twice. Pass it whenever the platform gives you one.
- If a member's Telegram handle differs from the name they should appear under,
  register it once: `expense-tracker alias --alias "@alice_hk" --member "Alice"`.
- The parser splits items ONLY on `;`, newlines, `、` and non-thousands commas —
  NOT on "and" or spaces. `MTR $5.9 and bus $4.8` parses as ONE item (last
  amount wins, description mangled). If a member lists items without separators,
  send each item as its own `add` call with the same timestamp/member instead of
  rewording the text.

### The self-improving categorisation loop

`add` returns an `unmapped` list of item descriptions the keyword mapping did not
recognise. Those rows are stored as `Uncategorized`. When `unmapped` is non-empty:

1. Choose a category for each description from `valid_categories` in the same
   response. Judge from the wording — "poke bowl" is Food & Drinks, "sneakers" is
   Shopping, "physio" is Health.
2. Persist the decisions in one call. This also backfills every past row with the
   same keyword:

```bash
expense-tracker learn --map '{"poke bowl": "Food & Drinks", "sneakers": "Shopping"}'
```

3. Confirm the saved items to the member in one short reply — total, and any newly
   learned keyword so they can object.

Use the item's **normalised description as the keyword** (lowercase, no punctuation).
Prefer the shortest keyword that generalises: learn `"physio"` rather than
`"physio session for knee"`, so the next message matches without another decision.
The mapping is checked longest-keyword-first, so a specific multi-word keyword
still beats a generic single word.

If a member corrects a categorisation, re-learn it with `--source user`:
`expense-tracker learn --map '{"bar": "Entertainment"}' --source user`.

Also check `duplicates` (already stored, say nothing was double-counted) and
`ignored` (chunks with no amount — mention them so nothing is silently dropped).

## Answering text queries

```bash
expense-tracker query --month 2026-07                    # whole household
expense-tracker query --month 2026-07 --member "Alice"   # one member
expense-tracker query --month 2026-07 --top-days 5
expense-tracker year --year 2026
expense-tracker list --month 2026-07 --member "Alice" --limit 20
```

`query` returns `total`, `count`, `by_category` (with `total`, `n` and `pct`) and
`by_member`. Omit `--month` for the current month. Answer with the numbers as
returned; state the currency; keep it to a short table or a few lines.

If `by_category` contains `Uncategorized`, run the learn loop above on
`expense-tracker unmapped` before answering, so the breakdown is complete.

## Producing the image report

```bash
expense-tracker report --month 2026-08 --out /tmp/expenses_2026-08.png
```

Add `--member "Alice"` for a single member. The response carries `image_path`;
send that file back to the member. One PNG contains all three panels:

- a category pie with each slice directly labelled with its dollar total and share,
- a table of the top 5 days in the month, broken down by category,
- a bar chart of every monthly total in that calendar year up to now, stacked
  by category with the HKD value printed inside each segment.

Do not rebuild these charts yourself and do not describe the chart at length —
send the image with a one-line summary of the month's total.

## Rules

- Never write to the database except through these commands.
- Never invent an amount, a total or a category that the tools did not return.
- Categories are a closed set — `learn` rejects anything outside it. To add a new
  category, edit `CATEGORIES` in `expense_tracker/categories.py` and give it a
  colour slot in `expense_tracker/report.py`.
- Amounts are stored in one currency per row; the tools do not convert. If a member
  reports a foreign-currency amount, ask before storing.

Full command surface and JSON shapes: `references/cli.md`.
