# hk-transaction-tracker

Watches Centanet 成交 records for a configured list of Hong Kong estates and
reports the new 買賣 and 租賃 deals that match the 間隔 and 面積(實) worth
hearing about — with a phone-readable table, a 呎價 trend per estate, and a
SQLite archive that outlives what the source will serve.

Part of the Hermes **estates-analyst** profile, alongside `hk-estates-supply`.

## What the code does, and what the model does

The code does all of it: fetch, decode, split sale from rental, apply the
criteria, dedupe, archive, take every median, draw every image, and write every
line of the summary. The model sends the message and answers questions about it.
It never computes a number.

## Quick start

```bash
uv sync
hk-tx estates                       # validate the config
hk-tx check --estate 泓都 --dry-run  # see what the criteria catch, writing nothing
hk-tx check                         # first run seeds silently
hk-tx report --commit               # the summary, once there is something to say
```

## The two-clock arrangement

Detection is polled and reporting is driven by a ledger, so the model is woken
only on the days something actually transacted:

```bash
hk-tx check >/dev/null
[ "$(hk-tx pending --count)" -gt 0 ] && hermes cron run <report-job>
```

The check is registered as a plain command. It costs no tokens on the many days
when the answer is "nothing new".

## Layout

```
hk_transaction_tracker/
  nuxt.py       decode the page's embedded __NUXT__ payload
  extract.py    payload -> transactions, 買賣/租賃 split, car parks dropped
  match.py      the 間隔 × 面積 criteria, and what a missing dimension does
  db.py         SQLite: the archive, the delivery ledger, the run log
  check.py      one pass: fetch, judge, store, say nothing
  trend.py      呎價(實) medians, windows and monthly series
  render.py     the grouped tables and the line charts
  report.py     grouping and the finished summary lines
  cli.py        one JSON object per command
  config/       estates.json and its validator
skills/hk-transaction-tracker/   the skill the agent loads
docs/DESIGN.md                   why it is built this way
```

## Notes

- Centanet serves **at most 100 records per estate** and honours no offset, so
  the archive is the only memory of anything older. Nothing here deletes a row.
- 呎租 and 呎價 live in the same source field. Nothing in this package averages
  across the two.
- Rows with no published 面積(實) — about a quarter of sale rows — are reported
  in their own group and excluded from every median and chart.

Tests: `uv run pytest`. The suite runs without a network, against a captured
page in `tests/fixtures/`.
