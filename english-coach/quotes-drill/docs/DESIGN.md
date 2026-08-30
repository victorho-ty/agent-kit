# Design notes

## What the model is for, and what it is not for

The expensive part of a drill is not the drill. It is the judgement: reading a
transcribed sentence and saying what a native speaker would have said instead.
Everything around that judgement is arithmetic, and arithmetic given to a model
is arithmetic done differently every time.

So the split is:

| Deterministic | The model |
|---|---|
| which item is drilled next | the category label on the way in |
| how many times it has been drilled | the score, and the one thing to fix |
| when it comes back | the spoken feedback and the model line |
| the day streak, the weakest items | which style flavour to give the example |

A drill costs one `next`, one judgement, one `record`. Nothing is summarised on
the way in, and no history is replayed into context: `next` carries the item,
its note, and the **single** most recent attempt — which is all the continuity a
coach actually uses.

## `next` writes nothing

The two halves of a drill are separate commands because the failure they guard
against is real: a cron fires at eight in the morning and nobody is listening.

If `next` stamped the item, that silence would cost it its turn and it would
come back a week later having never been drilled. Instead `next` is a pure read
and `record` is the only write, so an unanswered drill leaves the queue exactly
as it was. It also makes `next` safe to call twice while working out what to
say.

The same property covers a mid-drill failure: nothing is half-written, because
there is only ever one write.

## The queue, spelled as an ORDER BY

The rule the operator asked for — fewest drills first, then the one left alone
longest — is a two-column sort with `id` breaking the tie:

```sql
ORDER BY times_tested ASC, COALESCE(last_tested_at, '') ASC, id ASC
```

`COALESCE` puts never-drilled items at the front of their own group, which is
where they belong anyway since their `times_tested` is zero.

Two things sit on top of it:

**A cooldown**, twelve hours by default. A store of three items would otherwise
hand back the same one twice in a morning. An item drilled inside the window is
held out of the pool and reported in `pool.cooling`.

**A fallback rather than an empty answer.** If nothing is due, the least-drilled
item comes back flagged `due: false` with `reason: "not_due"`. A drill someone
asked for should never answer "nothing for you today"; the agent can say that
part in words. Only a genuinely empty store is an error, and it gets its own
exit code (31) because the next move — ask for material — is obvious and is not
a fault.

## The ladder, and why the streak lives on the row

Rungs of **1, 2, 4, 8, 16, 32** days. A good answer (4 or 5) climbs one, a 3
repeats the rung, anything below drops to the bottom. Five good answers move an
item from tomorrow to a month away; one miss brings it back tomorrow.

This is a Leitner box, not SM-2. SM-2 tunes an ease factor per item from a long
history, which is worth having when there are thousands of cards and no teacher.
Here there are dozens of items and a coach who is going to talk about the miss
anyway, so a ladder that can be explained in one spoken sentence — "you had it,
so I'll leave it a week" — is worth more than a better-tuned interval.

`streak` and `times_tested` are columns on `entry` rather than aggregates over
`attempt`, so the queue stays a sort over one table. The attempt log is history
and analysis; the row is state.

## Dedupe on the text itself

`norm_text` is the line case-folded, apostrophes removed, everything
non-alphanumeric flattened to a space. `Don't cry over spilt milk.` and
`dont cry over spilt milk` are one entry, because the same line typed twice a
month apart is a duplicate in the queue, not a second thing to learn.

The first save wins. A re-add returns the existing row with `created: false` and
does **not** overwrite its category or note — otherwise mentioning a quote in a
different context would silently reshuffle a category that was already right.

## Styles are config, and the rotation is arithmetic

`config/styles.json` maps a category to named voices with a one-line
description of each. The CLI picks by `times_tested % len(styles)`, so the same
item is heard in a different voice each time it comes round, the choice is
reproducible, and adding Nigella Lawson to Food is a config edit.

Two things this deliberately does not do. It does not let the model pick the
style — that turns into whichever celebrity is most available to a language
model, every time. And it does not treat a style as licence: the SOUL's rule
against invented usage outranks the voice, because a plausible-sounding wrong
idiom in a famous person's cadence is exactly the failure this profile cannot
afford.

An unknown category still drills, in the general voice, with
`style.source: "default"` saying so.

## No audio in this bundle

Hermes already has speech-to-text (whisper) and text-to-speech (kokoro)
configured for the profile. Shelling out to either from here would duplicate
that configuration and put it out of sync the first time it changed.

So the bundle is text in, text out, and the SKILL carries the constraint that
the output is going to a speaker: no markdown, short sentences, one question at
the end. The transcript is stored as it arrives, and the rubric tells the agent
to discount what transcription got wrong rather than mark a homophone as an
error.

## What is absent

- **No delete.** `status: retired` takes an item out of the queue and keeps its
  attempts. The store is small and the history is the progress record.
- **No accounts.** One operator, one profile, one database — unlike
  `coupon-tracker`, where a Telegram group makes isolation load-bearing.
- **No session table.** A drill is one item, so a session is one attempt row and
  a wrapper would only add a way for the two to disagree.
- **No scores computed from text.** The model supplies the score; nothing here
  tries to check it. What the tools guarantee is that the same score always
  produces the same interval.
