# Staged CLI and JSON contracts

Each command prints one JSON object. Exit 0 means a successful operation; partial
search/evidence results carry `status: partial` and explicit failures. An entirely
failed collection exits 20. Invalid input exits 10. Unknown items/drafts exit 30.
Read failures even when `ok` is true. JSON inputs are capped at 250000 characters.
`--input -` reads stdin. Do not put API keys in these files.

## Research and ingestion

`research` returns `as_of`, `window_start`, `lookback_days` and four `jobs`, each with
`source`, `category`, `query`, `reference_url` and `max_items`. `reference_url` identifies
the vendor; it is not an article to ingest. `research --topic "keywords"` returns
community jobs. Commands themselves make no search-provider calls.

The vendor plan also returns `feed_jobs` and `feed_count` for enabled non-search
sources. For each feed job, run `scan --source SOURCE`; it stores new entries directly
in the shared ledger, so do not also ingest the scan output. `count` counts search
jobs only. Community plans return no feed jobs. Copilot is the default RSS source;
its initial scan seeds existing history silently, and subsequent arrivals become
pending. Feed items use the same shortlist/evidence/briefing/HTML delivery stages.

Pass actual search metadata, for example:

```json
{
  "results": [
    {
      "source": "anthropic",
      "items": [{
        "title": "Title copied from a search result",
        "url": "https://www.anthropic.com/news/article-slug",
        "snippet": "Search-provider snippet, not an agent-written summary",
        "published_text": null
      }]
    },
    { "source": "google", "items": [] },
    { "source": "openai", "error": "Search tool unavailable for this query" }
  ]
}
```

This is a shape illustration, not news to ingest. Successful records require `items`.
Failed records use `error`; empty results are not errors. Each source may appear once
per input. Ingest validates the whole document before writing. It canonicalizes links,
removes configured exclusions, and deduplicates by source plus article URL even when
a title changes. Newly discovered search stories are pending immediately, including
on the first run. Previously reported or dismissed URLs are not reintroduced.

## Selection and evidence

`shortlist --limit 100` returns category `sections` with clustered `stories` containing
`ids`, `title`, `url`, `sources` (publisher domains), `vendors` (configured topics),
`published_text` and `snippets` tied to IDs. `count` is items returned, not story count.
`pending_items`, `has_more` and `legacy_pending_items` describe the queue. Limit 1–500.

`evidence --id 1 --id 2` returns `evidence` records with `item_id`, resolved `url`,
`title`, `published_text`, `text`, `fetched_at`, and `cached`. Fresh fetches also report
`content_root` and `truncated`. Failed items appear under `failures`; they are not
converted into empty evidence. `--refresh` replaces cached evidence only after a
successful fetch. Per call: at most `detail_budget` IDs, each response at most 3 MB,
and readable text at most 12000 characters. Python extracts article/main/body text,
not PDF or JavaScript-rendered content. Inspect login/bot pages and truncation.

## Briefing input

`briefing --input draft.json` expects exactly these top-level keys:

```json
{
  "highlights": [{"text": "A supported highlight", "item_ids": [1]}],
  "stories": [{
    "item_ids": [1],
    "headline": "A supported headline",
    "summary": "A summary supported by item 1's fetched article text.",
    "urls": ["https://www.anthropic.com/news/article-slug"]
  }],
  "community": []
}
```

Community entries contain `item_ids`, `summary` and `urls`, without a headline or
why-it-matters field. Stories no longer accept `why_it_matters`; include a short
implication in `summary` only when useful. Highlights are single-line bullets up to
160 characters. Headlines are at most 100 characters, summaries at most 220, and a
story's headline plus summary at most 280 (excluding source URLs). Community summaries
are also capped at 220. Multiline text and overlong drafts are rejected for the editor
to shorten; Python does not truncate claims. Use community-source IDs there, not vendor articles. At most
five highlights, three community entries and `detail_budget` vendor stories.
Each entry needs 1–3 distinct URLs belonging to its own fetched evidence; at least
one must be a resolved evidence URL. Neither an ID nor a URL can appear in multiple entries.
Highlights must refer to included vendor stories. All cited IDs must be pending
and belong to enabled sources. Every ID needs successful cached article evidence.

Output contains `briefing_id`, `created_at`, `committed_at`, `content`, `body` and
Python-computed `totals`. Vendor labels and publication text are added from the
ledger. No semantic fact-checking is claimed. An empty `stories` list is refused;
when nothing merits reporting, do not draft. The text body has Highlights, Stories
and optional Community Pulse, with one bullet per entry and all its URLs inline.
`body` is internal output, not a channel message. The
HTML uses the same concise content in a responsive, styled document: a dark header,
teal highlights, readable story rows, and clickable source links. It works offline
without external fonts, scripts or styles. Retained older drafts remain readable;
their separate why-it-matters field is no longer rendered. `--html NEW_PATH` exports escaped HTML
without overwriting another file. The staged SKILL requires `--html` on every briefing
creation or recovery; choose a fresh absolute path with an existing parent directory.
Successful export adds `html_path` to the result. `briefing --id N --html NEW_PATH`
retrieves a retained draft and exports its attachment for retry.

## Delivery state

The SKILL sends only the HTML document attachment plus a one-line Success/Error
status; it never posts `body` or briefing content as chat text. After confirmed HTML
attachment delivery, run `commit --briefing N`. An export or attachment-delivery
failure leaves the draft uncommitted; retry the failed step. A commit failure after
upload requires an acknowledgement retry, not another upload. A status-message
failure does not prevent committing a delivered attachment; retry only that status.
Only IDs included in that draft are stamped. A repeated acknowledgement returns `already_committed: true` with zero new
commits. A draft whose items were reported/dismissed elsewhere is stale and refused.

`dismiss --id N --reason "duplicate coverage"` records an editorial disposition and
removes a pending item from the queue without deleting it. Unselected items stay
pending unless explicitly dismissed. Old records are never deleted by this workflow.

`sources --all`, `items --source anthropic --limit 20`, and `runs --limit 3` inspect
configuration, retained items, and research ingestion health. Feed `scan` and legacy
`digest` remain supported for explicit feed configurations, but refuse the new default
search workflow so old cron commands cannot silently stamp unreviewed discoveries.
