# `quotesctl` — full command surface

Every command prints one JSON object on stdout. Success carries `"ok": true` and
exits 0. Failure carries `"ok": false` with a `code` from the closed enum below
and exits with the matching number.

Global options, before the subcommand:

| Option | Meaning |
|---|---|
| `--db PATH` | Database file. Defaults to `$QUOTES_DRILL_DB`, then `~/.local/share/hermes-english-coach/quotes_drill.db`. |
| `--now ISO` | Treat this as the current time. For replay and tests only; a drill uses the real clock. |

## Exit codes

| Code | Name | Meaning |
|---|---|---|
| 0 | `OK` | Success. |
| 10 | `ERR_USAGE` | Bad argument or bad import payload. **Nothing was written.** |
| 11 | `ERR_CONFIG` | `styles.json` missing or malformed. |
| 12 | `ERR_DB` | The database could not be opened. |
| 30 | `ERR_NOT_FOUND` | No entry with that id. |
| 31 | `ERR_NO_ENTRIES` | Nothing active to drill — the store is empty, or the category has nothing in it. Ask for material; this is not a fault. |

## `add`

```bash
quotesctl add --text "The flavour is unbelievable" --category Food \
  --kind quote --source "Mark Wiens" --note "present tense, sensory verb"
```

`--text` and `--category` are required. `--kind` is `quote` (default), `vocab`
or `phrase`.

```json
{ "ok": true, "created": true, "entry": { "id": 1, "...": "..." } }
```

Deduplication is on the text itself, case, punctuation and apostrophes ignored:
`Don't cry over spilt milk.` and `dont cry over spilt milk` are one entry. A
re-add returns `"created": false` with the **existing** row — the first save
keeps its category and note.

## `import`

```bash
quotesctl import --file batch.json
quotesctl import --file -          # read the JSON from stdin
```

Takes a JSON array, or an object with an `entries` array. Each item needs `text`
and `category`; `kind`, `source` and `note` are optional.

The batch is validated in full before anything is written. One bad item fails
the whole call with `ERR_USAGE` and `details.index` pointing at it, and nothing
is stored. Duplicates inside a valid batch are not errors — they are counted.

```json
{ "ok": true, "added": 3, "duplicates": 1,
  "entries": [{ "id": 4, "text": "tuck in", "created": true }] }
```

## `next`

```bash
quotesctl next                          # one item
quotesctl next --count 3                # three, still drilled one at a time
quotesctl next --category Joke
quotesctl next --no-style
```

**Writes nothing.** Calling it twice returns the same item.

```json
{
  "ok": true,
  "asked_at": "2026-08-30T09:00:00+08:00",
  "count": 1,
  "pool": { "active": 12, "due_now": 3, "never_tested": 2, "cooling": 1 },
  "items": [
    {
      "entry": {
        "id": 7, "text": "tuck in", "kind": "phrase", "category": "Food",
        "source": null, "note": "start eating, informal", "status": "active",
        "times_tested": 2, "last_tested_at": "2026-08-24T09:03:00+08:00",
        "last_score": 3, "streak": 0, "next_due_at": "2026-08-25T09:03:00+08:00",
        "created_at": "...", "updated_at": "..."
      },
      "reason": "due",
      "due": true,
      "style": { "name": "Mark Wiens", "voice": "food vlogger: present tense, ...",
                 "category": "Food", "source": "category" },
      "last_attempt": { "id": 9, "entry_id": 7, "score": 3,
                        "transcript": "...", "feedback": "...",
                        "error_kind": "register", "style": "Anthony Bourdain",
                        "created_at": "2026-08-24T09:03:00+08:00" }
    }
  ]
}
```

`reason`:

| Value | Meaning |
|---|---|
| `never_tested` | Never drilled. These come first, oldest first. |
| `due` | Its interval has elapsed. |
| `not_due` | Nothing was due; this is the least-drilled, longest-untouched item. `due` is `false`. |
| `cooling` | Everything was drilled inside the cooldown window. `due` is `false`. Normally a reason not to drill. |

`style.source` is `category` when the category has its own styles and `default`
when it fell back to the general ones. The style rotates on `times_tested`, so
it is stable for a given item until that item is drilled again.

## `record`

```bash
quotesctl record --entry 7 --score 4 \
  --transcript "we tucked in before the rain started" \
  --feedback "natural, and the past tense held up" \
  --error-kind none --style "Mark Wiens"
```

`--entry` and `--score` (0–5) are required. `--error-kind` is one of `none`,
`accuracy`, `context`, `register`, `grammar`, `fluency`.

One call does all of it: writes the attempt, increments `times_tested`, sets
`last_tested_at`, `last_score` and `streak`, and computes `next_due_at`.

```json
{ "ok": true, "entry": { "id": 7, "times_tested": 3, "streak": 1, "...": "..." },
  "interval_days": 2, "next_due_at": "2026-09-01T09:05:00+08:00" }
```

## `list`

```bash
quotesctl list --category Food --status active --limit 20
quotesctl list --status any
```

Returns entries in queue order — the order `next` will reach them — plus the
categories in use with their counts.

## `show`

```bash
quotesctl show --entry 7 --attempts 5
```

One entry and its most recent attempts, newest first.

## `edit`

```bash
quotesctl edit --entry 7 --category Empathy --note "softer than 'dig in'"
quotesctl edit --entry 7 --status retired
```

Changes `text`, `category`, `kind`, `source`, `note` or `status`. Never touches
drill state. Editing `text` onto another entry's text is refused with
`ERR_USAGE` naming the entry that already holds it.

## `stats`

```bash
quotesctl stats --weakest 5
```

```json
{
  "ok": true,
  "entries": { "active": 12, "retired": 2, "never_tested": 3, "due_now": 4 },
  "attempts": { "total": 41, "last_7d": 9, "last_30d": 33, "mean_score_last_20": 3.65 },
  "day_streak": 6,
  "by_category": [{ "category": "Food", "entries": 5, "attempts": 18, "mean_score": 3.7 }],
  "weakest": [{ "id": 7, "text": "tuck in", "category": "Food", "attempts": 4,
                "mean_score": 2.25, "last_score": 3 }]
}
```

`day_streak` counts consecutive days with at least one attempt, ending today or
yesterday — so it does not read as broken before the day's drill has happened.
`weakest` needs at least two attempts on an entry.

## `styles`

```bash
quotesctl styles
quotesctl styles --category Food
```

Returns the configured styles. `configured: false` means that category has none
of its own and the general ones apply. Styles live in
`quotes_drill/config/styles.json`; adding one is a config edit, never a code
change.

## Schema

SQLite at `$QUOTES_DRILL_DB`.

```sql
CREATE TABLE entry (
  id             INTEGER PRIMARY KEY,
  text           TEXT NOT NULL,
  norm_text      TEXT NOT NULL UNIQUE,       -- dedupe key
  kind           TEXT NOT NULL CHECK (kind IN ('quote','vocab','phrase')),
  category       TEXT NOT NULL,
  source         TEXT,
  note           TEXT,
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  times_tested   INTEGER NOT NULL DEFAULT 0,
  last_tested_at TEXT,
  last_score     INTEGER,
  streak         INTEGER NOT NULL DEFAULT 0,
  next_due_at    TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE attempt (
  id          INTEGER PRIMARY KEY,
  entry_id    INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  score       INTEGER NOT NULL CHECK (score BETWEEN 0 AND 5),
  transcript  TEXT,
  feedback    TEXT,
  error_kind  TEXT CHECK (error_kind IN
                ('none','accuracy','context','register','grammar','fluency')),
  style       TEXT,
  created_at  TEXT NOT NULL
);
```

Nothing is ever deleted. An entry that is finished with is `retired`, which
takes it out of the queue and keeps its history.

## The spacing ladder

`next_due_at` is `now + LADDER[streak]` days, where the rungs are
**1, 2, 4, 8, 16, 32**.

| Score | Streak | Interval |
|---|---|---|
| 4 or 5 | climbs one rung | the new rung |
| 3 | unchanged | the same rung again |
| 0–2 | back to zero | 1 day |

Five good answers take an item from tomorrow to a month away. One miss brings it
back tomorrow. This is the whole policy, and it lives in `schedule.py`.
