# Watchlist configuration

Lives at `stock_desk/config/watchlist.json`, or wherever `STOCK_DESK_CONFIG`
points. Adding a ticker, changing a horizon or correcting a peer set is a config
edit, never a code change.

Prefer the `stockctl watch` commands over hand-editing: they validate, they
rewrite the file atomically, and they reject an unknown field rather than
silently dropping it.

**The file states only what is unusual.** Anything absent falls back to
`defaults`, and `save()` omits any value equal to its default, so a ticker that
behaves normally is one line. That makes the fallback path the common path,
which is worth knowing when debugging: most fields most tickers use are not
written down anywhere.

```json
{
  "timezone": "Asia/Hong_Kong",
  "report": {
    "minutes_before_open": 30,
    "cluster_threshold": 0.6,
    "event_horizon_days": 10,
    "max_stories": 12
  },
  "defaults": {
    "analysis_types": ["technical", "competitor"],
    "technical_horizon_days": 30,
    "min_avg_dollar_volume": 5000000
  },
  "tickers": [
    { "ticker": "NVDA", "company_name": "NVIDIA Corporation", "competitors": ["CBRS", "AMD"] }
  ],
  "sectors": [
    { "name": "AI infrastructure", "members": ["NVDA", "CBRS", "AMD"] }
  ],
  "macro": { "enabled": true }
}
```

Every field below drives a decision somewhere. Fields that did not — 
`sector_keywords`, `notes`, `news_lookback_days` and a sector `proxy` — were
removed rather than left in place looking meaningful. A config file that lists
knobs connected to nothing teaches the reader to distrust all of them. Old files
containing them still load; the keys are ignored.

## Ticker fields

**`ticker`** — as Google and Yahoo Finance spell it. Unsuffixed is a US listing;
`.HK`, `.SS`, `.SZ`, `.T` and `.L` select their markets. The suffix chooses the
exchange calendar and report time, so it is not cosmetic. Upper-cased on load.

**`company_name`** — the most load-bearing field in the file, and the least
obviously so. It becomes the **alias set** that decides whether a headline is
about this company at all: `"NVIDIA Corporation"` yields `nvda`, `nvidia
corporation` and `nvidia`, and a story naming none of those is filed elsewhere
or dropped.

Get it wrong and the ticker silently goes quiet — no error, just an empty
section. Two traps:

- **Spell it the way journalists do.** `"Space X"` does not match a headline
  reading "SpaceX", because the alias is matched on word boundaries. `"SpaceX"`
  matches both the one-word form and, via the `$SPCX` and `(SPCX)` forms, the
  legal name.
- **Numeric HK symbols need it.** `0700` is never used as an alias — it matches
  a year, a price or a time as readily as it matches Tencent. Such a listing is
  found by its name or by the explicit `$SYM` / `(SYM)` forms, so an HK ticker
  without a `company_name` is close to unfindable.

Symbols under three characters are not aliases either, for the same reason: `T`,
`ALL` and `KEY` are real tickers and would match inside ordinary words.

**`enabled`** — omit for true. Pause with `false` rather than deleting, so the
news dedupe memory and setup history survive. A removed ticker that comes back
re-reports its entire back catalogue.

**`analysis_types`** — any of `technical`, `competitor`. Dropping `competitor`
stops the news poll for that ticker and its peers; dropping `technical` keeps it
in the news sweep but out of the setup scan. Omit to inherit both.

**`technical_horizon_days`** — how *fresh* a setup must be to earn a paragraph.
A base older than this is still detected and scored, and drops to a status line.
**It does not limit how much history the detector reads**, which is always about
a year — the band-width and ATR percentiles are meaningless without one.

**`competitors`** — the peer set, declared once. Each becomes its own Yahoo news
feed, and anything found is filed under the *watched* ticker with `peer_of`
naming the peer. That is what makes "AMD launches a rival part" appear in NVDA's
section.

**These must be tickers, not company names.** `"NVIDIA"` is not a symbol: the
feed returns nothing, and — because an unknown symbol is an empty result rather
than an error — nothing is reported either. The failure is completely silent.

Declared and not derived, deliberately: deriving a peer set from a sector
classification each morning would cost a model call per ticker per day to answer
a question whose answer changes about once a year. Three to five peers is the
useful range.

A peer story reaches **every** entry that declared that peer — news dedupe is
`UNIQUE (ticker, url_hash)`, scoped to the ticker. A global constraint gave one
AMD story to whichever entry was polled first, and NVDA lost its entire AMD feed
to CBRS.

**`min_avg_dollar_volume`** — per-ticker override of the liquidity floor. Rarely
needed; the default $5M/day suits most things worth swing trading.

## Sectors

A top-level block, because a sector is a group of *listings* and not a property
of any one ticker.

**`members`** — tickers, at least two. This is what separates a sector from a
keyword: the question sector analysis answers is comparative — did this name move
with its group or against it — and that needs prices, which keywords do not have.
A one-member sector is rejected at load rather than reported as "in line with its
group", which would be a statement about nothing.

There is no proxy or index field. One existed briefly and earned nothing: for
groups this size the comparison that matters is against the group's own median,
and a proxy ticker that is not itself on the watchlist has no cached bars, so
the reading was permanently null.

A ticker may sit in more than one sector. Its news then appears under both, on
purpose: the two sections answer different questions, and dropping it from one
to avoid repetition would make that sector look quiet when it is not.

Three readings come out of it:

- **standing** — `leading`, `in_line` or `lagging` the group median, with the
  gap in percentage points.
- **cohesion** — `bloc`, `mixed` or `scattered`, from the dispersion of member
  returns. A bloc is a theme or a macro input acting on all of them.
- **breadth** — how many members are up. One name up 30% beside five flat ones
  is not a sector that is working, whatever its median says.

`carried: true` is the field worth reading — the group moved together and this
name went with it, so the move is the sector's rather than the company's, and a
breakout on it deserves less conviction than the chart alone suggests.

Members are **not** priced unless they are also synced. A member with no cached
bars is named in `missing` rather than dropped, so a three-member sector
reporting on two says so.

## Macro

```json
"macro": { "enabled": true, "moves": { "ust_10y": 0.25 } }
```

Which series are tracked is not configurable — that set is revised about never.
What is configurable is the threshold at which one is worth mentioning, which is
exactly the knob wanted after a fortnight of being told about two-basis-point
drifts.

**`moves`** — per-series override, in the series' **own units**, not as a
percentage of the level. `0.25` on `ust_10y` means 25 basis points. Absolute and
not proportional, deliberately: ten basis points on the 10-year is the same
event whether the yield is 2% or 5%.

Keys: `ust_2y`, `ust_10y`, `ust_30y`, `fed_funds`, `cpi`, `unemployment`.

**Nothing is reported until it has moved.** A reading is compared against the
last *reported* level, not against yesterday — three consecutive four-basis-point
days are a twelve-basis-point move, and a day-over-day comparison reports none of
them. A series never reported before is never pending: the first sight of a level
is a starting point, not an event.

Each series declares how stale it may get before it is worth an Alpha Vantage
call — six hours for the yields, twenty-four for the monthly figures. That
matters because the free tier's 25 calls a day are shared with the news poller.

## Report settings

**`minutes_before_open`** — how far ahead of the open the report is due. The open
comes from the market's own calendar, so holidays and half-days are handled and a
US+HK watchlist has two report times. Run `stockctl schedule` to see them, and
re-run after a DST change — New York moves and Hong Kong does not.

**`event_horizon_days`** — how far ahead earnings and ex-dividend dates are
announced. Default 10.

**`cluster_threshold`** — title-overlap fraction above which two headlines are
one story. Default 0.6. Raise toward 0.8 if genuinely different stories are being
merged; lower toward 0.5 if the same event keeps arriving four times under four
bylines. The comparison is an overlap coefficient over stopword-stripped tokens,
so it is insensitive to length.

**`max_stories`** — ceiling on stories per message, default 12. Anything over it
is reported as `fresh_news_held` and stays pending for the next run. The cap is
applied *before* stamping, so held stories are never silently swallowed.

## Markets

Currency is stated on every figure and never summed across positions. HK issuers
frequently report in RMB while trading in HKD; reporting currency and trading
currency are separate fields.

Board lots are not one share. Position quantities are **share counts** — a lot
size of 100 confused for a quantity misstates a position by a factor of a
hundred.

ADRs, H/A-share pairs and dual-primary listings are one economic entity. Do not
put both sides of a pair on the watchlist: they will produce two setups and two
news sections for one company.
