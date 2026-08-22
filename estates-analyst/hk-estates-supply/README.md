# Hermes HK primary-market supply

Agent skill plus the deterministic Python tools behind it. A daily check reads
the Housing Bureau's 私人住宅一手市場供應 index page; on the one day a quarter it
finds a new PDF, it extracts the three headline figures from page 2, appends
them to the history CSV and leaves a quarter pending. The agent then renders the
report — a coloured quarter table and two trend charts — and sends it. It never
decides what is new, and it never computes a number.

```
hk-estates-supply/
├── hk_estates_supply/                  # the package
│   ├── cli.py                          # python -m hk_estates_supply <command>
│   ├── settings.py                     # env overrides: paths, timeouts, table size
│   ├── clock.py                        # the one place that reads the wall clock
│   ├── models.py                       # Figures, QuarterRow, Publication
│   ├── fetch.py                        # urllib + regex (no HTTP stack, no HTML parser)
│   ├── extract.py                      # PDF page 2 -> three integers
│   ├── history.py                      # the CSV, quarter arithmetic, QoQ
│   ├── state.py                        # delivery ledger and run log
│   ├── render.py                       # the coloured table and the two charts
│   └── report.py                       # the daily check, and the report payload
├── data/
│   └── hk_units_supply_history.csv     # the durable record, newest first
├── skills/hk-estates-supply/
│   ├── SKILL.md                        # what the agent loads
│   └── references/
│       ├── cli.md                      # full command surface and JSON shapes
│       └── data-source.md              # the source, the figures, the extraction traps
├── docs/DESIGN.md                      # why it is shaped this way
├── tests/                              # pytest, no network, no clock, no PDF binaries
└── pyproject.toml
```

## Install on Ubuntu

```bash
unzip hk-estates-supply.zip -d ~/projects/hermes/profile-estates-analyst
cd ~/projects/hermes/profile-estates-analyst/hk-estates-supply
uv sync
sudo apt install fonts-noto-cjk    # so the Chinese column headings render
```

Point Hermes at the skill — either copy it into the agent's skills directory or
symlink it:

```bash
ln -s ~/projects/hermes/profile-estates-analyst/hk-estates-supply/skills/hk-estates-supply \
      ~/.hermes/skills/hk-estates-supply
```

And expose the console script so the skill works from any working directory:

```bash
ln -s ~/projects/hermes/profile-estates-analyst/hk-estates-supply/.venv/bin/hk-supply \
      ~/.local/bin/hk-supply
hk-supply source
```

The first `check` or `pending` absorbs the existing history silently, so
installing the bundle does not fire a report for every quarter already in the
file.

## The one cron entry

Daily, as a **plain command rather than a prompt** — the check is fully
deterministic, and a model in that loop costs tokens on the 361 days a year when
the answer is "nothing changed":

```bash
cd ~/projects/hermes/profile-estates-analyst/hk-estates-supply && \
  .venv/bin/hk-supply check >/dev/null && \
  [ "$(.venv/bin/hk-supply pending --count)" -gt 0 ] && hermes-run hk-estates-supply-report
```

Everything before the last clause runs on a timer and wakes nobody. The last
clause fires about four times a year.

The agent's job on waking is one command and one message:

```bash
hk-supply report --commit
```

which returns the three image paths, the finished summary lines, and the whole
table as JSON.

## Configuration

| Variable | Default |
|---|---|
| `HK_SUPPLY_HISTORY` | `data/hk_units_supply_history.csv` in the bundle |
| `HK_SUPPLY_STATE` | `~/.local/share/hermes-estates-analyst/hk_supply_state.json` |
| `HK_SUPPLY_RUNS` | `~/.local/share/hermes-estates-analyst/hk_supply_runs.jsonl` |
| `HK_SUPPLY_IMAGE_DIR` | `~/.local/share/hermes-estates-analyst/hk_supply_images` |
| `HK_SUPPLY_QUARTERS` | `12` rows in the report table |
| `HK_SUPPLY_IMAGE_RETENTION` | `30` days |
| `HK_SUPPLY_TZ` | `Asia/Hong_Kong` |
| `HK_SUPPLY_TIMEOUT` / `HK_SUPPLY_RETRIES` | `30` seconds, `2` retries |
| `HK_SUPPLY_FONT` | auto-detected CJK family; set to force one |
| `HK_SUPPLY_INDEX_URL` / `HK_SUPPLY_PDF_BASE_URL` | the Housing Bureau URLs |

The history CSV lives **in the bundle** because it is the durable artefact —
eighteen quarters that exist nowhere else once the Bureau archives the PDFs.
Everything else lives under the profile state directory because it is
reconstructible, and keeping it out of the bundle means a redeploy never has to
merge it.

## The report

Three images, sent in this order, sized for a phone:

1. **the table** — the last twelve quarters, one row per quarter, newest at the
   top, each QoQ % cell green when the figure rose and red when it fell;
2. **現樓貨尾** — completed but unsold, over the whole history;
3. **建築中未售** — under construction and unsold, over the whole history.

Telegram renders no HTML, which is why the table that used to be an HTML e-mail
body is a PNG like the charts are. The message body comes from `summary_lines`,
already formatted, and the agent relays those strings rather than restating the
numbers in prose of its own.

Green is up and red is down. That is the whole of the semantics: the bundle takes
no view on whether more unsold stock is good news, and neither should the agent.

## The three rules that make it work

**Read the page, not the text stream.** A label and its figure share a visual row
but not a baseline, so reading-order extraction puts the number before the label
on some rows and after it on others. Words are grouped into rows by vertical
centre and read left to right instead. This is the whole reason `pdfplumber` is a
dependency, and the reason a misread would otherwise be invisible: every figure
on the page is a five-digit round number, so a wrong one looks exactly as
plausible as a right one.

**The ledger is the queue.** A quarter is pending when it is in the CSV and has
never been stamped by `report --commit`. There is no separate "sent" table to
keep in sync, so a missed day needs no catch-up, a failed send is still pending
tomorrow, and a successful one can never be sent twice however often cron fires.

**Seed on first use.** The first run stamps everything already in the history as
reported. Without it, installing the bundle makes eighteen quarters pending at
once — and the alert everyone remembers is the one they had to mute.

## Silence has three causes, and they must be distinguishable

This monitor is *correctly* silent for three months at a stretch, which makes it
the worst case of the problem: nothing published, nothing running, and a page
that changed shape all look identical from the outside.

- **Not running** — every check writes a `runs` row, failures included.
  `hk-supply runs` is the liveness check, and it is the only thing that can tell
  you the cron entry died.
- **Changed shape** — a page that loads but no longer carries a
  `stat<YYYYMM>.pdf` link, or a PDF whose page 2 no longer carries a label, is
  `ERR_PARSE` and is reported. It is deliberately a different exit code from
  `ERR_FETCH`, because a site being down for an hour is not news and a series
  that can no longer be read is.
- **Genuinely nothing** — the normal case for 361 days a year, and the only one
  that gets no message.

## Being a good guest

Two GETs a day at most: one 3KB index page, and a 500KB PDF only in the quarter
it changes. Transport failures are retried twice with a short backoff; a 4xx
never is, because it means the URL is wrong and repeating it will not make it
right. There is no crawling and no archive trawling.

## No Telegram module

Hermes owns the channel; these tools only ever hand back JSON and file paths.

## Tests

```bash
uv run pytest -q
```

89 tests, no network, no wall clock, and no PDF binaries — the extractor is
exercised through word boxes, so the trap it exists to avoid is visible in the
test rather than hidden in a 500KB file nobody can diff.

The ones that matter most:

1. **a figure is never read off a neighbouring row** — including the case where
   the number's baseline sits above the label's, and the case where a section
   number is printed to its left;
2. **the first run absorbs the back catalogue, but not a quarter published that
   same day** — seeding happens before the append, and getting that backwards
   swallows the one quarter that mattered;
3. **a quarter is never written twice** — cron retries, and a duplicated row
   would poison every QoQ after it;
4. **a filename that disagrees with the page's printed date is refused** — a
   quarter under the wrong label is believed, so failing is the cheaper outcome;
5. **QoQ refuses to span a gap** — a six-month change under a QoQ heading looks
   entirely normal, which is what makes it dangerous;
6. **no link at all is `ERR_PARSE`, not silence** — the failure that would
   otherwise hide for a year;
7. **colour follows `direction`, not the sign of the rounded percentage** — the
   one defect that would quietly ruin the report is a red cell above a number
   that went up.
8. **a report about an older quarter shows that quarter** — the table window and
   both charts end at the subject, not at today;
9. **the y-axis frames the data and the x labels thin out** — the axis is
   cropped to the series so the shape is readable on a phone, which is why the
   exact size of every move lives in the table and never in the line.
