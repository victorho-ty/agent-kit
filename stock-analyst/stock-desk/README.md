# stock-desk

Swing-trading watchlist and portfolio tools for the Hermes `stock-analyst`
profile. Deterministic Python finds the setups, renders the charts and remembers
what has already been said; the agent decides what is worth writing.

Hermes owns the channel. These tools return JSON and file paths.

## Four readings, one bundle

- **Technical** — the compression that precedes a breakout.
- **News** — from Yahoo Finance and Alpha Vantage over MCP, filtered by a
  materiality classifier before anything can reach a report.
- **Sector** — whether a name moved with its group or against it.
- **Macro** — Fed funds, Treasury yields and the 2s10s curve, reported only on
  change.

Plus the portfolio, watched for corporate events and alerted on the event rather
than on a schedule. One bundle because they share every expensive part: the bar
cache, the indicator engine, the news dedupe table and the chart renderer.

**Python is the MCP client, not the agent.** That is the decision the token
budget rests on — raw feed JSON is ~128k tokens a day; the report the agent
actually reads is ~1.7k.

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

Two entries. Each runs sync, events, news, macro and report in order and returns
one payload.

```cron
0  9  * * 1-5   hermes-run stock-desk-morning    # 09:00 HKT
0  9  * * 1-5   hermes-run stock-desk-preopen    # 30 min before the NYSE open (TZ-dependent)
```

```bash
stockctl run morning-hkt --commit
stockctl run pre-us-open --commit
```

Run `stockctl schedule` for the pre-open time — it moves with US daylight saving
and Hong Kong does not.

Between runs the desk is silent. Detection writes to the database; alerting is
driven by a null `notified_at`, so a poll costs no tokens and interrupts nobody.

Alpha Vantage's free tier is 25 calls a day for the whole profile. The two runs
budget 17 between them, leaving eight for questions asked by hand.

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
  providers/mcp_client.py                  the bundle as an MCP client
  providers/    bars.py                    yfinance primary, Stooq fallback
  feeds.py      materiality.py             intake, subject gate, noise filter
  news.py       events.py                  fetch on a timer, alert on an event
  sector.py     macro.py                   group standing; rates on change
  portfolio.py                             average cost open, FIFO realised
  charts.py     report.py  runs.py  brief.py  cli.py
```

The detection maths depends on none of the runtime dependencies, so the whole
test suite runs without pandas, without a network and without a market being
open:

```bash
.venv/bin/python -m pytest tests -q     # 350 tests, ~0.9s
```

## Design notes

`docs/DESIGN.md` covers why Python holds the MCP client end, why the classifier
runs before the sentiment score, why news dedupe is scoped to the ticker, why the
pivot excludes the current bar, and which false positives the thresholds were
tuned against.
