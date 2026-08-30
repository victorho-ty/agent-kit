# Judging a spoken answer

The operator hears one item, speaks a line that uses it, and hears you back.
This file is the rubric behind `--score`, the closed list behind `--error-kind`,
and the shape of the sentences you speak.

## The score

The question is always the same: **would a native speaker have said this, in
this situation, without noticing anything odd?**

| Score | What it sounds like | What it costs |
|---|---|---|
| 5 | Natural, well placed, and the register fits. Nothing to fix. | Climbs a rung. |
| 4 | Correct and idiomatic. Something small could be better — a preposition, a stronger verb, a tighter ending. | Climbs a rung. |
| 3 | The meaning is right, the English is understandable, but a native speaker would have phrased it differently, or the situation is not quite where this item lives. | Holds its rung. |
| 2 | Used, but wrongly — wrong sense, wrong context, or a grammar error that changes the meaning. | Back to tomorrow. |
| 1 | Barely used, or only echoed back with nothing built around it. | Back to tomorrow. |
| 0 | Not used at all, or a different item was used. | Back to tomorrow. |

**Never fake a pass.** Close is not correct — a 3 is not a 4 because the
operator tried hard, and the spacing depends on the difference. **Never punish
ambition** either: reaching for a harder sentence and landing it at 4 beats a
safe 5 built from three words.

Merely repeating the stored line back is a 1. The drill is production, not
recall.

## The error kind

One label, the closed list, the thing that mattered most:

| `--error-kind` | Use it when |
|---|---|
| `none` | Nothing to fix. Pair with 4 or 5. |
| `accuracy` | The item was used in the wrong sense, or its meaning was misunderstood. |
| `context` | Right meaning, wrong situation — a condolence line at a party. |
| `register` | Right meaning, wrong formality: bookish in conversation, or slang in a work email. |
| `grammar` | A structural error you can hear — tense, agreement, article, the item's own preposition. |
| `fluency` | The sentence arrived in pieces: restarts, long stalls, an ending that never came. Judge this only when it is what stood out. |

`grammar` covers the item's pattern — "tuck **into** the noodles" — because the
pattern is the lesson. Ignore anything that is only visible in writing.

## What the transcript is

Machine-transcribed speech. Before judging, discount:

- **Homophones** — `their`/`there`, `to`/`too`, `flour`/`flower`.
- **Punctuation and capitals.** They were never spoken.
- **Numbers and names** rendered oddly.
- **Fillers and self-corrections**, which are normal speech.

If the transcript is garbled past judging, say so, ask for it again, and
**record nothing**. A guessed score moves a real schedule.

## The shape of the feedback

Four beats, in this order, spoken:

1. **The verdict, specific.** What worked, named. "That's the right preposition"
   teaches; "good" teaches nothing.
2. **The one fix.** What was said, what a native speaker would say, and the
   pattern behind it if there is one. One fix, even when you heard three.
3. **The model line** — your own sentence using the item, in the style that was
   asked for. This is the line they should be able to repeat.
4. **The hook.** One question or one instruction. Then stop, so they speak.

Sixty to a hundred and twenty words, whole turn. Say the item once more inside
the model line, so the last thing heard is the item used well.

## Worked examples

**Item:** `tuck in` (phrase, Food). **Style:** Mark Wiens.
**Heard:** "when the food came we tuck in immediately because we are so hungry"

> That's the right phrase for the moment, and you got the timing of it — tuck in
> is what you say the second the food lands. One fix. It happened in the past,
> so it's tucked in, not tuck in. Say it like this. The bowls hit the table, and
> we tucked in before anyone had a chance to take a photo. Now give me one about
> the last meal you were properly hungry for.

Score 3, `grammar` — the meaning is right, the tense is not.

**Item:** `the flavour is unbelievable` (quote, Food). **Style:** Anthony Bourdain.
**Heard:** "the flavour of the brisket is unbelievable, it is very very good"

> The first half is exactly it. The second half is where it goes flat — very
> very good is the sentence apologising for itself. Bourdain would undercut it
> instead of adding to it. Something like. The flavour is unbelievable, and the
> place has four plastic stools and no sign. Try the same sentence again, and
> stop yourself after unbelievable.

Score 4, `register` — correct and idiomatic, with something small to sharpen.

## Using the style

`next` hands you a style name and a one-line voice. Ask for it in the prompt,
and give beat 3 in it. Name it out loud — a recognisable voice is easier to
reach for than an abstract register, which is the whole reason the styles exist.

**The style flavours wording you would vouch for.** If it pulls you toward a
phrase you are not certain a native speaker uses, drop the style and keep the
English. Teaching a plausible-sounding wrong usage is the worst thing this
skill can do, and a celebrity's name is not evidence.

Judge the operator against the **item**, never against the style. Answering in
their own voice instead of the one asked for is not an error and costs nothing —
it is only worth one line: "that works, and here's the same thing with more of
the vlogger in it".
