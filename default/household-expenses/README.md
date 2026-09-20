# Hermes household expense tracker

A Hermes Agent skill plus the deterministic Python tools behind it. Household
members message expenses over Telegram; the agent stores, categorises, queries and
charts them.

```
hermes-household-expenses/
├── skills/household-expenses/
│   ├── SKILL.md              # what the agent loads
│   └── references/cli.md     # full command surface and JSON shapes
├── expense_tracker/          # the deterministic tools
│   ├── cli.py                # python -m expense_tracker <command>
│   ├── parser.py             # message text -> (description, amount) items
│   ├── categories.py         # closed category set + learnable keyword mapping
│   ├── db.py                 # SQLite schema and writes
│   ├── ingest.py             # one inbound message -> stored rows
│   ├── queries.py            # month / day / year aggregations
│   ├── report.py             # the PNG report
│   └── config.py             # env-var configuration
├── tests/                    # pytest, no network
├── requirements.txt
└── pyproject.toml
```

## Install on Ubuntu

```bash
unzip hermes-household-expenses.zip -d ~/hermes
cd ~/hermes/hermes-household-expenses
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m expense_tracker init
```

Point Hermes at the skill — either copy it into the agent's skills directory or
symlink it:

```bash
ln -s ~/hermes/hermes-household-expenses/skills/household-expenses ~/.hermes/skills/household-expenses
```

The skill runs `python -m expense_tracker …` from the bundle root, so either start
Hermes with that as the working directory or install the package into the agent's
environment (`pip install -e .`, which also exposes an `expense-tracker` console
script).

## Configuration

| Variable | Default |
|---|---|
| `HOUSEHOLD_EXPENSES_DB` | `~/.local/share/hermes-expenses/expenses.db` |
| `HOUSEHOLD_EXPENSES_TZ` | `Asia/Hong_Kong` |
| `HOUSEHOLD_EXPENSES_CURRENCY` | `HKD` |
| `HOUSEHOLD_EXPENSES_REPORT_DIR` | `<db dir>/reports` |

The timezone matters: months and days are bucketed from the stored timestamp
converted to local time, so a message sent at 00:30 lands on the right day. `add`
stamps the row itself; `--timestamp` is only for back-filling past spending.

## How it fits together

Hermes owns the Telegram connection and passes each message through:

```bash
python -m expense_tracker add --member "Alice" \
  --message-id "tg:44821" --text "haircut \$300; dinner \$50; Bus \$4.8; MTR \$5.6; Books \$150"
```

Parsing, categorisation, storage, aggregation and charting are all deterministic
Python. The agent's only judgement call is categorising a keyword the mapping has
not seen — `add` returns those under `unmapped`, the agent picks a category, and
`learn` persists it and backfills every past row with that keyword. That is the
self-improving loop; it costs no extra API calls because the agent is already in
the conversation.

## Categories

`Food & Drinks`, `Shopping`, `Transportation`, `Entertainment`, `Beauty`,
`Health`, `Housing & Utilities`, `Education`, `Other`. Closed set — `learn`
rejects anything else.

The chart palette carries eight distinct, colourblind-validated hues, one fixed
slot per category, plus grey for `Other`. Adding a **ninth** spending category
therefore means either folding an existing one into `Other` or re-validating a new
palette (`CATEGORIES` in `categories.py`, `SLOTS` in `report.py`).

## Data model

`expenses(id, member, description, keyword, category, amount, currency, ts, ts_utc,
message_id, item_index, source_text, created_at)` — `ts` is local time.
`(message_id, item_index)` is unique, so replaying a message stores nothing twice.

`keyword_category(keyword, category, source, hits, created_at, updated_at)` —
`source` is `seed`, `llm` or `user`.

`members(alias, member, created_at)` — maps a Telegram handle or id to a display
name.

Nothing is ever deleted except through `delete --id`.

## Tests

```bash
pip install pytest && python -m pytest -q
```
