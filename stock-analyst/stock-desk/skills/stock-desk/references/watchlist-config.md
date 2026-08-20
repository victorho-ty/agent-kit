# Watchlist configuration

Lives at `stock_desk/config/watchlist.json`, or wherever `STOCK_DESK_CONFIG`
points. Adding a ticker, changing a horizon or correcting a peer set is a config
edit, never a code change.

Prefer the `stockctl watch` commands over hand-editing: they validate, they
rewrite the file atomically, and they reject an unknown field rather than
silently dropping it.

```json
{
  "timezone": "Asia/Hong_Kong",
  "report": {
    "frequency": "daily",
    "minutes_before_open": 30,
    "cluster_threshold": 0.6,
    "news_lookback_days": 3,
    "event_horizon_days": 10
  },
  "defaults": {
    "analysis_types": ["technical", "competitor"],
    "technical_horizon_days": 30,
    "min_avg_dollar_volume": 5000000
  },
  "tickers": [
    {
      "ticker": "NVDA",
      "company_name": "NVIDIA",
      "enabled": true,
      "analysis_types": ["technical", "competitor"],
      "technical_horizon_days": 30,
      "competitors": ["AMD", "AVGO", "MRVL"],
      "sector_keywords": ["AI accelerator", "data center GPU"],
      "notes": ""
    }
  ]
}
```

## Ticker fields

**`ticker`** — as Google and Yahoo Finance spell it. Unsuffixed is a US listing;
`.HK`, `.SS`, `.SZ`, `.T` and `.L` select their markets. The suffix is how the
right exchange calendar and report time are chosen, so it is not cosmetic.

**`company_name`** — becomes the news query, and it matters more than it looks. A
bare symbol is a terrible search: `T`, `ALL` and `KEY` are real tickers, and
`0700.HK` matches nothing a journalist ever typed. Without it the tools fall back
to the symbol and the results get noticeably worse.

**`enabled`** — pause with `false` rather than deleting, so the news dedupe
memory and setup history survive. A removed ticker that comes back re-reports its
entire back catalogue.

**`analysis_types`** — any of `technical`, `competitor`. Dropping `competitor`
stops the news queries for that ticker and its peers; dropping `technical` keeps
it in the news sweep but out of the setup scan.

**`technical_horizon_days`** — how *fresh* a setup must be to earn a paragraph.
A base older than this still gets detected and scored, and drops to a status
line. **It does not limit how much history the detector reads**, which is always
about a year — the band-width and ATR percentiles are meaningless without one.

**`competitors`** — the peer set, declared once. Each becomes its own news query,
and anything found is filed under the *watched* ticker with `about_competitor`
naming the peer. That is what makes "AMD launches a rival part" appear in NVDA's
section.

Declared and not derived, deliberately: deriving a peer set from a sector
classification each morning would cost a model call per ticker per day to answer
a question whose answer changes about once a year. Propose the set when adding
the ticker, confirm it, write it down.

Three to five peers is the useful range. Ten produces a news section nobody
reads.

**`sector_keywords`** — free-text queries for themes no single competitor covers:
`"EUV lithography"`, `"China internet regulation"`. Filed under the ticker with
no `about_competitor`.

**`min_avg_dollar_volume`** — per-ticker override of the liquidity floor. Rarely
needed; the default $5M/day suits most things worth swing trading.

## Report settings

**`minutes_before_open`** — how far ahead of the open the report is due. The open
comes from the market's own calendar, so holidays and half-days are handled and a
US+HK watchlist has two report times. Run `stockctl schedule` to see them, and
re-run after a DST change — New York moves and Hong Kong does not.

**`event_horizon_days`** — how far ahead earnings and ex-dividend dates are
announced. Default 10.

**`cluster_threshold`** — title-overlap fraction above which two headlines are
one story. Default 0.6.

Raise it toward 0.8 if genuinely different stories are being merged; lower it
toward 0.5 if the same event keeps arriving four times under four bylines. The
comparison is an overlap coefficient over stopword-stripped tokens, so it is
insensitive to length — "Nvidia beats" and "Nvidia beats estimates, guides
higher" collapse correctly.

**`max_stories`** — ceiling on stories per message, default 12. Anything over it
is reported as `fresh_news_held` and stays pending for the next run. The cap is
applied *before* stamping, so held stories are never silently swallowed.

**`news_lookback_days`** — how far back an on-demand `brief` looks for context.
Does not affect alerting, which is driven by `notified_at` and not by any window.

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
