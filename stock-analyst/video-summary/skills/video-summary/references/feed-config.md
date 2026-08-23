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
| `detect_shorts` | `true` | one extra request per new video to label short vs long |
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
| `transcript` | `true` | `false` sends title, link and thumbnail only |
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
  indistinguishable. `detect_shorts` resolves this with one request per new
  video: `/shorts/<id>` stays put for a Short and redirects to `/watch` for
  anything else. If the request fails, `kind` is `unknown` — a video is never
  lost over a label.
- **View count, likes, or anything else that changes after publication.** Which
  is a feature: nothing here needs re-reading a video it has already seen.

Videos absorbed by a **cold start are never resolved**. Fifteen redirects to
label a back catalogue nobody will be shown is fifteen wasted requests.

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
