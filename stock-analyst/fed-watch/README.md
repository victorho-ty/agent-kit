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

No API key. The two sources are the New York Fed's public rates endpoint and
Yahoo's dated CBOT futures.

## Use

```bash
fedctl snap                     # read the market now, store one row of history
fedctl check-changes            # snap, then report what moved since the last report
fedctl history --limit 20       # the last 20 reported changes, summarised and charted
fedctl meetings                 # the calendar, and the contract behind each date
```

Every command prints one JSON object. `check-changes --quiet` prints a bare
`1` or `0` so a cron wrapper can gate a wake-up:

```bash
[ "$(fedctl check-changes --quiet)" -eq 1 ] && hermes-run fed-watch-alert
```

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
