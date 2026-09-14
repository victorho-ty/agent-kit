---
name: fed-watch
description: Track what interest-rate futures are pricing for the next two FOMC meetings, and report when those odds move. Use when asked what the Fed is expected to do, what the odds of a cut or a hike are, whether rate expectations have shifted, when a scheduled rate-watch run is due, and when asked for the history or a chart of how the odds have moved.
---

# Fed watch

Deterministic Python fetches the futures prices and the effective rate, applies
CME FedWatch's published methodology, stores the history, detects what moved and
renders the charts. You own one job: deciding what is worth saying about it.

You never compute a probability here and you never open the CME website. Both
happen in Python, upstream of you. If a field is absent it is unknown — say so
rather than supplying it.

## Setup

Bundle root: `~/projects/hermes/profile-stock-analyst/fed-watch`.

```bash
fedctl <command> [options]
```

`fedctl` is a symlink at `~/.local/bin/fedctl` pointing at the project's
`.venv/bin/fedctl`. If missing, run from the bundle root instead:

```bash
cd ~/projects/hermes/profile-stock-analyst/fed-watch && .venv/bin/python -m fed_watch <command>
```

Every command prints one JSON object on stdout. Parse it. The one exception is
`check-changes --quiet`, which prints a bare `1` or `0` for shell use.

## These are not CME's numbers, and you must say so

The figures are **computed from 30d FedFund futures using CME's
methodology**. Against CME's own table the method is exact; the residual gap of roughly **one percentage
point** comes from the price source, because Yahoo serves a traded print and CME
publishes a settlement mid. `price_source` says which Yahoo series was used.

Every payload carries `attribution` and `price_source`. Attribute accordingly:
"futures are pricing about an 88% chance of a hike" is right. "CME FedWatch says
86.7%" is wrong — you did not read that page and the number is not theirs.

Never quote these to a third party as CME FedWatch figures.

## The commands

| command | what it does | wakes anybody? |
|---|---|---|
| `fedctl snap` | read the market now, store one row of history | no |
| `fedctl check-changes` | snap, then report what moved since the last report | only if something moved |
| `fedctl history --limit N` | the last N reported changes, summarised and charted | no |
| `fedctl meetings` | the calendar, and the contract behind each date | no |

### The scheduled run

One cron entry. `snap` on its own is for building history faster than changes
are reported; it is not needed if `check-changes` is on a timer.

```bash
fedctl check-changes    # every 30-60 min while US rate markets are open
```

**`quiet: true` means write nothing.** Not a sentence saying the odds held
steady. An alert that arrives empty teaches people to ignore the next one.

Gate a wake-up on the exit payload rather than running the agent every time:

```bash
[ "$(fedctl check-changes --quiet)" -eq 1 ] && hermes-run fed-watch-alert
```

## Reading a change report

`changes.changed` is the only thing to branch on.

**The baseline is the last snapshot that was *reported*, not the last poll.**
This is deliberate and it changes how you read `delta_pct`: a move of 4.7 points
may have accumulated over six hours and nine quiet polls. It is the move since
you last said something, which is the number worth reporting — but do not
describe it as having happened "in the last half hour" unless
`baseline_taken_at` says so. Quote that timestamp when the gap is wide.

Each moved meeting carries `outcomes`, and only the bands that cleared the
threshold appear. `largest_move_pct` is the headline. `implied_rate_from`/`_to`
and `expected_rate_from`/`_to` are the underlying rate move, in percent.

`changes.reason` on a first run means there was nothing to compare against, not
that everything moved. Say so plainly or say nothing.

`policy_changed: true` means **the Fed actually moved** — the effective rate or
the target range itself changed between the baseline and now. That is the real
event and it outranks every probability on the page. Lead with it.

A meeting marked `dropped` has passed or rolled off the tracked window; `new`
means it entered it. Neither is a change in the odds and neither should be
reported as one.

## Reading a snapshot

`meetings` is ordered — `ordinal` 1 is the next decision, 2 the one after it.

`outcomes` is the target-rate table, one row per 25bp band, contiguous and
always including `no change`. A band at `0.0` was priced at nothing; that is a
finding, not a gap. `label` is finished text (`25bp hike`, `no change`,
`50bp cut`) — use it rather than deriving the wording from `step`.

### The diagnostics are for you, not for the reader

`method`, `amplification` and `noisy` decide how much weight a number carries.
They are **never themselves worth reporting.**

| method | meaning |
|---|---|
| `clean_next_month` | read straight off a contract for a month with no meeting in it — no inversion, nothing amplified |
| `blend_inversion` | solved out of the meeting month's own contract, which multiplies any price error by `amplification` |

`noisy` appears **only when it is true**, and it is the bundle's verdict, not
yours — never compare `amplification` against a threshold yourself.

- `noisy` **absent** — the normal case. Say nothing whatsoever about it.
- `noisy: true` — add one clause saying that reading is rough, then carry on.

This is the only route by which any of the three reaches a message.

`status: "partial"` means only some meetings priced. The ones present are good;
the missing ones are named in `failures`. Never fill a gap from an earlier run.

## Charts

`charts` is a list of file paths, one per meeting, plotting each band's
probability across the reported changes. **Only moves are drawn** -- there is
no no-change line, because it is 1 minus the moves. Each line carries its
latest value as a printed percentage. `series` lists exactly what was drawn.

**The y axis is fitted to the data, not fixed at 0-100**, and the tick step
changes with it. Two charts are therefore not comparable by eye, and a steep
line may be a move of half a point. Never read a magnitude off the shape --
`changes` and `latest` carry the numbers.

**A chart is a file path to you, not an image.** Never describe a line, a slope
or a trend from one. Everything you say about the movement comes from `changes`
and `latest` in the same payload.

`charts_skipped` names meetings with nothing to draw — fewer than two reported
changes, or a market that has only ever priced no-change. Both are normal and
neither is an error; `reason` says which.

Charts are rendered portrait for a phone. `FED_WATCH_CHART_ORIENTATION=landscape`
is the override; do not use it unless the operator says they are at a desk.

## On demand

```bash
fedctl history --limit 20      # last 20 reported changes, summarised and charted
fedctl snap --meetings 3       # price a third meeting as well
fedctl check-changes --threshold 0.5
```

`history` also returns `net_change` — the move from the oldest snapshot in the
window to the newest, computed at a zero threshold so nothing is filtered. It is
the "where have we come from" number, and it is not the same as the last
reported change.

## When something looks wrong

`calendar_warning` means `config/fomc.json` is running out of FOMC dates. The
Fed publishes about a year ahead and this file is a copy; when it empties, every
command fails with `ERR_CONFIG` rather than guessing a date. Tell the operator
to add next year's from federalreserve.gov — do not invent one.

`ERR_INSUFFICIENT` from `history` means no changes have been reported yet, not
that nothing is stored. Waiting is the answer; re-running is not.

`ERR_FETCH` naming a contract usually means it has settled and been delisted, or
has not started trading. `fedctl meetings` shows which contract each date needs.

## Rules

- Never write to the database except through these commands.
- Never present these figures as CME FedWatch's published numbers.
- Never compute a probability, an implied rate or a delta yourself.
- Never describe what a chart looks like. You have a path, not a picture.
- A probability is not a forecast. Report what is priced; do not predict the
  decision, and never advise a trade or a position off the back of it.
- Never state or imply that the Fed will do something. The market pricing it at
  99% is still the market's opinion.
- When `quiet: true`, say nothing at all.
- **Never narrate a diagnostic field.** `method`, `amplification`, `noisy`,
  explain the number to you, not to the reader. Reporting that nothing is wrong with them is noise.
- Never run a poll to make something appear, and never loop one. Nothing moved
  is the normal answer.
- Do not re-report a change from memory. Everything unreported is in the
  payload, and everything in the payload is unreported.

Full command surface and JSON shapes: `references/cli.md`.
The calculation, its validation against CME, and its limits: `references/methodology.md`.
