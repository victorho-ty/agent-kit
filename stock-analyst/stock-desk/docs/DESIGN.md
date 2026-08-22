# Design notes

Why this bundle is shaped the way it is. Written for whoever changes it next,
including a future me who has forgotten which of these decisions were forced.

## The token argument

An agent doing this conversationally — pulling prices, computing moving averages
in context, searching news for five tickers and their four peers each — costs
somewhere around 40–80k tokens a day and produces a different answer every time
it runs. This bundle exists to move all of that into Python and hand the agent a
payload it can read in about two thousand.

Four mechanisms do the work, and each is load-bearing:

**Python holds the client end of the MCP pipe.** News, sentiment and macro all
come from MCP servers, but the *agent* never calls them. Measured on this
watchlist: one Yahoo `get_news` reply is ~6,400 tokens of JSON for ten stories,
of which the four fields actually stored are ~15%; one Alpha Vantage
`news_sentiment` reply is ~28,000 for fifty items, of which two survived
filtering. Ten symbols twice a day is ~128k tokens of intake before a word is
written. Routed through the agent that lands in context every run; routed
through `providers/mcp_client.py` it lands in SQLite once and never again.

**Pre-rendered status lines.** `setups.status_line` builds the one-line summary
for every ticker that did not earn a paragraph. The agent relays them verbatim.
This is why the daily cost is driven by how many setups fired rather than by how
many tickers are watched — forty tickers produce forty strings and not one model
token.

**The pending gate.** `stockctl pending --count` prints a bare integer. The cron
wrapper tests it and only invokes the agent when it is above zero. On a quiet
afternoon that is eight polls and zero agent invocations.

**The bar cache.** Only days after the newest stored bar are fetched. A first
sync pulls two years; every sync after it pulls one bar per ticker. That is what
makes it affordable for the detector to demand a full year of history for its
percentile windows.

## Two clocks

The single most important structural decision, and the one most likely to be
undone by accident.

**Detection is polled on a schedule. Alerting is driven by events.** They are
decoupled through the database: `news.notified_at` and `events.notified_at` are
null until something is reported, and a null row is the only reason anybody is
disturbed.

The consequence is that polling frequency and alert frequency are independent
knobs. Poll every fifteen minutes if you like — nothing downstream cares, nobody
is woken, and no tokens are spent.

The failure mode this avoids is the naive one: query "earnings within 10 days"
on a daily cron and alert on the result, which announces the same earnings date
every morning for ten consecutive days until the operator mutes the channel.

A moved date is genuinely new, though. `UNIQUE (ticker, kind, event_date)` means
a date shifted from the 12th to the 15th inserts a fresh row and alerts again —
correct, because the change is the news.

## Why the pivot excludes the current bar

`compression.pivot_level` takes the base's high over every bar *except the last*.

If today's own high defines the pivot, then on the day price finally clears the
range the pivot rises with it, and `close > pivot` is never true. The breakout
would be undetectable by construction. Depth still uses the whole window, because
how deep the range is genuinely includes today.

`pivot_touches` counts the same bars for the same reason: today's action must not
inflate the count of its own level.

## Why the squeeze gauge holds a veto

`tightness_signals` returns four booleans and the coil test originally required
any one of them. Run against live NVDA data on 2026-08-14 that produced a
`coiled` verdict with band width at the **94th percentile** of its own year — a
single NR7 bar outvoting both squeeze gauges.

That is not a tuning miss, it is a category error: the premise is that volatility
has *fallen*, and a range at the 94th percentile has done the opposite.
`max_bbw_percentile` now rejects the call outright. Contracting swings inside a
still-wide range are a base.

## Why prior expansion needs a margin, not just a percentile

`percentile_rank` counts ties with `<=`. On a stock whose volatility barely
varies, the run-up equals nearly every historical value and ranks near the 100th
percentile — reporting a prior expansion for a stock that has never expanded.

A bare "greater than the median" does not fix it either: ATR% is ATR divided by
close, so an oscillating close alone lifts the mean above the median by a
fraction of a basis point. Hence `expansion_margin`, which requires the run-up to
clear the median by 15% — nothing to a genuine expansion, fatal to a flat one.

Found by a test on a perfectly periodic fixture, which is the kind of degenerate
input real data never quite produces and synthetic data produces immediately.

## Why the base search takes the longest window

Depth is monotone in window length, so the search walks outwards until the band
breaks and keeps the longest qualifying window.

Taking the *shortest* qualifying window would find a base on everything — every
sideways stretch contains a tight three-day window. The minimum length of 7 and
the maximum of 60 bound it at both ends.

## Two cost bases

Open positions are carried at **average cost**; realised profit is computed
**FIFO**. These answer different questions — "am I up on this line" versus "what
does the tax authority see" — and reporting one number for both would be wrong
for one of the two purposes, always.

Selling more than is held opens a short rather than raising an error. A swing
trader shorts, the arithmetic is symmetric, and refusing to record a trade that
really happened would make the log a worse record than the broker statement.

## Why the classifier runs before the sentiment score

Alpha Vantage ships a `relevance_score` that looks like a filter and is not. On
a live NVDA query every one of fifty items scored between 0.52 and 1.00, and the
1.00 bucket held a piece about the CEO's daughter next to a 13F filing headlined
"NVIDIA Corporation $NVDA Stake Increased by Paladin Wealth LLC" — which the
same vendor labelled **Bullish**.

Relevance asks "is this about the company". Materiality asks "does this change
anything". Only the second is worth waking somebody for, and no vendor supplies
it. So `materiality.py` classifies first and the vendor's sentiment is a
decoration on a story that already earned its place — never a reason to include
one.

The subject gate is separate again, and lives in `feeds.py`. Alpha Vantage's
per-article `ticker_sentiment` list is *ranked*, and the top-ranked ticker is
reliably the article's real subject: zero of the 43 off-topic items in that
sample had NVDA at the top, while the four on-topic items that did not name NVDA
in the title were caught by the alias check. Absolute relevance separates
nothing; relative rank plus aliases made no errors either way.

## Why news dedupe is scoped to the ticker

`UNIQUE (ticker, url_hash)`, not `UNIQUE (url_hash)`.

A global constraint means one AMD story can be stored once, under whichever
watchlist entry was polled first. Observed live: NVDA lost all six of its AMD
peer stories to CBRS, because both declare AMD as a competitor and CBRS sorts
earlier. The module already said clustering never crosses tickers for exactly
this reason; storage had been contradicting it.

## Why Alpha Vantage calls are spaced out

The free tier limits requests per *second* as well as per day, and enforces it
by answering with HTTP 200 and a body that reads "please spread out your free
API requests more sparingly". No error status, no retry header, and the wasted
call still counts against the daily 25. Three of six back-to-back news calls
came back that way. `mcp_client` sleeps between calls to the metered server and
to no other.

That same error path is why `redact()` exists: the vendor's quota message quotes
the API key back at you, and that string would otherwise travel from the failure
list into the report payload, into the model's context, and out to a Telegram
chat.

## Provider strategy

Bars come from yfinance in-process, and only bars. It scrapes an undocumented
endpoint, so it breaks — which is what the Stooq fallback is for. Everything
else with a vendor behind it now arrives over MCP, because the servers are
already configured for the interactive agent and a second copy of the same
credentials in a second config file is a thing that goes stale.

Stooq covers only daily bars, and only US listings, and that is deliberate: bars
are the one thing whose absence stops the whole desk working. Fundamentals and
the earnings calendar can wait a day. An HK ticker raises `FetchError` rather
than returning nothing, so a caller never mistakes "unsupported" for "no new
bars".

Field lookups go through `_first(...)` because Yahoo renames keys between
releases, and guessing one name and getting `None` looks exactly like a company
with no earnings.

## What is pure and what is not

`indicators`, `compression`, `setups` and `portfolio` are pure functions over
plain data — no pandas, no network, no clock. Everything they need is an
argument.

That is what makes the whole suite run in 0.3 seconds against hand-written bars,
and it is why a fixture is a list of `Bar` rather than a database. pandas appears
only in `charts`, at the edge, because mplfinance requires a DataFrame.

`clock.now()` exists so tests can pin the instant. A detector whose answer
depends on the wall clock is a detector nobody can write a regression test for.

## Testing the detector on synthetic bars

`tests/conftest.py` builds one scenario: 200 quiet bars → 15 volatile bars rising
hard → 3 bars diving deep → a 21-bar base whose swings shrink in three steps.

Three details in there are not arbitrary, and each was found by a failing test:

- **The deep pullback is ordered deepest-last.** The bar adjacent to the base has
  to be the one that breaks the depth limit; put it earlier and the base search
  steps straight over it.
- **The base is 21 bars, not 16.** Band width is a 20-bar window. A shorter base
  leaves that window straddling the pullback behind it, so the reading describes
  the dive rather than the base, and the squeeze veto rejects a coil the fixture
  means to be genuine.
- **The background oscillates on a four-bar cycle.** Band width must be *wider*
  in the background than in the base, or every base ranks high and the veto
  fires; ATR% must be *lower* than in the rally, or the prior-expansion test
  finds the background more volatile than the expansion. Close dispersion drives
  the first and bar-to-bar true range drives the second, so pacing separates
  them — a four-bar triangle travels the same distance in half-steps.

The `steep_rally` helper exists because `expansion_run` is gentle enough that a
nine-bar window at its top genuinely qualifies as a shallow base. That is correct
behaviour, and useless for testing the no-base branch.
