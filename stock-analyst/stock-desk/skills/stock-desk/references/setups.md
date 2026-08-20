# Setups: what is detected, and how

Read this when explaining *why* a ticker got the stage it did, when tuning a
threshold after a run of false positives, or when the operator asks what the
score means.

## The sequence

The detector implements one definition, in order:

> High volatility → consolidation → progressively smaller swings → breakout

Each arrow is a separate test, and all of them must pass for `coiled`.

### 1. High volatility (`prior_expansion`)

Mean ATR% over the 20 sessions **before the base began**, ranked against the
ticker's own trailing year.

Passes when it clears the 66th percentile *and* exceeds the median by at least
15% (`expansion_margin`). The margin is not decoration: a percentile alone ranks
a stock whose volatility never varies at the 100th percentile, because
`percentile_rank` counts ties — which would report a prior expansion for a stock
that has never expanded in its life.

Why it matters: a stock that never moved has no energy to release, and its tight
range is just its normal condition.

### 2. Consolidation (`base_length`, `base_depth_pct`)

The **longest** recent window whose whole range fits inside 15%
(`max_base_depth`), between 7 and 60 bars.

Longest and not shortest, deliberately. Every sideways stretch contains a tight
three-day window; taking the shortest would find a base on everything.

Depth is monotone in window length — extending backwards can only raise the high
or lower the low — so the search walks outwards until the band breaks.

### 3. Progressively smaller swings (`contraction_ratios`, `contraction_monotone`)

The base is split into three equal thirds. Each third's range is divided by that
third's mean close, and each must be **at most 98%** of the one before
(`contraction_tolerance`).

Normalising by the mean close matters: a base that drifts 10% higher across its
length would otherwise show a shrinking range purely because the denominator
moved.

The tolerance stops a 0.5% difference counting as contraction.

**Plus** at least one corroborating signal that the range is tight *right now*,
because a base can contract steadily and then loosen again in its final week:

- `nr7` — narrowest range of the last seven bars, within the last 3 bars
- `inside_day` — entirely inside the previous bar's range, within the last 2
- `bbw_squeeze` — Bollinger band width at or below its 20th percentile
- `donchian_squeeze` — channel width at or below its 25th percentile

### The squeeze veto

Even with all of the above, a coil is **rejected** when `bbw_percentile` exceeds
60 (`max_bbw_percentile`).

This is not redundant with the signals above. Observed on live NVDA data:
contraction was monotone, an NR7 bar tripped a tightness signal, and band width
sat at the **94th percentile** of its own year. Contracting swings inside a
still-wide range are a base, not a coil — and the premise is that volatility has
*fallen*.

### 4. Squeezed toward a key level (`pivot`, `pivot_touches`)

The pivot is the base's high **excluding the current bar**, and it must have been
touched at least twice within 2% (`pivot_tolerance`).

Excluding today is load-bearing. A pivot is a level established by prior action;
if today's own high defines it, then on the day price finally clears the range
the pivot rises with it and the breakout can never be detected.

A level tested once is one day's high. Tested twice, it is somewhere sellers have
actually shown up.

Price must close within 3% below it (`pivot_proximity`) to be `coiled`.

### 5. Breakout (`triggered`, `volume_confirmed`)

Close above the pivot. `volume_confirmed` is true when RVOL is at or above 1.5×
(`volume_confirm_rvol`).

An unconfirmed breakout — price through the level on below-average volume — is
the classic failure mode and must always be reported as such.

### Failure (`failed`)

Yesterday's stage was `triggered` and today's close is back under the pivot.

This is the only stage that depends on stored state (`setup_state`), which is why
`scan --commit` matters: a scan whose verdict was never committed leaves tomorrow
unable to tell a failed breakout from a setup that never fired.

## The liquidity gate

Checked before any pattern is interpreted. Below `min_avg_dollar_volume` (default
$5M/day, 20-session average) the stage is `none` with reason `illiquid`.

A perfect coil on something that trades $200k a day is not a trade.

## The score

0–100, and a **ranking device only** — it orders a morning's candidates against
each other. It is not a probability, and a 90 that fails is not a bug.

| component | weight | full marks at |
|---|---|---|
| contraction | 25 | final third half the width of the first |
| band width percentile | 20 | 0th percentile |
| proximity to pivot | 15 | at or through the level |
| prior expansion | 10 | passed (binary) |
| pivot touches | 10 | 4 or more |
| volume dry-up | 10 | final third at half the first third's volume |
| tightness signals | 10 | all four tripped |

## The horizon

`technical_horizon_days` (default 30) controls **reporting**, not detection.

A setup whose base started longer ago than the horizon is still found, still
scored, and still carries `within_horizon: false` — it simply drops to a status
line instead of earning a paragraph. Detection always reads about a year of bars,
because the band-width and ATR percentiles are meaningless without one.

## Tuning

Every threshold lives in one place — `Thresholds` in `stock_desk/models.py` —
rather than scattered as module constants, on the theory that a threshold nobody
can find is a threshold nobody revises after a run of false positives.

Per-ticker overrides for the horizon and the liquidity floor go in
`watchlist.json`. Everything else is global and changing it is a code edit,
deliberately: these are not knobs to turn per morning.
