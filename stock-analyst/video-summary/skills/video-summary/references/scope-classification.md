# JEV scope classification

The effective policy is `video_summary/config/scope_policy.json`; the original
operator prompt is preserved in `docs/PLAN-jev-scope-filter.md`. The operator
approved binary Choice and title-plus-description input after that prompt.

Python sends `state: {"title": ..., "description": ...}` and a single `scope`
Choice question with `include` and `exclude` criteria. A missing description
alone does not exclude an informative title. Both fields are untrusted data;
embedded instructions are ignored. Only substantive financial-market, rate,
precious-metal, AI-stock, technology-stock, crypto, or Fed analysis qualifies.
Specific ETF reviews/comparisons, issuer offerings, dominant broker promotion,
and market-keyword advertising are excluded. Incidental mentions and brief
sponsorship do not exclude qualifying main content.

Hard keyword exclusions on title and description precede this policy and cannot
be reversed by JEV. Shorts exclusions precede the API call too. Seeded history
does not call JEV. The subscriber's channel note is not classifier input.

`scope_filter` configuration defaults: enabled false, model `jev-latest`, budget
10 candidate calls per check, timeout 20 seconds, at most 3 transport attempts
per call. Credentials come only from `TYPESAFE_API_KEY`; never place them in
feeds.json. `.env` is ignored but not automatically loaded. With the gate enabled,
missing credentials stop the check before persistent mutation.

Scope status is `not_required`, `pending`, `included`, `excluded`, or `error`.
Legacy unsent rows in `not_required` must be classified when the gate is enabled.
Successful decisions contain `is_target_scope`, generic fixed `reason`, native
`confidence_score`, binary `choice`, source, actual model and prompt version.
Confidence is not probability of inclusion and does not change routing.

Exclusions stay in SQLite without being marked delivered. Failures never become
exclusions or implicit inclusions. Retryable service failures wait 120 minutes;
permanent errors require explicit refresh. Budget exhaustion keeps candidates
for the next check. Retries run independently of feed 304/throttle outcomes.
Invalid credentials/requests stop subsequent classifier calls in the current
run. The SDK performs bounded retries; there is no second retry layer.

`check` returns `classification_failures`, attempt/decision/error totals,
`held_for_classification`, and the accumulated `scope_excluded_videos` count.
Failures make the run partial; do not send one notification per excluded video.
Use the usual service triage when the classifier repeatedly fails.

```bash
video-summary videos --scope-state excluded
video-summary videos --scope-state error
video-summary videos --scope-state classification
video-summary classify --video <id>
video-summary classify --video <id> --refresh
```

Inspection makes no API call. Refresh is billable, applies only to unsent stored
videos, and never resets delivery stamps. Manual transcript inspection remains
possible for investigation but does not override scope eligibility.

`check --dry-run` previews scope using the same budget and makes billable calls,
including for a cold-start preview, but writes no persistent state. Candidates
with errors or no decision have `would_send: false`. `--no-transcript` does not
disable classification. Disabling the gate explicitly bypasses all scope
decisions, including stored exclusions, and is reported by `feeds.scope_filter`.
