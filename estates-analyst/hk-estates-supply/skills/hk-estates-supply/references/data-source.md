# The source, the figures, and how they are read

## Where the numbers come from

The Housing Bureau publishes 私人住宅一手市場供應 (private residential
primary-market supply) once a quarter, as a single PDF linked from one page:

- index: `https://www.hb.gov.hk/tc/publications/housing/private/pshpm/index.html`
- PDF: `https://www.hb.gov.hk/tc/publications/housing/private/pshpm/stat<YYYYMM>.pdf`

The index page carries exactly one live link — the current quarter — plus a
link to an archive page for older ones. `<YYYYMM>` is the quarter end: `202606`
is the quarter ending June 2026, which this bundle labels `2026/Jun`. Only
`03`, `06`, `09` and `12` are valid; anything else is refused rather than guessed
at.

Beside the link the page prints its own wording, e.g. `2026年6月`. When both are
present and they disagree, the tools stop with `ERR_PARSE` instead of filing the
figures under a label that might be wrong. A missed quarter is recoverable; a
quarter recorded under the wrong label is believed.

Publication has run roughly two months after the quarter end. `overdue` turns
true 100 days after the *next* quarter's end, which is late enough not to cry
wolf and early enough to notice a series that quietly stopped.

## The three figures

They sit on **page 2**, each printed to the right of its label, comma-formatted
with a 伙 suffix, rounded to the nearest thousand:

| key | label on the page | 短名 | what it counts |
|---|---|---|---|
| `land_ready` | 已批出土地上可隨時動工的單位數目 | 可隨時動工 | units on disposed land that could start construction at any time |
| `being_built` | 建築中的單位數目，減去已預售單位數目 | 建築中未售 | units under construction, less those already pre-sold |
| `built_not_sold` | 已落成但未售出的單位數目 | 現樓貨尾 | units completed but not yet sold |

`total` is the figure the page describes in prose as the supply for 未來三至四年間.

**It is the Bureau's printed total, not the sum of the three components**, and
the two are not always the same number: each component is rounded to the nearest
thousand independently and so is the total, so they can differ by a thousand or
two. Four of the eighteen inherited rows do. Storing the sum instead would make
the Total column mean one thing for rows recorded before this bundle existed and
another for rows recorded after it, which is the kind of discontinuity nobody
finds until they are chasing a figure that will not reconcile.

The sum is still computed, as a cross-check. When it and the printed total
disagree, `total_matches_printed` is `false` and both numbers are in the payload.
That is **not** an error — a gap of a thousand or two is the rounding. A gap of
tens of thousands is a figure read off the wrong row, and beyond
`report.TOTAL_TOLERANCE` (3,000) the sum is stored instead, on the grounds that a
wildly wrong printed total means the parse is what went wrong.

## Why the extractor is not three regexes

A label and its figure are typeset on the same visual row, but their baselines
differ by a couple of points. Reading-order text extraction therefore emits the
number **before** the label on some rows and **after** it on others — and it is
not consistent between issues.

Any regex anchored to reading order gets two of the three rows right and picks up
a neighbouring row's number for the third. Nothing about the result looks wrong:
every figure on the page is a five-digit round number, so a misread lands in
exactly the range a correct read would.

So words are grouped into visual rows by vertical centre (8pt tolerance; rows are
about 28pt apart), each row is ordered left to right, and a figure is taken as
the first number lying to the right of the end of its label. That is how a person
reads the page, and it is stable against both the baseline wobble and a section
number printed to the left of the label.

The 建築中 label is matched on its prefix, because the page appends
「，減去已預售單位數目」 to it.

## The history file

`data/hk_units_supply_history.csv`, five columns, newest first:

```
Quarter,LandReady,BeingBuilt,BuiltNotSold,Total
2026/Jun,16000,61000,19000,96000
2026/Mar,19000,62000,20000,101000
```

**Columns are read positionally; the header is never trusted.** The file
inherited by this bundle had its Chinese headers written through a console that
could not encode them and arrived as `Quarter,?????,???/?????,????,Total`. The
numbers underneath were intact. Reading by position means a mangled header costs
nothing, and the header is rewritten on every append, so it heals itself.

This file is the durable artefact. Once the Bureau moves a PDF into its archive,
the figures for that quarter exist here and nowhere else convenient. It is
rewritten atomically through a temporary file, in UTF-8 with LF endings, and a
quarter that is already present is refused rather than overwritten — published
figures do not change, so a second arrival wants a person, not a silent update.

## QoQ, and what the rounding does to it

`pct` compares a quarter against the **calendar-preceding** quarter, not against
the previous row in the file. If a quarter were missing, its successor gets no
percentage at all (`basis: "unavailable"`) rather than a six-month change printed
under a QoQ heading.

Because every figure is rounded to the nearest thousand at source, the smallest
move the data can express is one thousand units — 6.25% on a 16,000 base, 1.6% on
a 61,000 one. A QoQ percentage here measures the change in the published rounded
figures. It is not a measurement of the market to two decimal places, and two
quarters that differ by 6% may differ by very little.

## Colour

In the table image, a QoQ cell is green when the figure is **higher** than the
prior quarter and red when it is **lower**. Grey with an em dash means there was
nothing to compare against; grey with `+0.00%` means genuinely unchanged.

The colour is chosen from `direction`, which comes from the raw delta — never
from the sign of the rounded percentage, so a cell can never be green above a
number that fell.

**No judgement is encoded.** Higher 現樓貨尾 (unsold completed stock) and higher
可隨時動工 (land ready to start) are different facts about different parts of the
pipeline, and this bundle takes no view on either.

## The charts

**Both trend charts window on the data, not on zero** — `min` less a tenth of the
span to `max` plus a tenth, floored at zero. These figures sit in the tens of
thousands and move a thousand at a time, so a zero-based axis puts the whole line
in a flat band at the top of the frame, unreadable on a phone.

The cost of a cropped axis is that a small percentage move looks large. **That is
why the height of the line is never the source of anything you say.** The exact
size of every move is in the table, to two decimal places, with its direction
coloured; the chart is there for shape and for where the level sits in its own
recent range.

Grid lines are dotted so they stay a guide rather than competing with the series
at phone size.

X labels are thinned to at most fourteen, counting back from the newest quarter
so the one the report is about always keeps its label. Four quarters a year means
ten years of history is forty labels, which at 45 degrees overlap into a grey
band well before that.

The end label is the last value, drawn to the right of the final marker. It was
above it until a quarter arrived that was the series maximum and the label
collided with the chart title.
