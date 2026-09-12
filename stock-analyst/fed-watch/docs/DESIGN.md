# fed-watch — design notes

## Why this computes instead of scraping

The obvious reading of "scan the CME FedWatch page" is a scraper. That was
investigated first and rejected on evidence.

The tool is not on `cmegroup.com` at all. The page embeds a cross-origin
ASP.NET WebForms application served from `cmegroup-tools.quikstrike.net`, which
returns a generic error page unless the request carries a `cmegroup.com`
referer. The parent page sits behind Akamai bot management: a real browser loads
it, and headless Chromium is dropped with `ERR_HTTP2_PROTOCOL_ERROR` before any
markup arrives. A scraper here would need a headful browser on the cron box and
would break on any redesign of a tool we do not control.

The alternative is that FedWatch is arithmetic over a public futures price, and
CME publishes the method. Recomputing it costs two HTTP calls, needs no key, no
browser and no rendering, and is testable offline against fixtures.

**It is also exact.** Given CME's own mid price, the pipeline reproduces CME's
published table for 16 September 2026 to the tenth of a point — 13.3% no
change, 86.7% a 25bp hike. That equality is a test, not a claim.

The residual ~1pp against the live page is the price source: Yahoo serves a
traded print, CME publishes a settlement mid. The arithmetic contributes nothing
to it.

The price is the last **one-minute** print, not the daily bar's `Close`. Yahoo
disagrees with itself on those -- 96.2675 against 96.2700 for ZQU26 on the same
11 September 2026 session, which is another percentage point of probability --
and the daily bar is kept only as a fallback for when the intraday series is
empty. It also gives `price_as_of` a real tick timestamp rather than midnight. `price_source` and `attribution` ride on every payload so the figures can
never be mistaken for CME's own.

See `skills/fed-watch/references/methodology.md` for the derivation and the
validation table.

## Module layout

```
fed_watch/
  probabilities.py   the calculation. pure stdlib, no I/O, no clock
  contracts.py       ZQ symbol construction
  config/fomc.py     the FOMC calendar, and "does this month hold a meeting"
  rates.py           EFFR and the target range, from the New York Fed
  quotes.py          dated ZQ prices, from yfinance
  snapshot.py        fetch -> compute -> store, and the payload shape
  changes.py         diffing two snapshots, and reshaping history for charts
  charts.py          matplotlib, imported lazily
  db.py              SQLite
  cli.py             argparse; one JSON object per command
```

`probabilities.py` depends on nothing that touches a network or a clock, which
is what lets the validation against CME run as an ordinary unit test.

## Three decisions worth recording

### The calendar is load-bearing, not decoration

Which contract answers a meeting depends on whether the *following* month holds
a meeting. A late-month decision solved out of its own month's contract divides
by a small number of days and multiplies price error tenfold — for 28 October
2026, by 10.3. Read off a clear November instead, it is exact.

So a hole in `config/fomc.json` does not produce an error. It makes an occupied
month look clear and produces a confidently wrong number. Hence: the loader
insists the dates are sorted, unique and parseable; running out is a hard
`ERR_CONFIG` with a remedy rather than an empty result; and `snap` and
`meetings` warn once fewer than three dates remain ahead.

### The effective rate is fetched, never assumed

Using the midpoint of the target range instead of the published EFFR moves the
answer by four percentage points. Both figures are pinned by tests so the
substitution cannot creep back in as a "simplification".

The New York Fed endpoint returns the target range alongside the rate, so
neither is configuration. A target range typed into a file goes stale on the
one afternoon it matters most.

### The change baseline is the last *report*, not the last poll

ZQ trades continuously, so consecutive polls always differ slightly. Comparing
poll to poll, a drift of a third of a point an hour never clears a
one-point threshold and is never reported, while the probability quietly walks
ten points across a day.

`snapshots.reported_at` is stamped only when `check-changes` actually reports
something. A quiet poll leaves the baseline where it was, so drift accumulates
against its origin and is reported once — then the baseline moves.

The cost is that `delta_pct` is not "since the last poll" and must not be
described as such. `baseline_taken_at` travels in the payload for exactly that
reason, and the SKILL tells the agent to quote it when the gap is wide.

## Storage

Three tables. `snapshots` is one row per reading; `meeting_snapshots` one row
per meeting within it; `outcomes` one row per target-range cell. Ordered by
`taken_at` that is a time series, grouped by `meeting_date` it is one meeting's
path — both readings the brief asked for, from one write.

Timestamps are the isoformat of a timezone-aware instant in one fixed zone, so
lexical ordering is chronological ordering.

The database lives under `hermes-stock-analyst/`, shared with the stock-desk
bundle, because state is scoped to the profile rather than the bundle. Charts
deliberately do **not** share `stock-desk`'s directory: that bundle sweeps every
PNG in its own chart directory on a retention timer and would delete these.

## What is deliberately absent

- **No conditional tree beyond a uniform second-meeting increment.** Immaterial
  over two meetings with one 25bp step in play; documented rather than hidden.
- **No intermeeting moves, and no non-25bp increments.**
- **No forecast of any kind.** The bundle reports what is priced. Nothing in it
  produces a view, a target or a position.
- **No historical backfill.** Expired ZQ contracts are delisted from Yahoo
  rather than archived, so history begins when snapping begins.
