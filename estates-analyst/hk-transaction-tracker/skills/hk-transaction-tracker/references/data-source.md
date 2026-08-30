# The source: Centanet 成交 lists

## What the URL is

A Centanet transaction list for one estate, phase or block:

```
https://hk.centanet.com/findproperty/list/transaction/泓都_2-SSPPWPPYPS?q=8prsheylr1o5h
https://hk.centanet.com/findproperty/list/transaction/港島南岸-3B期-Blue-Coast_2-SDPPWPPJAB@P?bigestate=3-SDPPWPPJPB
```

The Chinese is percent-encoded in practice; either form works. The `q=` is an
opaque saved-search key and the `bigestate=` scopes a phase to its development —
both are passed through untouched, which is why the fetch layer appends `size`
textually rather than round-tripping the query through an encoder.

The page must be a `/list/transaction/` URL. A `/list/buy/` or `/list/rent/` URL
is a *listing* page — flats on offer, not deals done — and carries no
`transactionList` at all. The config refuses those rather than letting them
present as ERR_PARSE on every run.

## The page is server-rendered, so there is nothing to scrape

The obvious approach is the wrong one here. The visible 成交 table is built by
Vue in the browser, so **CSS selectors against the served HTML match nothing** —
there are no `<tr>` elements to find. What the response does contain, near the
end of the document, is the complete data the table will be built from:

```html
<script>window.__NUXT__=(function(a,b,c,…){ci[0]=E;return {state:{…}}}(false,true,0,…))</script>
```

Every transaction on the page is already in that payload, with its saleable
area, its unit price and its sale/rental flag separated into fields. So one
plain HTTP GET plus a decoder is the whole fetch path: no browser, no
JavaScript engine, no headless Chromium being paid to hand back data the first
response already contained.

### How the payload is minified

It is a function of a few hundred single-letter parameters called with the
literal values, so `count:c` means `count:0` and the same string appears once no
matter how often it is used. Decoding is: read the parameter names, read the
argument literals, evaluate the returned object literal against that symbol
table. `hk_transaction_tracker/nuxt.py` does exactly that — a JSON parser with
three extra cases (identifiers, `void 0`, `Array(n)`). Nothing from the page is
ever passed to `eval`.

Two details that are easy to get wrong and are covered by tests:

- **A prelude of assignments runs before the `return`.** `ci[0]=E;` patches an
  argument that was passed in as a hole, which is how the minifier expresses a
  value referenced from two places. Skipping the prelude yields a payload that
  parses but is quietly incomplete.
- **Bracket matching must skip string literals.** Estate names contain
  parentheses — `2座 (2A)` — and counting brackets naively closes the function
  early and takes the argument list with it.

If Centanet changes build tooling the symbol table stops resolving, and that
surfaces as `ERR_PARSE` naming the unknown identifier rather than as silence.
That is the failure worth reporting.

## The hundred-record ceiling

The list renders 24 records by default and honours a `size` query parameter.
Probed against the live site:

| request | result |
|---|---|
| `&size=100` | 100 records |
| `&size=101` and above | HTTP 200, a valid page, **and an empty `transactionList`** |
| `&offset=`, `&page=`, `&skip=`, `&start=`, `&pageIndex=`, `&currentPage=` | all ignored; still offset 0 |
| `&day=Day30` | parsed as a list and yields nothing; leave the default alone |
| `&sort=`/`&order=` | ignored; the list is always newest first by 成交日期 |

So **the newest 100 records is the entire visible window and there is no way to
page behind it.** Two consequences the whole design turns on:

1. `fetch_size` is clamped at 100 in both the settings layer and the config
   validator, because the failure above 100 is silent — an empty list is
   indistinguishable from a quiet estate. For the same reason
   `extract.extract` treats an empty `transactionList` as `ERR_PARSE`.
2. The SQLite archive is the only record of anything older. A first check seeds
   5–12 months depending on how briskly the block trades; everything before that
   is unrecoverable, and everything after it survives only because it was stored.

At Centanet's default 3-year window, 100 records covers roughly a year on 泓都
(286 transactions in 3 years) and about 5 months on 港島南岸 3B期 Blue Coast
(762).

## The fields that are read

From `state.transaction.transactionList.data[]`:

| field | used as | notes |
|---|---|---|
| `id` | `tx_id` | Centanet's own; unique within an estate. The dedupe key. |
| `postType` | `deal_type` | **`S` = 買賣, `R` = 租賃.** The only thing separating them. |
| `transTheme` | filter | `Post` is a home. `CarPark` is dropped. |
| `transactionPrice` | `price` | 成交價 on a sale; the **monthly rent** on a rental. |
| `nArea` | `saleable_area` | 面積(實). Often `null` on land-registry sale rows. |
| `nUnitPrice` | `saleable_unit_price` | 呎價(實) on a sale; **呎租(實)** on a rental. |
| `gArea` / `gUnitPrice` | recorded only | 建築面積. Never averaged, never mixed with saleable. |
| `bedroomCount` | `bedrooms` | 間隔. `0` is 開放式; `null` on a car park. |
| `insDate` | `ins_date` | 成交日期. **Present on both sides** — the date everything is ordered and bucketed by. |
| `regDate` | `reg_date` | 登記日期. Land Registry only, so `null` on every rental. |
| `estateName`, `buildingName`, `yAxis`, `xAxis` | the unit label | 屋苑 / 座 / 樓層 / 室. |
| `dataSource` | recorded | `Land` (土地註冊處) or `AC` (中原集團). |
| `detailUrl` | recorded | the deal's own page. |

`0` and `null` are used interchangeably for "not published", so a zero area or
zero unit price is read as missing rather than as a very small number.

## 買賣 and 租賃 arrive together

`transactionSearch.postType` is `Both` on every one of these URLs. The site's
買賣/租賃 control filters client-side; the served payload always contains both,
interleaved and ordered by `insDate`. There is no separate rental URL to
configure and no filter to set — the split is made per row on `postType`, and
every query in the package is scoped by it.

The consequence worth repeating: `nUnitPrice` is $24,458 on a sale row and $57
on a rental row two lines below it. Any average taken across the two is
meaningless, which is why nothing in this package computes one.

## Rows without an area

Roughly a quarter of sale rows — and most sale rows on a new development, where
the Land Registry has the deed before Centanet has matched the unit — arrive
with a price, an address and `nArea: null`. They have no 呎價(實) and cannot be
put in a size band.

They are stored, matched on their other dimension, reported in a 面積待補 group
with an em dash for area and 呎價, and excluded from every median, percentage
and chart. Neither dropping them (real deals in the tracked block would vanish)
nor bucketing them (inventing a number) is acceptable.

## Being a good citizen

Three requests per run, one per estate, with a configurable delay between them
and a browser `User-Agent` because the default Python one gets a challenge page.
Nothing here evades a rate limit, solves a challenge, or reads anything behind a
login. Keep `request_delay_seconds` at 1 or above, leave `fetch_size` at 100,
and never loop `check` to make something appear.
