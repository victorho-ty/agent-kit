# Design

Model owns research selection, script, factual/visual judgment. Python owns evidence
extraction, canonical URLs, hashes, state transitions, model HTTP transport, timing,
rendering, technical QC and publication receipts.

## Adapted Verticals

Upstream revision: 48835dea9c0cc9d119e19ad9365717006d6f59a6.
Media code adapts broll.animate_frame's FFmpeg zoompan expressions,
assemble.assemble_video's concat + audio + caption muxing, and captions._srt_time
formatting. No runtime upstream dependency, dynamic clone, or upstream cloud provider
registry. Attribution/license retained in THIRD_PARTY_NOTICES.md. Narrow adaptation
avoids importing upstream CLI's auto-upload, search-snippet fallback, global output
directory and optional silent caption omission.

## Model routing

Hermes profile mode returns schema + evidence + request ID to invoking agent, which
is already running selected profile model. Backend never guesses OAuth tokens or
reads provider-specific profile config. This works with cloud subscriptions/custom
providers without forcing provider API credentials into subprocesses.

llama.cpp mode talks directly to configured OpenAI-compatible endpoint, optionally
discovers single served model, requests JSON and rejects incomplete responses. No
implicit fallback. Config/environment endpoint and explicit generator selection
available to CLI/skill. Narration and image assets remain separate media inputs.

## State and failure behavior

SQLite ledger stores evidence, draft/review, artifacts, QC and publication per story.
BEGIN IMMEDIATE serializes mutating commands per profile directory. Sources + language
dedupe job creation. Revision invalidates downstream state; fingerprinted request IDs
reject stale task responses. Maximum three draft revisions. Every render uses fresh
UUID directory; failed artifacts retained. No deletion API.

Review must use opposite route and each supported claim needs an exact source quote.
This is structural provenance validation, not semantic proof. Independent underlying
model identity is operator's responsibility when Hermes itself points to local model.

Render hashes bind video/subtitles to exact draft and reviewer verdict. QC validates
streams, duration, dimensions and black/silent intervals. Visual/editorial acceptance
binds to full render record hash and remains separate from technical QC. Publication
requires all current gates. Intent committed immediately before first remote upload
chunk. Unknown result blocks retries; explicit reconciliation records confirmed ID.

## Scope

Initial style: caption cards or supplied images with pan/zoom; optional online Edge
TTS or supplied per-scene narration. Caption timing estimated within measured scene
audio. No diffusion video, image generation service, cloned voice service, ASR,
background music, platform analytics or automatic schedule creation. Extend media
inputs after end-to-end deployment measurements establish need. Existing news-radar
can provide discovery/evidence without new collection stack.
