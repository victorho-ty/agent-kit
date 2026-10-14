# news-radar design

## Ownership

One Hermes agent follows a staged skill using the profile's current model. It owns
web discovery, editorial selection, optional community research, interpretation and
writing. Python owns deterministic planning, ingestion, canonical URLs, clustering,
article extraction, output validation, rendering and retained state. There is no
Python model call, JEV gate, ADK runtime or provider-specific search integration.

The default vendor search queries follow ai-latest-briefing revision 22774b5; the
implementation is independent. Unlike that application, every vendor need not have
a story, Hebrew translation is not mandatory, citations cannot be substituted with
unrelated links, and a fresh execution does not discard dedupe history.

## Discovery and identity

`research` expands dates at invocation using the configured timezone. It returns
vendor search jobs plus targeted scan jobs for enabled feeds, or HN/Reddit jobs when
a topic is supplied. Copilot RSS collection uses a targeted scan, then joins the
same shortlist, evidence, summarization and HTML-delivery stages. Hermes records actual search
metadata; Python does not pretend a model's generated summary is source evidence.
The entire input is validated before ingestion writes. An empty result is a success;
a failed query is recorded as a failure. Omitted jobs are not claimed as completed.

Search identity is source plus canonical article URL, so tracking changes and
headline rewrites do not reintroduce a reported story. Exclusions run before storage.
First imports are pending rather than silently seeded: search results are bounded
and the editor controls whether an older result merits publication or dismissal.
The existing feed scanner keeps its own URL/title identity and cold-start rules.

## Shortlisting and evidence

The shortlist includes current enabled sources only and preserves the existing
category-scoped headline-overlap clustering. It exposes IDs and snippets for compact
batch comparison by Hermes. Grouping is advisory; the editor must not merge unrelated
news just because headlines overlap. Publication strings are preserved, not converted
into ages. The discovery window is a preference; undelivered backlog does not expire.

Only selected IDs are fetched. Each HTTP response is bounded to 3 MB and readable
text to 12000 characters, with a per-command item budget and inter-request delay.
Article/main/body text excludes script, navigation, forms and other chrome. Evidence
is cached; failed refreshes leave the previous successful record intact and return
an explicit failure. HTTP success and extraction are not semantic verification:
Hermes must inspect paywall/login/bot pages and cite only supported claims.

## Draft validation and delivery

Briefing inputs contain highlights, vendor stories and optional community entries.
Each entry has unique pending IDs, fetched evidence and citations belonging to those
IDs. Highlights refer to included vendor stories. Category validation keeps community
threads out of vendor cards. Counts, vendor labels and publication text are supplied
by Python. The validator checks structure and provenance, not factual entailment.

Drafts allow up to five single-line highlights (160 characters each). They enforce
story headlines (100 characters), summaries (220), and a combined headline/summary
budget of 280 characters. Implications
belong in the same summary rather than a separate paragraph. Overlong text is rejected,
never silently truncated. The channel body renders only Highlights bullets. HTML renders one bullet per
story with inline citations; the self-contained HTML adds responsive typography, color and sectioning.
Older retained drafts remain readable, but their separate implication field is not rendered.

Validated drafts are retained in the `briefing` table with a stable ID. Drafting and
HTML export do not change pending state. The CLI requires HTML export and returns
metadata, the attachment path and a Highlights-only `body`. Structured content
stays internal. The HTML header displays the current export date/time in the
configured timezone at the far right. Recovery uses its own export time. The SKILL
sends the HTML document attachment and returns its Highlights body as the final
channel response; failures return the actual error message. Once the authorized HTML
upload is confirmed, Hermes acknowledges
that ID with `commit`; only included items are marked reported. A failed Highlights send
does not block acknowledgement or require another attachment upload. Repeated
acknowledgement is idempotent. A stale draft whose items were handled elsewhere is
refused. Delivery and acknowledgement are separate operations, so exactly-once
messaging is not guaranteed across a crash between them.

Explicit editorial dismissal retains the item and records a review reason. Deferred
items remain pending. No silent failure path empties the queue or marks drafts sent.

## Migration and retained interfaces

Existing item, run and source-state rows are preserved. New evidence, review and
briefing tables are added. Old source names no longer enter the default shortlist;
`legacy_pending_items` makes that history visible without letting it contaminate the
new briefing. Feed parsers/scanning remain functional for custom feed configurations.
The old scan and digest CLI refuse a search configuration, preventing obsolete jobs
from silently operating with changed semantics. Deployment must replace those jobs
with the staged Hermes skill invocation at the framework cron job's chosen frequency; this code does not modify schedules.

## Limits

Search-provider availability and factual search metadata depend on Hermes's tool.
No full web crawl, PDF extraction, JavaScript article rendering or semantic fact-checker
is included. Cached evidence can age; refresh deliberately when an article changes.
Same-event deduplication across different URLs and different days remains editorial;
Python guarantees URL identity and supplies clustered pending candidates. No hardcoded
model, external delivery client, mandatory translation or synthetic community opinion
is part of the bundle.

The SKILL imposes no daily cadence. It may run on demand or any number of times per
day, reusing delivery state so earlier reported stories remain excluded. Runs for
the same profile must be serialized to avoid overlapping delivery decisions.
