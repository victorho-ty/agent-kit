# couponctl — command surface

Every command accepts `--json` and prints one JSON object. Global flags
(`--json`, `--config`, `--now`) work before or after the subcommand.

`--now <ISO8601>` freezes the clock. Use it for replays and testing, never to
fake a result.

## Account selection

Every command that touches coupon data needs an account. Precedence:

1. `--account <account_id>`
2. `--telegram-user <telegram_user_id>`
3. `COUPONCTL_ACCOUNT` environment variable

Missing or unknown is exit 12 `ERR_ACCOUNT`. There is no default account.

## Exit codes

Branch on these. Never parse stderr.

| Code | Name | Meaning |
|---|---|---|
| 0 | `OK` | |
| 10 | `ERR_CONFIG` | config.yaml missing or invalid |
| 11 | `ERR_DB` | database error; also `doctor` finding problems |
| 12 | `ERR_ACCOUNT` | account missing, unknown, duplicate telegram id, or a payload/path from another account |
| 20 | `ERR_PREDICATE_SCHEMA` | a condition used a kind outside the closed enum; nothing written |
| 21 | `ERR_CANDIDATE_MISMATCH` | candidate count disagreement |
| 22 | `ERR_DEDUPE_COLLISION` | |
| 30 | `ERR_NOT_FOUND` | no such coupon **in this account** — identical response to a nonexistent id |
| 31 | `ERR_ILLEGAL_TRANSITION` | e.g. using a voided coupon |
| 50 | `ERR_PURGE_UNSAFE` | purge found anomalies; clean cases still ran |

An error payload is `{"ok": false, "code": "ERR_...", "exit_code": 12,
"message": "...", "details": {...}}`.

## Setup

```bash
couponctl init [--root <dir>]     # config.yaml, directories, database
couponctl migrate                 # apply pending migrations
couponctl doctor                  # integrity across every account; exit 11 if unclean
```

`doctor` checks: rows whose `account_id` names no account, coupons pointing at
another account's media, media files with no row, rows with no file, unreferenced
media rows, and media directories belonging to no account.

## Accounts

```bash
couponctl account add --name "Horace" [--telegram-user 111] [--chat-id 111]
couponctl account list                                  # with per-status counts
couponctl account show --account <id>
couponctl account set --account <id> [--name X] [--telegram-user Y] [--chat-id Z]
couponctl account delete --account <id> [--commit]      # dry run by default
```

`account add` with a telegram id already in use is exit 12 — it names the owning
account in `details`.

`account delete` cascades to every coupon, media row and inbox row, then removes
`media/<id>/` and `inbox/<id>/`. Dry run returns the same manifest and touches
nothing.

## Coupons

```bash
couponctl add --account <id> --merchant "..." --title "..." --expires-on YYYY-MM-DD \
    [--code X] [--value-text "$20 off"] [--uses 1] [--notes "..."] [--media <path>]
couponctl add --account <id> --json-file <path>

couponctl commit-candidates <path> --account <id> [--auto-confirm]

couponctl list --account <id> [--status active] [--merchant X] \
    [--expiring-within 7] [--include-expired] [--limit N]
couponctl usable-now --account <id> [--at "2026-08-05T19:30"] [--channel dine_in] \
    [--location "Causeway Bay"] [--payment-method X] [--spend 250]
couponctl show <id> --account <acct>
couponctl review --account <id> [--resolve <coupon_id> --as active|void]
```

`list` excludes `expired` and `void` unless asked. `usable-now` returns only
`active`, unexpired coupons whose conditions do not explicitly fail, each with a
`caveats` array of human-readable strings.

## Lifecycle

```bash
couponctl use <id> --account <acct> [--uses 1]
couponctl unuse <id> --account <acct> [--force]     # --force beyond undo_window_hours
couponctl void <id> --account <acct> --reason "..."
couponctl extend <id> --account <acct> --to YYYY-MM-DD
couponctl sweep-expiry --account <acct> [--commit]
```

Each returns `{"previous_status", "new_status", "uses_remaining", "no_op"}`.
`no_op: true` with exit 0 means the coupon was already in that state — harmless,
by design, so a double-tap costs nothing.

## Alerts

```bash
couponctl alerts due [--commit] [--account <id>]
```

Unscoped by default: walks every account. `--commit` persists the expiry sweep it
runs first; without it nothing is written.

```json
{
  "ran_at": "2026-08-05T08:00:00+08:00",
  "dry_run": false,
  "alert_days_before": 1,
  "groups": [
    {"account_id": "01J…", "display_name": "Horace", "chat_id": "111",
     "coupons": [{"merchant": "Cafe de Coral", "title": "$20 off",
                  "alert_kind": "pre_expiry", "days_left": 1, "...": "..."}]}
  ],
  "skipped": [], "failures": [],
  "totals": {"accounts_with_alerts": 1, "coupons": 1, "skipped": 0, "failed": 0}
}
```

There is deliberately **no** top-level coupon list. Send each group to its own
`chat_id` and nowhere else.

`alert_kind` is `expiry_day` (expires today) or `pre_expiry`. `skipped` holds
accounts with no `chat_id`. `failures` holds accounts that raised; the rest still
ran. Exit is non-zero only if every account failed.

No sent-ledger exists: a coupon in the window reappears every run until used or
expired. `alert_days_before` controls the repeat rate.

## Purge

```bash
couponctl purge --account <id> [--commit] [--include-void] \
    [--older-than N] [--merchant X] [--id <coupon_id> ...]
couponctl purge-manifest --last
```

`--account` is required; there is no all-accounts purge. Default target set is
`used` + `expired`. Dry run is the default and produces the same manifest shape
as `--commit`.

Manifest fields that matter when reporting back:

- `coupons_purged` — what went
- `media_deleted` — images removed, with `released_by`
- `media_held` — images kept because surviving coupons still reference them, with
  `refs` and `held_by`. **Always report these**; they are the proof that a shared
  image was not taken with one of its coupons.
- `anomalies.orphan_files` / `anomalies.missing_files` — reported, never
  auto-deleted; their presence is exit 50
- `totals.bytes_freed`

Every manifest is also written to `logs/purge-<account_id>-<timestamp>.json`.
