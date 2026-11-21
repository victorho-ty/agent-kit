# CLI and deployment

Global flags precede subcommand: `video-pipeline --data-dir /ABS/STATE
--config /ABS/config.yaml COMMAND`. Config is optional; copy bundle's
`config.example.yaml` to ignored `config.yaml` when needed. Console script must
be on gateway PATH. Hermes routes are task handoffs to the invoking agent,
not subprocesses that guess profile credentials or provider configuration.

## Linux installation

```bash
cd /path/to/agent-kit/ai-researcher/video-pipeline
uv sync --extra youtube
uv tool install '.[youtube]'
sudo apt-get install ffmpeg fonts-dejavu-core
video-pipeline install-skill --hermes-home /actual/profile/home
video-pipeline doctor
```

`--hermes-home` is the profile's effective HERMES_HOME, not necessarily ~/.hermes.
Installer copies skill/references and refuses to overwrite existing skills.
Python wheel includes skill data under share/video-pipeline. Skill installation
does not install model weights, change Hermes config, or register jobs.

## Selecting models

Cloud: `draft --job ID --generator hermes`. Returned JSON contains request_id,
system and payload. Current Hermes profile model generates output itself. It
returns `{ "request_id": "exact returned ID", "output": { ... } }` via --input.
Keep response files inside job directory. No cloud endpoint/key/model is stored.
Changing Hermes profile model changes the next Hermes task automatically.

Local: set `VIDEO_LLAMA_URL=http://127.0.0.1:8080/v1` and optionally
`VIDEO_LLAMA_MODEL` (server model ID/alias). If omitted, `/v1/models` must advertise
exactly one model. `video-pipeline models` checks selection. Use
`draft --job ID --generator llama-cpp`. Set token only through environment variable
`VIDEO_LLAMA_API_KEY` if server requires it. Endpoint must be reachable from
Hermes terminal backend: localhost inside Docker refers to that container.
llama.cpp needs a chat template and JSON response_format support. Increase
max_tokens/timeout explicitly for slow local models; errors never route to cloud.
Review defaults to opposite route; same route is refused. Underlying model
independence still requires a genuinely different model in Hermes profile.

## Evidence

`create --topic TEXT --language en --url URL --url URL` fetches readable HTML.
For blocked/JS pages use Hermes browser/extraction tool, then import:

```json
[
  {"url": "https://example.org/announcement", "title": "Source title", "text": "Full fetched article evidence, at least 100 characters, copied faithfully from extraction tool."}
]
```

Maximum eight sources, 16000 chars/source. Import data can come from news-radar's
`evidence` list: id/item_id fields are normalized to S1, S2, etc. No separate
discovery scheduler/database needed. Canonical source URLs + language identify a
story; same inputs return existing job, including upload state.

## Draft/review

Returned model task contains current schema. Draft: title, scenes containing
narration/visual/source_ids, and claims containing text/source_ids. Every spoken
factual claim must appear in claims. Drafts have 1-12 scenes and 1-40 claims.
No numerical durations are requested from model.

Review: verdict pass/reject, script_supported boolean, adult_content boolean,
checks covering every zero-based claim_index exactly once. Each check includes
supported boolean, reason, and quotes [{source_id, text}]. Supported claims need
exact contiguous quotations in cited evidence. Reviewer checks omitted spoken
claims, implications and translation as well. Source attribution and exact quotes
do not prove semantic entailment: reviewer owns that judgment.

Task response cannot be replayed after a draft/evidence change. Revisions preserve
artifacts and invalidate review/render/QC. Maximum three successful draft revisions;
model/validation failures are surfaced, not automatically retried.

## Media and acceptance

`render --job ID --assets /ABS/images.json --audio /ABS/audio.json`.
Each file is a JSON array in scene order. Images can contain null to request a
caption card. Audio entries must all be existing narration paths. Generated Edge
audio is online and separate from text-model choice. For entirely local media,
supply narration produced by your local TTS; llama.cpp chat is not a TTS service.
Image/audio inputs are copied/converted into a new immutable render directory.
Set config font to installed TTF/OTF supporting requested language (e.g. Noto CJK).
Caption timing is estimated by character count inside measured scene duration;
inspect sync. No forced translation: draft task writes requested language.

`status --job ID` returns render_hash, draft and artifact paths. Inspect scene
frame-N.png, narration.wav, captions.srt and video.mp4. Visual review file:

```json
{"render_hash": "copy exact returned hash", "passed": true, "notes": "Record actual checks and any limitations."}
```

`qc --job ID --visual-review /ABS/review.json` checks streams, dimensions, measured
duration, black footage >=0.5s, silence >=2s and file hashes. Missing visual verdict
does not block technical QC, but blocks upload. File/draft/review changes invalidate
acceptance. Failed attempts remain on disk; no automatic deletion or hidden fallback.

## YouTube

Enable YouTube Data API; create OAuth desktop client. Keep credentials under profile's
private artifact/config directory, outside repository. Authorize interactively once:

```bash
video-pipeline youtube-auth --client-secrets /PRIVATE/client_secret.json --token /PRIVATE/youtube.token.json
video-pipeline upload --job ID --token /PRIVATE/youtube.token.json --privacy private
```

OAuth uses callback port 8085; for headless Linux, forward it over SSH and complete
browser authorization. Token must stay on authorized machine. Upload token refreshes
in place; no secret values returned. Default visibility private; public/unlisted must
match user's authorization. Upload includes source links and synthetic-media flag.
Captions already burned in; sidecar SRT retained locally. No remote caption upload.

Upload intent committed before first network chunk. Interrupted/failed upload stays
unknown and cannot retry automatically. Inspect channel, then record confirmed ID
using reconcile. If no video exists, investigate resumable session externally before
any new publication; this bundle intentionally has no reset/delete shortcut.

## Scheduling

When requested, attach video-pipeline skill to Hermes cron using current supported
cron tool/CLI. Include absolute data/config paths and chosen text generator in prompt.
Leave model/provider unset to inherit profile configuration. Gateway must remain
running; cron platform needs terminal/files/web/media tools and same executable PATH.
No jobs are installed by this bundle. SQLite serializes mutations per data directory;
each command rechecks gates. Avoid parallel complete runs for same job.
