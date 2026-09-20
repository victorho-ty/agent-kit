# Accounts and isolation

## The model

One `account` row per person. Two ids on the same row:

- **`id`** — a ULID, and the only thing every other table references. It appears
  in CLI flags, purge manifests, log files and directory names. A mistyped ULID
  is `ERR_ACCOUNT`; a mistyped small integer would silently hit someone else's
  data.
- **`telegram_user_id`** — unique, nullable. Maps an inbound sender to an
  account. Re-pointing it after a lost Telegram account is a one-row update.
- **`chat_id`** — where alerts are delivered. Stored separately because it is
  genuinely a different value: in a group chat, the chat id is not the sender's.

There is no separate identity table, and no support for two Telegram ids on one
account. One person, one account, and no coupon is ever shared.

## Onboarding

`config.yaml` carries the allowlist:

```yaml
accounts:
  allowlist: ["111222333", "444555666"]   # telegram user ids, as strings
```

On a message from a sender with no account:

- **In the allowlist** → create the account, using the sender's first name as the
  display name and the message's chat as `chat_id`. Then handle the message
  normally.
- **Not in the allowlist** → one "this bot is private" reply, and drop it.
  Nothing is written — not a row, not a file — so a stranger cannot consume
  storage.

Auto-creation only ever happens on the inbound message path. `couponctl account
add` is the manual path. An unknown `--account` on the CLI is always an error and
never an implicit create.

## What isolation guarantees

Every coupon, media file, inbox item and alert belongs to exactly one account.
There is no shared data and no cross-account read.

Three mechanisms make that structural rather than a habit:

1. **`AccountScope` is the only handle.** No function in `store`, `query`,
   `lifecycle`, `purge` or `alerts` accepts a bare database connection. Every
   statement carries `account_id = ?`. There is no admin variant that skips it.
2. **A foreign id is "not found."** `show`, `use`, `extend` and friends resolve
   within the scope only. Asking for another account's coupon returns exit 30
   with a response identical to that for a nonexistent id — so the CLI cannot be
   used to probe what exists elsewhere.
3. **`doctor` audits the invariants** and fails the run if any are violated.

Files are partitioned too: `media/<account_id>/` and `inbox/<account_id>/`. Paths
are always derived from the scope, never accepted from a caller, and one that
escapes its directory is `ERR_ACCOUNT`.

## Two things that look like bugs but are not

**The same image in two accounts becomes two files.** Media dedupe is keyed on
`(account_id, sha256)`, not on the hash alone. If it were global, one person
purging their last coupon would find the image held alive by someone else's
row — and "delete my data" would stop being true. A duplicated file is the
cheaper problem.

**The same coupon in two accounts is not a duplicate.** Dedupe is per account.
Two people holding the same voucher is normal. Within one account, an identical
merchant/title/expiry does flag `needs_review`, because a coupon book legitimately
contains identical vouchers and only a person can tell those apart.

## Alerts and delivery

`couponctl alerts due` returns coupons grouped by account, each group carrying
its own `chat_id`. There is no flat coupon list in the payload, so the only way
to read a coupon out is through the group that says where it goes.

Send each group to that group's `chat_id` and nowhere else. Never merge groups
into one message.

## Deleting an account

```bash
couponctl account delete --account <id>            # dry run, full manifest
couponctl account delete --account <id> --commit
```

Cascades to every coupon, media row, inbox row, then removes both directories.
Same protocol as purge: dry run, report, explicit confirmation, commit, report
totals. There is no suspension state and no undo.
