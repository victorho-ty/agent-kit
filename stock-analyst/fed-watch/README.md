# fed-watch

FOMC target-rate probabilities for the next two meetings, derived from 30-Day
Fed Funds futures, with a snapshot history and change detection. Part of the
Hermes `stock-analyst` profile.

The figures are **computed** using CME FedWatch's published methodology, not
scraped from the CME page. Given CME's own mid price the calculation reproduces
their published table exactly; reading Yahoo's last traded print instead puts it
within about one percentage point. They are never CME's numbers and must not be
attributed as such — see [docs/DESIGN.md](docs/DESIGN.md) for why the bundle
computes rather than scrapes.

## Install

```bash
uv sync
ln -s "$PWD/.venv/bin/fedctl" ~/.local/bin/fedctl
```

No API key required. Probabilities use the New York Fed's public rates endpoint
and Yahoo's dated CBOT futures. Catalyst context comes from CNBC Economy RSS.

## Use

```bash
fedctl snap                     # read the market now, store one row of history
fedctl check-changes            # snap, then report what moved since the last report
fedctl history --limit 20       # the last 20 reported changes, summarised and charted
fedctl meetings                 # the calendar, and the contract behind each date
```

The default alert cutoff is **5.0 percentage points**. Pass `--threshold` or
set `FED_WATCH_CHANGE_THRESHOLD` to override it. A qualifying odds move includes
recent CNBC Economy stories for the alert agent to assess.

### Scheduled alerts

Save this pre-check as `fed-watch-check.sh` under the stock-analyst profile's
Hermes scripts directory (the profile's `$HERMES_HOME/scripts`).

```sh
#!/bin/sh
exec "$HOME/.local/bin/fedctl" check-changes --hermes-cron
```

In that profile, attach the script and skill to the existing alert job.

```bash
hermes cron edit fed-watch-alert --script fed-watch-check.sh --add-skill fed-watch \
  --prompt "Use the fed-watch skill to report the original change payload from the pre-check context."
```

Use this job for polling and delivery in place of the separate `--quiet` polling
wrapper. Quiet ticks skip the agent. The script passes the same diff and chart
paths that triggered the wake-up. Put any `--threshold` override in the script.

## Keeping it running

`fed_watch/config/fomc.json` holds the FOMC calendar, currently through
**27 October 2027**. The Fed publishes about a year ahead; when the file runs
short, `snap` and `meetings` emit a `calendar_warning`, and when it empties
every command fails with `ERR_CONFIG` rather than guessing. Add the next year's
dates from
[federalreserve.gov](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
— they are the **second** day of each two-day meeting.

## Tests

```bash
uv run pytest
```

No network, no clock, no market hours. The calculation is pinned against CME's
own published output for 16 September 2026.

## Layout

```
fed_watch/            deterministic Python; probabilities.py is pure stdlib
  config/fomc.json    the FOMC calendar (shipped package data)
skills/fed-watch/     SKILL.md and its references
tests/                pytest
docs/DESIGN.md        why this computes instead of scraping, and what that costs
```
