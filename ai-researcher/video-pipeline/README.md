# Video pipeline

Skill + deterministic Python backend for sourced news shorts. Select text
generation through current Hermes profile model or existing llama.cpp server.
Rendering adapts Verticals' FFmpeg pan/zoom and concat approach with independent
review, evidence validation, artifact checks and upload recovery.

## Install on Linux

```bash
cd ai-researcher/video-pipeline
uv sync --extra youtube
uv tool install '.[youtube]'
sudo apt-get install ffmpeg fonts-dejavu-core
video-pipeline install-skill --hermes-home /actual/hermes/profile/home
video-pipeline doctor
```

Load `/video-pipeline` in Hermes. Console command must be on gateway PATH.
Skill/references ship in wheel and source bundle; installer refuses overwrite.
Use active profile's actual HERMES_HOME; state defaults to its artifacts/video-pipeline.
Override with VIDEO_PIPELINE_HOME or global --data-dir. Keep credentials/state
outside checkout. Bundle config.yaml, .env, work/, artifacts/ and token JSON ignored.

## Models

| Selection | Execution |
| --- | --- |
| `--generator hermes` | Returns task for invoking Hermes agent; inherits profile's current model/provider/auth. Submit request_id/output envelope via --input. |
| `--generator llama-cpp` | Calls existing OpenAI-compatible /v1/chat/completions with JSON response_format. Discovers single served model or uses explicit server alias. |

```bash
export VIDEO_LLAMA_URL=http://127.0.0.1:8080/v1
video-pipeline models
video-pipeline create --topic "Selected announcement" --url https://example.org/article
video-pipeline draft --job RETURNED_ID --generator hermes
video-pipeline draft --job RETURNED_ID --generator hermes --input /ABS/draft-response.json
video-pipeline review --job RETURNED_ID --generator llama-cpp
video-pipeline render --job RETURNED_ID
video-pipeline status --job RETURNED_ID
video-pipeline qc --job RETURNED_ID --visual-review /ABS/visual-review.json
```

Response schemas returned by commands. Copy config.example.yaml to ignored
config.yaml for model endpoint/alias, voice, font and dimensions; pass global
--config /ABS/config.yaml. Optional authenticated llama.cpp uses environment variable
VIDEO_LLAMA_API_KEY, never a literal token in config. No fixed cloud model or cron pin.
VIDEO_LLAMA_URL and VIDEO_LLAMA_MODEL override corresponding YAML settings.

Review uses opposite route; two routes must represent different underlying models.
Same-route reviews refused. Draft factual completeness/entailment remains reviewer's
judgment; Python validates references, exact quotations, request IDs and hashes.

## Media

Default: caption cards + online Edge narration + measured scene timing + burned-in
subtitles + H.264/AAC MP4. Image scenes via --assets JSON; local/external narration
via --audio JSON. Each array contains one absolute path per scene. Images allow null
for cards. No hidden image-generation fallback or cloud image API required.

llama.cpp chat generates script text, not images/audio. Local TTS can supply narration
files; cloud profile selection does not change narration engine. Subtitle alignment
uses character-weighted scene timing, not word-level ASR. Inspect pronunciation and
sync before accepting video. Font defaults to installed DejaVu/Arial; configure
Noto CJK or another suitable font for non-Latin output.

## Publishing

Optional `[youtube]` dependencies, one-time OAuth and explicit upload command.
Private by default. Requires passing independent factual review, technical QC and
current visual/editorial acceptance. Unknown upload outcomes cannot blindly retry.
No schedules, uploads, or Hermes profile settings changed by installation.

See [workflow](skills/video-pipeline/references/workflow.md), [design](docs/DESIGN.md).

## Tests

```bash
uv sync --group dev
uv run pytest
uv run ruff check video_pipeline tests
```

Media integration test uses installed ffmpeg/ffprobe (or VIDEO_FFMPEG/VIDEO_FFPROBE)
and synthetic audio; model tests use local mock HTTP server. No paid APIs or live
publication needed for validation.
