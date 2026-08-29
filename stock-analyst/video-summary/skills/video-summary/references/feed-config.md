# Feeds, transcripts, and the traps

`video_summary/config/feeds.json` is the whole of what is watched. Whole-line
`//` comments are stripped before parsing, so disabled examples can live in the
file as documentation.

## Global keys

| key | default | meaning |
|---|---|---|
| `timezone` | `Asia/Hong_Kong` | how timestamps are written |
| `request_delay_seconds` | `2.0` | pause between feed requests within one check |
| `max_per_check` | `5` | videos handed over per run; the rest stay pending |
| `summary_char_cap` | `800` | what the agent is told to write to, per video |
| `transcript_languages` | `en, en-US, en-GB, zh-Hant, zh-Hans, zh` | preferred caption languages, best first |
| `transcript_grace_minutes` | `120` | how long a caption-less video is held before going out bare |
| `max_transcript_attempts` | `3` | attempts per video before it is left alone |
| `exclude_shorts` | `true` | drop Shorts at the door; the default for every feed |
| `exclude` | none | drops a video outright, whatever else it says |

There is deliberately **no schedule key**. The cron entry is the cadence;
restating it here would be a second source of truth that drifts. What cron
cannot express is per-feed politeness, so that is the one knob provided below.

## Per-feed keys

| key | default | meaning |
|---|---|---|
| `name` | required | unique; how every other command refers to this feed |
| `url` | required | a real YouTube feed url (see below) |
| `note` | none | what this channel covers, **in words the agent reads** |
| `transcript` | `true` | `false` sends the title and the link only |
| `exclude_shorts` | global | `false` keeps this channel's Shorts |
| `min_interval_minutes` | `0` | a floor on how often this feed is fetched |
| `max_items` | `15` | entries taken from one document |
| `enabled` | `true` | `false` pauses a feed without losing its history |

### The url must be a feed url, and the id must be a channel id

```
https://www.youtube.com/feeds/videos.xml?channel_id=UCnexoc6tvesvcCEzZhmI-Ag
https://www.youtube.com/feeds/videos.xml?playlist_id=PL...
```

A channel *page* url is rejected at load time, because it would fetch happily,
parse as HTML, and report nothing new forever.

**A handle is not a channel id.** `@DiamondNestEgg` is a display name that can be
changed by its owner; `UCnexoc6tvesvcCEzZhmI-Ag` is the permanent id, starts
`UC`, and is 24 characters. Open the channel page and read `channelId` out of the
source. The config rejects anything else with that sentence in the message.

### `note` is not a comment

It is passed through to the agent in every payload for that feed. "US
Treasuries, bond ladders, CDs, money market and fixed income mechanics" tells
the model what kind of video it is reading and what a subscriber would already
know. A `//` comment does not reach it; this does.

### min_interval_minutes is a floor, not a schedule

The two-hourly cron still runs. A feed inside its floor is reported `throttled`
and skipped; the others in that run are checked normally. Rarely needed here —
a conditional GET against an unchanged feed is a few hundred bytes — but it
exists for a channel that posts monthly and does not need eighty-four checks
between uploads.

`--ignore-throttle` overrides it for a manual test.

## What the feed does not tell you

The Atom document YouTube publishes carries the video id, the title, the
description, the publication time and a thumbnail url. It does **not** carry:

- **Duration.** Nothing in the feed says how long a video is.
- **Whether it is a Short.** Shorts and long uploads arrive in the same feed,
  indistinguishable. One request per new video resolves it: `/shorts/<id>` stays
  put for a Short and redirects to `/watch` for anything else.
- **View count, likes, or anything else that changes after publication.** Which
  is a feature: nothing here needs re-reading a video it has already seen.

Videos absorbed by a **cold start are never resolved**. Fifteen redirects to
label a back catalogue nobody will be shown is fifteen wasted requests.

## Shorts

`exclude_shorts` is `true` by default, globally and therefore per feed. A Short
that a feed excludes is **dropped at the door**: it costs one redirect, and then
no row, no transcript fetch and no place in a ledger it would never leave. It is
counted separately from the keyword filter, as `shorts_excluded` per feed and
`videos_excluded_shorts` in the totals, because "this channel posts nothing but
Shorts" and "this channel posts nothing" are different answers to the same
question.

Set `"exclude_shorts": false` on a feed whose Shorts are worth reading — some
channels put a real rate call in sixty seconds, and most use the format as
advertising. That is a per-channel judgement, which is why the key is per feed.

Detection is not configurable. Every new video is resolved short-or-long, on
every feed, because the answer costs one redirect and is worth knowing whether or
not this feed filters on it — and a knob that could switch detection off would be
a knob that silently disables a filter the operator asked for.

One rule holds whatever the config says: **`unknown` is never excluded.** If the
redirect fails, the video goes through. A video is never lost to a label that
could not be resolved.

The corollary of dropping at the door: flipping `exclude_shorts` to `false` later
does not recover the Shorts already dropped — they were never stored. Ones still
inside the feed's `max_items` window will be picked up on the next check and sent
as if new. Same behaviour as un-excluding a keyword.

## Transcripts

`youtube-transcript-api` reads YouTube's own caption tracks. It is the code path
for the same material the `youtube-content` skill would fetch for a model — and
it is a library rather than that skill because a skill is instructions for a
model, not something a subprocess can call.

**A manual track beats an automatic one, and language order breaks ties within
each.** Auto-generated captions mangle exactly the words this desk cares about —
tickers, basis points, "the two-year" — so a human-written track in a later
language is preferred over a machine one in the first. If neither exists, any
track at all is taken: a foreign-language transcript is still better than
silence, and `transcript.language` says which it was.

The text is flattened from three-second cues into prose and written to
`~/.local/share/hermes-video-summary/transcripts/<video_id>.txt` with a
four-line header naming the video and whether the captions were auto-generated.
**The payload carries the path, never the text** — that single decision is what
keeps a busy check from costing two hundred thousand characters of context.

### The grace period

Captions are generated some minutes after an upload, not at it. A video with
none yet is **held back** rather than sent as a bare headline — for
`transcript_grace_minutes`, then released regardless with `transcript.status`
saying why there is nothing to summarise. Without this, every video posted in
the last few minutes would arrive as a link, which is the one thing this skill
exists to improve on.

`max_transcript_attempts` stops a video with captions genuinely disabled from
being asked about every two hours forever.

### IpBlocked

YouTube refuses caption requests from most cloud provider address space, and
from any address that has asked too often. It arrives as
`transcript.status: "error"` with `IpBlocked` in the message, on *every* video
rather than one.

The feed itself is unaffected — it is a public document on a different path — so
the watcher keeps working and only the summaries go missing. The fix is
`VIDEO_SUMMARY_PROXY=http://user:pass@host:port`, which is wired into the
caption client and nowhere else. Retrying without it changes nothing.

## Traps

**The same video in two feeds is one video.** Identity is YouTube's `video_id`,
globally unique, so a channel feed and a playlist feed carrying the same upload
store — and send — it once. This is the opposite of `news-radar`, where two
outlets carrying one story are deliberately two items.

**Feeds double-escape.** A title arrives as `Yield&#8217;s` because the value was
escaped twice before the XML parser saw it. Entities are decoded on extraction;
left alone they reach the reader as mojibake.

**A cold start is not news.** A feed's first successful check stores its whole
back catalogue already stamped. `--seed` does the same on demand.

**A dead channel looks exactly like a quiet month.** Both yield nothing. That is
why `recent_yield` exists: a feed that returned entries on its last successful
check and returns none now is reported `zero_yield`. A feed that has *never*
yielded anything is treated as quiet, so a wrong url on a brand new feed is only
caught by `--dry-run`.

**`exclude` is the only filter, and it is substring-matched** on title and
description after case folding. Keep the terms specific: `"ad"` would match
`"advance"`, `"Adelaide"` and `"broadband"`. There is no include list — the
operator already said what a channel is about by subscribing to it.

**`published_text` is never parsed.** It is YouTube's own string, passed through.
Nothing in this bundle needs the value — ordering is by the order we first saw
things — so parsing it would only create a way to be wrong.
