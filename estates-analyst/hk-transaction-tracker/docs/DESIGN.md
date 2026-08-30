# Design notes

The decisions that were not obvious, and what each one is defending against.

## 1. Read the payload, not the page

The Centanet 成交 list is a Nuxt page. The visible table is built by Vue in the
browser, so CSS selectors against the served HTML match nothing — but the
response already contains every transaction, in a `window.__NUXT__` assignment
near the end of the document, with saleable area, unit price and the sale/rental
flag already in separate fields.

So the fetch path is one HTTP GET and a decoder. The alternative — Playwright,
a Chromium start per estate, a wait for hydration, then selectors against
generated markup — would be slower, far more fragile, and would be paying a
browser to hand back data the first response contained.

The decoder is a JSON parser with three extra cases (identifiers resolved
against the minifier's symbol table, `void 0`, `Array(n)`). Nothing from the
page reaches `eval`. Two subtleties are load-bearing and tested: a prelude of
assignments runs before the `return` and patches shared references, and bracket
matching has to skip string literals because estate names contain parentheses.

**Failure mode defended against:** a build-tooling change at Centanet surfaces as
`ERR_PARSE` naming the unresolvable identifier, not as a partial payload or as
silence.

## 2. The hundred-record ceiling is the shape of everything else

Probing the live site: `&size=100` works, `&size=101` returns HTTP 200 with an
*empty* list, and no offset/page/skip parameter is honoured. The newest hundred
is the entire visible window and there is no way to page behind it.

Three consequences:

- `fetch_size` is clamped at 100 in the settings layer **and** refused above 100
  by the config validator, because the failure is silent.
- An empty `transactionList` is `ERR_PARSE`, not "no transactions". Those two
  are indistinguishable otherwise, and one of them hides for ever.
- The SQLite archive is the only memory of anything older, so nothing in the
  package deletes or rewrites a row. A first check seeds 5–12 months depending
  on how briskly the block trades; everything before that is unrecoverable.

## 3. Seeding is silent

An estate's first check absorbs its whole back catalogue as already-reported. A
year of history announced because the tracker was installed today would bury the
one deal that mattered, and the person would mute the channel.

The corollary is that a *failed* first check must not mark the estate seeded —
otherwise the next good run announces the back catalogue. Tested.

## 4. Unmatched transactions are stored anyway

The criteria decide what is **announced**. The archive holds everything
residential, because the trend is estate-wide.

A median over the two or three deals a quarter that match "2房, 500–700呎" is
noise wearing a percentage sign. A median over every flat in the block is a
market level. Storing only the matches would make the estate-wide trend
unrecoverable after the fact, and it costs nothing to keep the rest.

## 5. A missing dimension cannot reject a transaction

About a quarter of sale rows — most of them on a new development — arrive from
the Land Registry with a price, an address and no `nArea`. Three options, and
two of them are wrong:

- *Reject them for failing the size band.* Real sales in the tracked block
  vanish silently.
- *Put them in a band anyway.* That is inventing a number.
- **Skip the dimension, require the other one to pass, flag the row.** They are
  reported in a 面積待補 group with an em dash for area and 呎價, and excluded
  from every median, percentage and chart.

The floor under this is that at least one configured dimension must actually
have been checked and passed, so a row with neither 間隔 nor 面積 matches
nothing. Absence never matches by default.

## 6. 買賣 and 租賃 are separate everywhere

`nUnitPrice` is $24,458 on a sale row and $57 on a rental row two lines below
it — the same field, two different quantities, in one list. `postType` (`S`/`R`)
is the only thing separating them, and the site's own 買賣/租賃 control merely
filters client-side.

Every query, median, chart, table and summary line in the package is scoped by
`deal_type`, and the column headings are chosen per side (`成交價` vs `月租`,
`呎價(實)` vs `呎租(實)`) so that no rendered artefact can be read as the other
thing.

## 7. Medians, 呎價 only, and a named basis

- **Median, not mean.** Four transactions a month, and one penthouse moves a
  mean by ten per cent.
- **呎價(實), never 成交價.** A quarter that transacted larger flats shows a
  rising 成交價 in a falling market.
- **Saleable, never gross.** The two bases differ by roughly a quarter; mixing
  them manufactures a trend on its own. Gross figures are recorded and never
  averaged.
- **`basis` is a field.** `insufficient` (below `min_samples` in either window)
  and `no_data` are reported as themselves, with `pct: null`, because "too few
  deals to say" and "prices did not move" are different answers and only one of
  them is ever true.

## 8. The truncated first month is dropped from charts

The archive begins on whatever day the first check ran, so the oldest month is a
partial sample of that month. Left in, it sits at the left-hand end of every
chart for ever, usually below the rest, and reads as the start of a rise that
never happened. It is excluded from the series and reported as
`partial_first_month` so the fact is available rather than hidden.

## 9. Hand-drawn tables

The grouping *is* the content: 買賣 → 屋苑 → 間隔 → 面積 is four levels, and a
section heading has to span the full width to say which bucket the rows below it
belong to. `matplotlib`'s `axes.table` cannot merge cells, so a heading would be
clipped into the first column. The table is drawn as rectangles and text in inch
coordinates instead — about eighty lines, and it gives fixed row heights and
identical type sizes whether there are three rows or thirty.

買賣 and 租賃 are separate images rather than columns of one, because a wide
table that has to be pinch-zoomed is a table nobody reads.

## 10. Finished strings, not structured data to narrate

`summary_lines` are complete, formatted, relay-verbatim sentences — 萬/億 for
prices, `/月` for rents, `/呎` for unit prices, the sample size inside every
trend line. The model sends them.

That is the token argument for the whole bundle: nine new transactions across
three estates cost nine formatted lines, not nine paragraphs of a model deciding
again how to write a price. It is also a correctness argument — a number that
was never re-typed was never re-rounded.

## 11. Read-only by construction

There is no command that edits or deletes a transaction, an estate or the
archive. `check` appends, `report --commit` stamps a delivery ledger, and
everything else reads. Asking a question in chat can never consume a pending
summary, which is why `history`, `trend` and `transactions` exist separately
from `report`.
