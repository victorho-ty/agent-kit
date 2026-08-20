# stock-desk

Swing-trading watchlist and portfolio tools for the Hermes `stock-analyst`
profile. Deterministic Python finds the setups, renders the charts and remembers
what has already been said; the agent decides what is worth writing.

**There is no Telegram module** Hermes owns the channel. These tools return JSON and file paths.

## Two jobs, one bundle

- **Watchlist** — scanned daily for the compression that precedes a breakout,
  reported 30 minutes before each market's open.
- **Portfolio** — positions watched for corporate events and news, alerted on the
  event and never on a schedule.

One bundle because they share every expensive part: the bar cache, the indicator
engine, the news dedupe table and the chart renderer.

## Install

```bash
cd ~/projects/hermes/profile-stock-analyst/stock-desk
uv sync
ln -s "$PWD/.venv/bin/stockctl" ~/.local/bin/stockctl
```

State lives in `~/.local/share/hermes-stock-analyst/` — scoped to the **profile**,
not the bundle, so a second bundle added to this profile shares one state
directory and nothing from another profile can read it.

## Cron

```cron
# Silent. Writes to the database, tells nobody.
30 21 * * 1-5   stockctl sync
0  *  * * 1-5   stockctl news poll >/dev/null && [ "$(stockctl pending --count)" -gt 0 ] && hermes-run stock-desk-alerts
15 21 * * 1-5   stockctl events refresh

# Speaks. One entry per market -- run `stockctl schedule` for the times.
0  9  * * 1-5   hermes-run stock-desk-report   # 30 min before the US open
```

Detection is polled on a schedule; alerting is driven by events. They are
decoupled through the database, so the poller can run as often as you like
without costing tokens or interrupting anyone.

## What it detects

> High volatility → consolidation → progressively smaller swings → breakout

Each arrow is a separate test. A base that contracts but sits in a still-wide
range is `basing`, not `coiled` — the squeeze gauge holds a veto, because the
premise is that volatility has *fallen*.

Stages: `triggered`, `coiled`, `failed`, `basing`, `expansion`, `none`. Only the
first three earn prose; the rest get a one-line status string rendered in Python.
That is what stops a forty-ticker watchlist costing forty paragraphs.

Full definitions: `skills/stock-desk/references/setups.md`.

## Layout

```
stock_desk/
  indicators.py compression.py setups.py   pure stdlib, no network, no clock
  providers/    bars.py                    yfinance primary, Stooq fallback
  news.py       events.py                  fetch on a timer, alert on an event
  portfolio.py                             average cost open, FIFO realised
  charts.py     report.py  brief.py  cli.py
```

The detection maths depends on none of the runtime dependencies, so the whole
test suite runs without pandas, without a network and without a market being
open:

```bash
.venv/bin/python -m pytest tests -q     # 100 tests, ~0.3s
```

## Design notes

`docs/DESIGN.md` covers why the schedule is decoupled from the alerting, why the
pivot excludes the current bar, and which false positives the thresholds were
tuned against.
