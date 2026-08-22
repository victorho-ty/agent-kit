# Why it is shaped this way

The bundle replaces a single script (`hk_units_supply.py`) that scraped the
Housing Bureau, appended a CSV row and e-mailed an HTML table with two charts
attached. Everything below is either a thing that script got right and this kept,
or a thing that changed because the report now goes to a phone and the runner is
now an agent.

## What the script got right, and this kept

**Rows by vertical centre, not by reading order.** The whole reason `pdfplumber`
is here. See `skills/hk-estates-supply/references/data-source.md`; this is the
one piece of logic that would be very hard to rediscover and very easy to break.

**A quarter's identity is the PDF filename.** `stat202606.pdf` is
unambiguous, ASCII, and set by the publisher. Deriving the quarter from the
publication date, or from text inside the PDF, would depend on things that vary.

**The CSV is newest-first and read positionally.** Both inherited, both kept —
the ordering because it is what a person opening the file wants, the positional
read because the file arrived with its Chinese headers mangled by a console that
could not encode them, and the numbers were fine.

## What changed, and why

### One script split into detect and report

The script did everything in one `__main__`: fetch, parse, append, render, send.
That is the right shape for cron-plus-SMTP and the wrong shape for an agent,
because it fuses two decisions with completely different costs. Checking the
index page is cheap and happens daily; rendering and sending is expensive and
happens four times a year.

So `check` detects and writes, `report` renders and returns, and they are joined
by a ledger file rather than by being the same function call. That buys three
things the script could not have:

- the cron entry is a plain command, so 361 days a year cost no tokens;
- a send that failed is still pending tomorrow, with no retry logic anywhere;
- somebody can ask for the current report between quarters without consuming the
  quarter's own alert (that is what `--commit` is for).

### `--force` became `--commit`

The script had `--force` to re-send an already-processed quarter. Inverting the
default is better: a report is rendered whenever asked, and only `--commit`
changes state. The dangerous operation is now the one you have to type, and the
common case — "show me the current picture" — is the safe one.

### The HTML table became a PNG

The report goes to Telegram now, which renders no HTML. `pretty_html_table` and
the `<img src="cid:…">` body have no meaning there.

Drawing the table with matplotlib rather than converting HTML keeps the
dependency list at three and makes the colouring exact — each cell's wash and
text colour is set from `direction`, which is what the operator asked for
(green up, red down) and what an HTML-to-image converter would have made
approximate.

Layout is computed in inches from the row count rather than scaled to fit, so a
four-quarter table and a twelve-quarter table have identical row heights and type
sizes. A report that looks different every quarter is one people stop reading
carefully.

### The chart y-axis frames the data, with a buffer

Both charts window on the series' own range — `min - 10%` of the span to
`max + 10%`, floored at zero — rather than anchoring at the origin.

This was tried the other way first. A zero-based axis cannot overstate a
movement, which is the property a research desk would like to have; but for
figures that sit in the tens of thousands and move by a thousand at a time, it
pushes the whole line into a flat band in the top fifth of the frame. On the
phone these are read on, that is a chart nobody can read, and an unreadable chart
is worse than an honest-but-flattering one because it conveys nothing at all.

So the axis shows the shape, and the overstatement is answered where it can be
answered exactly: **the table beside the chart carries every move as a
percentage, to two decimal places, with its direction coloured.** The skill's
standing rule that the agent never describes a chart — it has a file path, not a
picture — is what keeps the cropped axis from turning into a claim. Magnitude is
never read off the line.

Grid lines are dotted for the same reason the axis is cropped: on a phone a solid
rule competes with the series, and a dotted one stays a guide.

### Colour is decided by `direction`, never by `pct`

`pct` is derived and rounded; `direction` comes from the raw integer delta. If
colour were chosen from the sign of a formatted percentage, a small move could
round to `+0.00%` and still be painted green — a cell whose colour disagrees with
its own number. That is the single defect that would quietly destroy trust in the
whole report, so the two are computed from different things on purpose, and there
is a test that says so.

### No e-mail, and no Telegram

Hermes owns delivery, and the tools return file paths and JSON. It also means the tests need
no network and no credentials.

### pandas and BeautifulSoup went away

Not on principle — because the work does not need them. The history has never
exceeded a few dozen rows, so `csv` and a dataclass are enough and the quarter
arithmetic stays pure stdlib. The index page is 3KB with one link on it, so
`urllib` and a regex are fewer moving parts than an HTTP stack plus an HTML
parser. What is left is `pdfplumber` (irreplaceable), `matplotlib` (the images)
and `tzdata` (data, not code).

The payoff is that the whole test suite runs offline in about two seconds, and
the modules that hold the real logic — `history`, `extract` — import nothing
heavy at all.

## Decisions that are load-bearing

**Seeding.** The first run stamps everything already in the CSV as reported. The
alternative fires eighteen reports on install. Seeding happens *before* a new
quarter is appended, so a quarter published on installation day is still
reported — getting that order backwards swallows the one quarter that mattered
and looks like it worked.

**Refusing to overwrite a quarter.** Published figures do not change. A second
arrival for the same quarter means either this ran twice or the source restated
something, and both deserve a person. Overwriting silently would make a
restatement invisible.

**A report's window ends at its subject.** The table and both charts stop at the
quarter the report is about. Windowing on the newest rows regardless of subject
produced a table headed "— 2023/Mar" whose oldest row was 2023/Sep, with nothing
highlighted and three years of figures the accompanying text never mentioned. The
QoQ inside the window is still computed against the whole file, so a short window
cannot silently delete a comparison that exists.

**The Total column is the publisher's figure, not our sum.** Four of the eighteen
inherited rows differ from their own components by a thousand or two — the source
rounds each part and the total separately. Computing a sum for new rows would
have made one column mean two different things either side of the day this bundle
was installed, and the discontinuity would only surface years later, in the
middle of chasing a number that would not reconcile. Beyond a few rounding steps
apart, the sum wins instead: that far out is a misread row, not rounding.

**Failing on a filename/label mismatch.** When the anchor says `stat202606.pdf`
and the page says `2026年3月`, the tools stop. A missed quarter is recoverable
from the archive; a quarter filed under the wrong label is *believed*, and every
QoQ after it is wrong.

**Splitting `ERR_FETCH` from `ERR_PARSE`.** A site down for an hour is not news.
A page that no longer parses is the failure mode this design fears most, because
it is indistinguishable from "nothing was published" — and this monitor is
legitimately silent for three months at a time, so it could hide for a year.

**The run log.** Cheap, append-only, trimmed to 200 lines, written on failures
too. It is the only thing in the system that can answer "is this still running",
which for a quarterly monitor is a question that otherwise has no answer until
the day somebody notices they have not heard anything since April.

**The history CSV stays in the bundle; everything else does not.** The CSV is the
one artefact that cannot be reconstructed — the Bureau archives old PDFs, and
this file is where those quarters live. It is versioned with the bundle and
written atomically. The ledger, the run log and the PNGs are all disposable, so
they live under the profile state directory and a redeploy never has to merge
them.

**CJK font detection with an English fallback.** A headless Ubuntu box without
`fonts-noto-cjk` would otherwise draw a row of tofu boxes where the column
headings should be. Falling back to English labels and reporting `cjk_font: null`
means a report that lost its Chinese says so, rather than looking broken.

## What is deliberately not here

- **No district breakdown.** Later pages of the PDF carry one. The three
  headline figures are what the history records and what the report is about;
  adding a dimension the CSV cannot hold would mean two sources of truth.
- **No archive backfill.** `previous_stat.html` lists older quarters. The
  existing CSV already goes back to 2022/Mar, and a backfill would be a one-off
  script, not a daily code path.
- **No interpretation.** Nothing here says a supply level is high, tight or
  improving, and nothing converts a supply figure into a price implication. The
  desk describes; the operator decides. That is a SOUL rule, and the code keeps
  it by never producing an adjective.
