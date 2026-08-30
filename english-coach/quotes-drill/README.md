# Quotes drill

An agent skill plus the deterministic Python behind it, for the `english-coach`
profile. The operator saves the quotes, words and phrases they want to own; the
tools decide which one is drilled next and when it comes back; the agent asks
for it out loud, judges what was said, and coaches in spoken English.

The model never picks the item and never computes an interval. The tools never
judge a sentence.

```
quotes-drill/
├── quotes_drill/
│   ├── config/
│   │   ├── styles.py               # StyleSet, load(), rotation
│   │   └── styles.json             # category -> named speaking styles
│   ├── cli.py                      # quotesctl / python -m quotes_drill
│   ├── settings.py                 # env overrides: db path, styles, tz, cooldown
│   ├── clock.py                    # the one place that reads the wall clock
│   ├── db.py                       # SQLite: entry, attempt
│   ├── models.py                   # Entry, Attempt
│   ├── store.py                    # add, edit, record, read
│   ├── schedule.py                 # the queue and the spacing ladder
│   └── stats.py                    # counts, day streak, weakest items
├── skills/quotes-drill/
│   ├── SKILL.md                    # what the agent loads
│   └── references/
│       ├── cli.md                  # full command surface, JSON shapes, schema
│       └── judging.md              # the rubric and the shape of spoken feedback
├── docs/DESIGN.md                  # why it is shaped this way
├── tests/                          # pytest, no network, no wall clock
└── pyproject.toml
```

## Install on Ubuntu

```bash
unzip quotes-drill.zip -d ~/projects/hermes/profile-english-coach
cd ~/projects/hermes/profile-english-coach/quotes-drill
uv sync
```

Point Hermes at the skill:

```bash
ln -s ~/projects/hermes/profile-english-coach/quotes-drill/skills/quotes-drill ~/.hermes/skills/quotes-drill
```

And expose the console script so the skill works from any working directory:

```bash
ln -s ~/projects/hermes/profile-english-coach/quotes-drill/.venv/bin/quotesctl ~/.local/bin/quotesctl
quotesctl stats
```

## One cron entry

The cron wakes the *agent*, not the CLI — a drill is a conversation, and the
tools have nothing to say on their own.

```
0 8,21 * * *    run one quotes drill
```

Twice a day, and the schedule is independent of the queue: `next` returns
whatever is due whenever it is asked, so a missed drill needs no catch-up and a
drill nobody answers costs nothing, because `next` writes nothing.

## Configuration

| Variable | Default |
|---|---|
| `QUOTES_DRILL_DB` | `~/.local/share/hermes-english-coach/quotes_drill.db` |
| `QUOTES_DRILL_STYLES` | `quotes_drill/config/styles.json` in the bundle |
| `QUOTES_DRILL_TZ` | `Asia/Hong_Kong` |
| `QUOTES_DRILL_COOLDOWN_HOURS` | `12` — how long a just-drilled item stays out of the queue |

State is under `hermes-english-coach`, scoped to the profile rather than to this
bundle, so everything the coach owns backs up as one directory.

## The loop

```bash
quotesctl add --text "tuck in" --category Food --kind phrase --note "start eating, informal"
quotesctl next
# ... the agent asks, the operator speaks, the agent judges ...
quotesctl record --entry 7 --score 4 --transcript "..." --feedback "..." --error-kind grammar
```

`next` is a pure read; `record` is the only write that moves an item. That
separation is the design: see `docs/DESIGN.md`.

## Data model

SQLite at `$QUOTES_DRILL_DB`, two tables. `entry` holds the material and its
drill state — `times_tested`, a single `last_tested_at`, the current `streak`
and the `next_due_at` those produce. `attempt` holds one row per answered drill:
what was said, what it scored, what was wrong with it, which style was asked
for.

Nothing is ever deleted. An item that is owned is `retired`, which takes it out
of the queue and keeps its history. Full DDL in
`skills/quotes-drill/references/cli.md`.

## The queue

Fewest drills first, then the one left alone longest — a two-column `ORDER BY`,
not a judgement call. On top of it: a twelve-hour cooldown so a small store does
not repeat itself in a morning, and a fallback that returns the least-drilled
item flagged `due: false` rather than answering a request for a drill with
nothing.

Spacing is a Leitner ladder of **1, 2, 4, 8, 16, 32** days. A 4 or 5 climbs a
rung, a 3 repeats it, anything below drops to tomorrow.

## Styles

`quotes_drill/config/styles.json` maps a category to named voices — Food to Mark
Wiens, Bourdain and Nigella Lawson; Joke to a stand-up beat and dry British
understatement; and so on — each with one line describing the register. The CLI
rotates through them on the item's own drill count, so the same phrase is heard
in a different voice each time and the choice is reproducible.

A style flavours wording the coach would vouch for. It is never a licence to
teach an idiom nobody says.

## Tests

```bash
uv run pytest -q
```

19 tests, no network and no wall clock — every function that needs the time is
handed it, so a month-long interval is tested in a millisecond.

The ones that matter most:

1. **the same line punctuated differently is one entry**, and the first save
   keeps its category;
2. **never-drilled items come first**, then fewest drills, then oldest;
3. **an unanswered drill costs nothing** — two `next` calls in a row return the
   same untouched item;
4. **the ladder climbs on good answers and falls all the way on a miss**;
5. **nothing due still returns something**, flagged `not_due`;
6. **one bad item rejects the whole import**, and the store stays empty.

## Limitations

- **The score is the model's.** Nothing here checks it. What the tools guarantee
  is that the same score always produces the same interval.
- **The transcript is machine-transcribed speech.** Homophones and missing
  punctuation are artefacts, not errors, and the rubric says so — but a
  sufficiently garbled transcript simply cannot be judged, and the agent is told
  to ask again rather than guess.
- **One operator.** No accounts, no isolation; the profile is the boundary.
- **Categories are free text.** An unknown one drills in the general voice
  rather than being rejected, so a typo shows up as `style.source: "default"`
  and not as an error.
