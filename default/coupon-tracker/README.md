# Hermes coupon tracker

A Hermes Agent skill plus the deterministic Python tools behind it. Coupon
photos and messages arrive over Telegram; the agent extracts, the tools store,
evaluate and alert. Multi-account: every coupon belongs to exactly one Telegram
user, and one account's data is never visible to another.

```
coupon-tracker/
├── skills/coupon-tracker/
│   ├── SKILL.md                     # what the agent loads
│   └── references/
│       ├── cli.md                  # full command surface, JSON shapes, exit codes
│       ├── accounts.md             # identity model, isolation, onboarding
│       └── extraction-rules.md     # date ambiguity, closed predicate enum
├── coupon_tracker/                 # the deterministic tools
│   ├── cli.py                      # couponctl entrypoint
│   ├── accounts.py                 # AccountScope — the handle everything else takes
│   ├── store.py                    # CRUD, dedupe, ingest commit
│   ├── query.py                    # list / usable-now
│   ├── predicates.py               # closed condition enum + evaluation
│   ├── lifecycle.py                # use / unuse / void / extend / sweep-expiry
│   ├── purge.py                    # on-demand purge + media GC
│   ├── alerts.py                   # which coupons are due, grouped by account — sends nothing
│   ├── db.py / clock.py / config.py / errors.py / models.py
│   └── migrations/001_initial.sql
├── tests/                          # pytest, no network
├── config.example.yaml
├── docs/DESIGN.md                  # full design rationale
└── pyproject.toml
```

There is no Telegram client anywhere in this package. Hermes owns the channel,
the tools only ever get handed a message and hand back JSON.

## Install

```bash
cd ~/projects/hermes/coupon-tracker      # wherever you clone/copy this repo to
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"       # installs couponctl into this venv
```

`pip install -e .` (without `[dev]`) is enough to run the tool; the `dev`
extra adds `pytest` and `freezegun` for the test suite.

## Configure

`couponctl init` writes `config.yaml` next to itself and creates
`coupons.db`, `media/`, `inbox/`, `logs/` — the whole runtime state lives in
one directory:

```bash
couponctl init --root ~/projects/hermes/coupon-tracker
```

Or copy `config.example.yaml` to `config.yaml` in the same directory and
edit it by hand — every key is documented inline. The one setting you're
likely to touch before going live is the allowlist:

```yaml
accounts:
  allowlist: ["111222333"]   # telegram user ids permitted to auto-create an account
```

Everyone else who messages the bot gets one "this bot is private" reply and
nothing is written for them. You can also skip the allowlist and create
accounts by hand:

```bash
couponctl account add --name "Horace" --telegram-user 111222333 --chat-id 111222333
```

`config.yaml`, `coupons.db*`, `inbox/`, `media/` and `logs/` are all
`.gitignore`d — this is runtime state, not something to commit or ship in the
repo.

## Point Hermes at the skill

```bash
ln -s ~/projects/hermes/coupon-tracker/skills/coupon-tracker \
      ~/.hermes/skills/coupon-tracker
```

`SKILL.md` calls `couponctl <command> --json`. Either make sure that resolves
on PATH (the console script Hermes' shell will find after `pip install -e .`
in an activated venv), or start Hermes with the bundle root as its working
directory and fall back to `.venv/bin/couponctl` / `.venv\Scripts\couponctl.exe`
directly — see `SKILL.md`'s Setup section.

## Wire the daily alert

One cron entry, once a day (08:00 local is the convention the design uses — a
coupon expiring "today" is still usable at breakfast):

```bash
couponctl alerts due --commit --json
```

The agent relays each group in the result to that group's own `chat_id` —
never merges groups, never sends one group's coupons to another chat. See
`skills/coupon-tracker/references/cli.md` for the payload shape.

There is no purge cron entry, and there should never be one: deletion only
ever happens through conversation (`docs/DESIGN.md` §5).

## Verify

```bash
couponctl doctor --json     # integrity check across every account; exit 0 means clean
pytest -q                   # from the bundle root, with the venv active
```

Full design rationale — schema, the isolation guarantees, the purge protocol,
milestones — is in `docs/DESIGN.md`.
