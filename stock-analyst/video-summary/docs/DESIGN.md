# video-summary — design

Why the bundle is shaped this way. Written alongside the implementation; where
this document and the code disagree, the code is right and this file is a bug.

## 1. One command, still decoupled from its schedule

news-radar splits `scan` from `digest` because a digest must wait: stories
arrive from different outlets at different times and only cluster once several
are pending. Nothing here waits for anything except a caption track, and that
wait is measured in minutes and handled inside the run.

So `check` collects *and* reports, and there is one cron entry.

What is deliberately kept from news-radar is the property that mattered — the
coupling to the schedule is a **column**, not a clock. `check` returns every
video where `summarised_at IS NULL`, however many runs happened since:

- a missed run needs no catch-up, because "new" is `video_id` against the table
  rather than a time range;
- a caught-up run repeats nothing;
- the cron expression can change without touching the config, which is exactly
  why no interval appears in it.

Two hours is a floor on *notification frequency*, not on cost. A conditional GET
against an unchanged feed is a few hundred bytes and no parsing.

## 2. The stamp goes after the send, one video at a time

news-radar's `digest --commit` stamps before returning, because a digest is one
message: it either goes or it does not.

Here a run sends up to five messages, and the third can fail.
Both batch strategies are wrong:

- stamp up front and a failure loses every video after it, permanently, with no
  record that anything was missed;
- stamp at the end and a failure re-sends the two that already arrived.

So `check` never stamps and `mark --video <id>` is called per video, after its
own send. `mark` on an already-stamped video reports `already_marked` rather
than failing, which makes a retry safe. `--all` exists for the case where the
whole batch demonstrably went out, and SKILL.md tells the agent to prefer the
per-video form.

The cost is one extra tool call per video and a dependency on the agent actually
making it. A forgotten `mark` means a repeat in two hours — annoying, visible,
and recoverable. A wrongly-placed automatic stamp means silence, which is not.

## 3. The transcript is a file, and that is the load-bearing decision

A forty-minute video is roughly forty thousand characters of captions. Five in
one wake-up is two hundred thousand characters of context for a model that will
write four thousand — and most of it for videos the model will summarise in two
sentences because they turn out to be promotional.

So captions are flattened to prose, written to
`$VIDEO_SUMMARY_TRANSCRIPTS/<video_id>.txt`, and the payload carries the
**path**. The agent opens what it is about to write about, and nothing else.

Three consequences worth stating:

- the payload for a busy check is a few kilobytes regardless of video length;
- a transcript survives the run, so a failed send can be retried without
  re-fetching anything from YouTube;
- `--text` exists on the `transcript` command for triage, and SKILL.md tells the
  agent to prefer the path, because the file is on the same machine.

Cues are flattened rather than kept per-line because a line-per-cue file reads
as a poem, and a three-second fragment is not a sentence. Timestamps are dropped
entirely: nothing downstream cites a moment in a video.

## 4. Manual captions beat automatic ones, across languages

The obvious ordering — first configured language wins, manual or not — is wrong
for this desk. Auto-generated captions mangle precisely the tokens that carry the
information: tickers that sound like words, "basis points" as "basis point",
numbers adjacent to units. A human-written Traditional Chinese track is a better
input than a machine English one.

So the search is: manual in language order, then automatic in language order,
then any track at all. `transcript.language` and the file header both say which
was used, and SKILL.md tells the agent to flag a garbled transcript rather than
guess what was meant.

## 5. The grace period

Captions do not exist at the instant a video is published; YouTube generates them
over the following minutes. A checker that runs every two hours will therefore
catch some videos before their captions exist.

Without a grace period, those go out as bare headlines — which is precisely the
outcome this skill exists to improve on, and it happens *more* often for the
videos posted most recently, i.e. the ones people care about.

So a video with no transcript yet is held back and does not reach the agent at
all. After `transcript_grace_minutes` (120 — one cron cycle) it is released
regardless, with `transcript.status` saying why there is nothing to summarise.
`held_for_transcript` reports the count so a quiet run is still explicable.

`max_transcript_attempts` (3) is the other half: a channel with captions
genuinely disabled must not be asked about every two hours forever.

## 6. Short versus long costs one request, and is a filter

The feed does not carry duration and does not distinguish Shorts. There is no
free API that answers either without a key.

What does answer it: `/shorts/<id>` stays put for a Short and redirects to
`/watch` for anything else. One HEAD request per *new* video, always, on every
feed.

**Shorts are excluded by default**, per feed. That reverses this section's
original position — that the operator subscribed to a channel, not to a format —
and the reversal is deliberate: in practice most channels use Shorts to advertise
their long videos, so the default that respects the operator's attention is to
drop them and let a feed opt back in with `"exclude_shorts": false`.

The filter runs **before the insert**, not at release: an unwanted Short costs one
redirect and then nothing at all — no row, no transcript fetch, no ledger entry.
That ordering is the whole value, given a caption fetch is the expensive and
blockable step.

Detection has no configuration knob, and briefly had one. A `detect_shorts` flag
existed while the label was only a label, and survived one revision as "label the
videos I am not filtering". It was removed because a knob that can switch
detection off is a knob that can silently disable a filter the operator asked
for, and because the thing it saved — one HEAD request per new video — is not
worth a second setting to reason about. Resolution is now unconditional.

Three constraints, all in the code:

- **failure is `unknown`, and `unknown` is never excluded.** A video is never
  lost to a label that could not be resolved;
- **cold-start videos are never resolved.** Fifteen redirects to label a back
  catalogue nobody will be shown is fifteen wasted requests;
- **`--dry-run` resolves anyway**, cold start included. It is the one command
  typed by a person asking what a feed would do, and answering `unknown` to that
  is answering nothing.

The label also reaches the agent, so a sixty-second clip from a feed that keeps
its Shorts gets a two-sentence summary rather than a paragraph.

## 7. Where the model is, and where it is not

The tools decide **what is new** and **what was said**. The model decides **what
is worth saying**, which is the only judgement in the pipeline and the reason
this is a skill rather than a shell script with a webhook.

That boundary was the one real fork in the design. The alternative — the bundle
holding an API key and returning a finished summary string — is cheaper per
wake-up and gives deterministic output length. It was rejected because:

- it puts a second, unaccountable model inside a tool the agent is told to trust
  as deterministic;
- it needs a key on the Hermes host, and a second billing surface;
- the summary is exactly where profile context belongs. The `stock-analyst`
  SOUL — the four readings, the labelling of `fact` / `derived` / `opinion`, the
  refusal to state a market claim on a YouTuber's authority — is loaded in the
  agent and would have to be duplicated, badly, into a prompt constant here.

The corollary is that **nothing is stored about the summary**. There is no
summaries table and no copy of what was sent: the bundle cannot verify a
sentence it did not write, and a stale copy of one is worse than none.

## 8. Telegram is Hermes's, not this bundle's

There is no Telegram client anywhere in this package, matching `news-radar`,
`coupon-tracker` and `household-expenses`. The agent already owns the channel,
the chat id and the credentials.

One delivery detail is nonetheless pinned in SKILL.md, because getting it wrong
is expensive and invisible: **one `sendMessage` per video, carrying the url, and
never `sendPhoto`.**

The bundle extracts `thumbnail_url` from the feed and stores it, and the first
draft of the skill sent it as a photo before the summary. That was redundant:
Telegram builds its own preview card — thumbnail, title, channel — from any
YouTube link in a message body. The photo send bought a second notification
showing the same image, from a url that can rot independently of the video.

The field is kept in the payload rather than removed. It costs one attribute in
a feed we already parse, it is the honest answer to "what image does this video
have", and it is the fallback if a client ever suppresses previews. SKILL.md
says plainly not to send it.

A photo *caption* was the other candidate and is worse still: captions cap at
1024 characters against a message's 4096, which would weld the skill's 800 —
a decision about reading on a phone — to a protocol limit it has nothing to do
with.

## 9. Failure isolation, copied wholesale

Per-feed try/except, `feed_failures` in the payload, run status `partial`, the
`recent_yield` zero-yield guard, conditional GET, no retry on 4xx, and the
request delay are all from `news-radar` and unchanged. They earn their place for
the same reasons documented there.

The one addition is that **a transcript failure is never a feed failure**. A
video with no captions is real, new, and worth a line. Only an explicit
`transcript --refresh` — where the caller asked for exactly this one thing — is
allowed to exit non-zero on it.

## 10. What is deliberately absent

- **No summaries table** (§7), **no Telegram module** (§8), **no scheduler**
  (§1).
- **No include list.** The operator subscribed to the channel; everything it
  posts is in scope. `exclude` exists only for feed furniture and is the one
  filter there is.
- **No date parsing.** `published_text` is YouTube's own string. Ordering is by
  the order we first saw things, which is what the ledger already implies.
- **No view counts, no engagement metrics.** They change after publication,
  which would mean re-reading videos already seen, to inform a decision nobody
  makes.
- **No transcript search, no cross-video analysis.** Both are real ideas and
  neither is this skill's job. The transcripts are plain text on disk with
  predictable names if something else ever wants them.
