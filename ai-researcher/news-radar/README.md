# news-radar

A staged AI daily briefing for a Hermes profile. Hermes discovers stories through
web search, selects the useful developments and writes evidence-supported summaries
using its configured model. Python plans queries, stores discoveries, deduplicates,
fetches selected articles, validates drafts and tracks delivery.

The default coverage follows the vendor searches in
[kobyal/ai-latest-briefing](https://github.com/kobyal/ai-latest-briefing/blob/22774b57cb8c8e74e86a702d51072285dd62ab50/ai_latest_briefing/prompts.py):
Anthropic, OpenAI, Google Gemini and Azure OpenAI. Optional topic-specific
HN and Reddit searches supply Community Pulse. The GitHub Copilot changelog RSS feed
is scanned alongside these searches and joins the same LLM selection and evidence-based
summary pipeline. The upstream application is not
installed or copied. No ADK, Gemini or JEV dependency is introduced.

## Setup

Requires Python 3.12+ and uv:

```bash
cd news-radar
uv sync --group dev
uv run news-radar research
```

Hermes needs an available web-search tool. No Python search-provider key is required.
Existing installations can keep the `news-radar` console-script symlink pointing to
this project's `.venv/bin/news-radar`. Install the bundled `skills/news-radar` skill
in the Hermes profile using the existing deployment process.

## Pipeline

```text
research → feed scan + Hermes web search/ingest → shortlist → Hermes selection
    → evidence → Hermes writing → briefing → authorized delivery → commit
```

| command | purpose |
|---|---|
| `research` | Four vendor search jobs and configured feed scan jobs |
| `research --topic "story keywords"` | Optional HN/Reddit jobs |
| `scan --source github-copilot` | Collect new changelog entries into the shared queue |
| `ingest --input results.json` | Validate/store actual search metadata |
| `shortlist` | Compact clustered pending stories from enabled sources |
| `evidence --id ID` | Fetch/cache bounded readable article evidence |
| `briefing --input draft.json --html NEW_PATH` | Validate, retain and export a Hermes-written briefing |
| `briefing --id ID --html NEW_PATH` | Retrieve a saved draft and export its attachment for retry |
| `commit --briefing ID` | Acknowledge successful delivery of that draft |
| `dismiss --id ID --reason "reason"` | Record editorial rejection without deleting data |
| `sources`, `items`, `runs` | Inspect configuration, retained items and research health |

Repeat `--id` for selected articles. The SKILL requires `--html NEW_PATH` for every
briefing and delivers it as an HTML document attachment, including on Telegram.
Channel text is limited to a one-line Success/Error status; the briefing body is not
posted as a text message. The CLI exports a responsive HTML document with a dark header, teal Highlights,
readable fonts, sectioned story bullets and clickable source links; it refuses to
overwrite existing files. Text and HTML share the same concise content: up to five
short Highlights bullets and one line per story, with links inline. Draft validation
allows up to five Highlights bullets, capped at 160 characters each,
and caps each story's headline plus summary at 280 characters;
there are no separate why-it-matters paragraphs.
Use a fresh absolute path with an existing parent directory. Each command prints one
JSON object. All input/output shapes: [CLI reference](skills/news-radar/references/cli.md).

Summaries require fetched article evidence. Search snippets support shortlisting,
not article summarization. The validator verifies structure, pending IDs and citation
provenance; Hermes must still verify factual claims against evidence. Missing or
blocked article content is reported explicitly. A vendor without substantive news
need not occupy a card. Community research and translation are optional, not forced.

## Daily scheduling and deployment migration

The shipped skill executes the entire workflow within one daily Hermes job. That
job must inherit the profile's configured model and use the authorized delivery
channel. This repository update does not install or modify a live schedule.

When deploying, replace old hourly `news-radar scan` and `news-radar digest --commit`
jobs with the staged skill invocation. They deliberately reject the new search
configuration rather than pretending to collect news or marking unreviewed stories
as delivered. Feed scanning remains available for explicitly configured legacy feeds.

A draft does not mark anything reported. Commit only after the HTML attachment is
confirmed delivered. Export or attachment-delivery failures leave the draft and
pending items available. Retry acknowledgement if commit fails after upload; retry
only the status message if that send fails. Never resend a confirmed attachment just
to recover its acknowledgement or status. A crash after delivery but before
acknowledgement can cause a duplicate send; inspect the delivery result before retrying.

## Configuration and state

Sources live in `news_radar/config/sources.json`. Add a search topic by configuring
its category, reference URL and query; Python code and model configuration need no
change. Query placeholders are `{today}`, `{month_year}` and optional `{topic}`.
`lookback_days` defaults to 1 and supplies a preferred freshness boundary, not an
expiry rule. The database, not the time window, prevents repeated articles.

[Configuration reference](skills/news-radar/references/source-config.md) describes
budgets, categories, exclusions, optional feed compatibility and runtime overrides.

The default database is `~/.local/share/hermes-ai-researcher/news_radar.db`.
Override with `NEWS_RADAR_DB`; `NEWS_RADAR_CONFIG` selects another source config.
Existing rows are retained. Evidence, review and briefing tables are created
additively. Pending records from retired sources are excluded from the new shortlist
but remain available through `items`. Nothing deletes old news or delivery history.

## Validation

```bash
uv run pytest -q
```

Tests run without network calls or API keys and cover search ingestion, evidence,
citation provenance, delivery acknowledgements and legacy database migration.
