# Search topics and configuration

`news_radar/config/sources.json` supports whole-line `//` comments. The shipped
configuration replaces the old feeds with five vendor queries adapted from
[kobyal/ai-latest-briefing](https://github.com/kobyal/ai-latest-briefing/blob/22774b57cb8c8e74e86a702d51072285dd62ab50/ai_latest_briefing/prompts.py):

| source | query |
|---|---|
| anthropic | Anthropic Claude latest news {month_year} |
| aws | AWS Bedrock latest announcement {month_year} |
| openai | OpenAI latest release {month_year} |
| google | Google Gemini AI latest update {month_year} |
| azure | Microsoft Azure OpenAI latest announcement {month_year} |

HN and Reddit jobs use `{topic}` from a selected story and `{today}`. They are
returned only by `research --topic`. Vendor jobs are returned by plain `research`.
GitHub Copilot is also enabled as an RSS source at
`https://github.blog/changelog/label/copilot/feed/` with `max_items: 30`. Plain
`research` includes it in `feed_jobs`; Hermes runs its targeted `scan` before the
shared shortlist. Its new entries go through the same selection and evidence-based
summarization as search discoveries. The feed does not need a `query` field.

The query may discover primary announcements and independent reporting; `url` is
the source's reference homepage, not a restriction on returned publisher domains.

## Global settings

| key | default | meaning |
|---|---|---|
| timezone | Asia/Hong_Kong | date context and timestamps |
| lookback_days | 1 | preferred discovery window, 1–30; not a hard item expiry |
| categories | required | ordered sections; community category identifies discussion |
| exclude | [] | case-insensitive substrings dropped before storing |
| request_delay_seconds | 2 | delay between selected article fetches |
| detail_budget | 10 | evidence IDs per command and vendor stories per draft |
| cluster_threshold | 0.7 | deterministic headline-overlap grouping |

No scheduler or LLM provider is specified here. Hermes uses its profile's model and
available search tool. No JEV or Gemini key is required by the Python package.

## Search source fields

Each source needs `name`, `category`, `url`, `kind: "search"` and `query`.
`category` must be declared. `max_items` caps ingestion per source per input;
`enabled: false` pauses a source. Query placeholders are limited to `{today}`,
`{month_year}` and `{topic}`. Date context is computed at command execution, not at
module import. Adding a vendor does not require editing a Python prompt or schema.

Run `sources` and `research` after an edit. Check a new query with Hermes web search,
then ingest real metadata. Search-source `min_interval_minutes`, `render`, selectors
and `follow_detail` do not control discovery; Hermes executes the jobs explicitly.

## Storage and compatibility

Existing item/run/source tables are retained. New evidence, review and briefing
tables are added without dropping or rewriting rows. The staged shortlist only uses
current enabled sources; retired-source pending records remain accessible with `items`.
Search results become pending on their first import; unlike feed seeding, research
has already selected a bounded result set. Duplicate identity for search ingestion
uses canonical URL within a topic. A reported article is not repeated merely because
its title or tracking parameters change.

`rss`, `html` and `regex` source configs use the existing feed scanner and appear
in the vendor plan's `feed_jobs` list. Their items join the staged shortlist after
a targeted feed scan; the shipped Copilot source follows this path.
Feed fields retain the existing CSS selector/attribute and browser/static behavior;
use `scan --source NAME --dry-run` to verify an explicit feed configuration.

Environment overrides remain `NEWS_RADAR_CONFIG`, `NEWS_RADAR_DB`, `NEWS_RADAR_TZ`,
`NEWS_RADAR_TIMEOUT`, `NEWS_RADAR_RETRIES`, `NEWS_RADAR_DELAY` (legacy feed pacing),
and `NEWS_RADAR_HEADLESS` (legacy browser feeds).
