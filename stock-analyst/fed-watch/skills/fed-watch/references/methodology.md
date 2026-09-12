# The calculation, and what it is worth

Open this when a number needs defending, when a reading looks wrong, or when
deciding how much weight a particular meeting's figure carries.

## What a ZQ price is

A 30-Day Fed Funds future (`ZQ`) settles to the **arithmetic mean of the daily
effective federal funds rate across its whole delivery month**. So:

```
implied month average = 100 - price
```

That is a month average. It is never the rate on a particular day, and it is
never a probability. Everything below exists to get from it to the rate the
market expects *after* one specific decision.

## Getting from a month average to a post-meeting rate

A new target range is effective the **day after** the announcement. So a month
of `n` days holding a decision on day `d` runs `d` days at the old rate and
`n - d` at the new one:

```
average = (d/n) * r_start + ((n-d)/n) * r_end
```

Two ways to solve for `r_end`, and the bundle prefers the second wherever the
calendar allows it.

### `blend_inversion`

Invert the equation directly:

```
r_end = (average - (d/n) * r_start) / ((n-d)/n)
```

Dividing by `(n-d)/n` amplifies any error in the price by `n/(n-d)`. That factor
is reported as `amplification`:

| meeting | days at new rate | amplification |
|---|---|---|
| 16 Sep 2026 (30-day month) | 14 | 2.1 |
| 28 Oct 2026 (31-day month) | 3 | **10.3** |

At 10x, a tick of price error is a tenth of a rate move. The figure is not worth
reporting without saying so.

### `clean_next_month`

When the **following month holds no meeting**, its contract runs at a single
constant rate for its whole length. So `100 - price` *is* the post-meeting rate:
no inversion, no amplification, nothing solved.

November 2026 has no FOMC meeting, which is why the 28 October decision — the
worst case for inversion — is read cleanly off the November contract instead.

This is why `config/fomc.json` must be complete and not merely cover the
meetings being priced. A missing date makes a month look clear when it is not,
and the result is a confidently wrong number rather than an error.

## Turning a rate move into probabilities

The FOMC moves in 25bp increments. A move of `m` percentage points is spread
across the two adjacent increments bracketing it:

```
steps = m / 0.25
lower = floor(steps)
P(lower) = 1 - (steps - lower)
P(lower + 1) = steps - lower
```

Cuts fall out of the same expression: `m = -0.15` gives `steps = -0.6`,
`lower = -1`, so a **60% chance of a 25bp cut** and 40% of no change. Check it
against the expectation: `0.6 * -0.25 = -0.15`.

The second meeting convolves a further increment onto the first meeting's
distribution, which is why its table spans three bands where the first spans
two.

## Why the effective rate, not the midpoint of the range

`r_start` is the **published effective federal funds rate**, fetched from the
New York Fed. It is not the midpoint of the target range, and substituting one
for the other is the single largest error available here.

Validated against CME's own published table for 16 September 2026, using CME's
own mid price of 96.2688:

| `r_start` | no change | +25bp |
|---|---|---|
| 3.625% — midpoint of the 3.50–3.75% range | 9.0% | 91.0% |
| 3.63% — published EFFR | **13.3%** | **86.7%** |
| **CME FedWatch published** | **13.3%** | **86.7%** |

Half a basis point of `r_start` is four percentage points of probability. Both
rows of this table are pinned by tests.

## What the residual error is

Given CME's price, the method is exact. The bundle reads Yahoo's **last
one-minute print** rather than CME's **settlement mid**, and that is worth
roughly one percentage point:

| price for ZQU26 | no change | +25bp |
|---|---|---|
| 96.2688 — CME mid | 13.3% | 86.7% |
| 96.2700 — Yahoo 1m last print (what the bundle reads) | 14.3% | 85.7% |

The gap is the price source and nothing else. This is why `price_source` travels
on every payload and why the figures must never be attributed to CME.

## The modelling simplification

For the second meeting, the increment the market prices is applied uniformly
across the first meeting's branches rather than solved conditionally for each
one. Over a two-meeting horizon with a single 25bp step in play the difference
is immaterial. It would stop being immaterial over a longer horizon or in a
regime where 50bp moves are live, and the honest fix then is a full conditional
tree.

## What this does not model

- **Intermeeting moves.** The Fed can act between scheduled meetings. The whole
  framework assumes it does not.
- **A non-25bp increment.** A 10bp technical adjustment would be spread across
  25bp buckets and read as a partial probability of a full move.
- **The EFFR drifting inside the range.** `r_start` is today's print, held
  constant until the decision. In practice it wanders a basis point or two.
- **Anything about whether the market is right.** These are prices, not
  forecasts.

## Sources

- Methodology: <https://www.cmegroup.com/articles/2023/understanding-the-cme-group-fedwatch-tool-methodology.html>
- Effective rate and target range: <https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json>
- FOMC calendar: <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>
