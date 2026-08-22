"""The history CSV, quarter arithmetic, and quarter-on-quarter change.

The file is five columns, newest first:

    Quarter,LandReady,BeingBuilt,BuiltNotSold,Total
    2026/Jun,16000,61000,19000,96000

**Columns are read positionally and the header is never trusted.** The file this
bundle inherited had its Chinese headers written through a non-UTF-8 console and
arrived as ``Quarter,?????,???/?????,????,Total``; the numbers underneath were
perfectly fine. Reading by position means an unreadable header costs nothing, and
rewriting the header on every append means it heals itself. Everything this
package writes is UTF-8 with LF line endings, because the file is edited on
Windows and read on Ubuntu.

Newest-first on disk is inherited too, and kept: it is the order a person opening
the file wants. Chronological order exists only inside this module, where the
percentage arithmetic needs it.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
from pathlib import Path

from .errors import HistoryError
from .models import QuarterRow

HEADER = ["Quarter", "LandReady", "BeingBuilt", "BuiltNotSold", "Total"]

# Quarter-end month -> the 3-letter label used in the CSV's Quarter column.
MONTH_LABELS = {3: "Mar", 6: "Jun", 9: "Sep", 12: "Dec"}
LABEL_MONTHS = {label: month for month, label in MONTH_LABELS.items()}

QUARTER_RE = re.compile(r"^(\d{4})/(Mar|Jun|Sep|Dec)$")


# --------------------------------------------------------------- quarter maths


def quarter_label(year: int, month: int) -> str:
    """(2026, 6) -> "2026/Jun". A non quarter-end month is a programming error."""
    if month not in MONTH_LABELS:
        raise HistoryError(f"not a quarter-end month: {month}", month=month)
    return f"{year}/{MONTH_LABELS[month]}"


def parse_quarter(quarter: str) -> tuple[int, int]:
    """"2026/Jun" -> (2026, 6)."""
    match = QUARTER_RE.match(quarter.strip())
    if match is None:
        raise HistoryError(f"unparseable quarter label: {quarter!r}", quarter=quarter)
    return int(match.group(1)), LABEL_MONTHS[match.group(2)]


def quarter_key(quarter: str) -> tuple[int, int]:
    """Sort key. Comparing the strings would put 2026/Dec before 2026/Jun."""
    return parse_quarter(quarter)


def previous_quarter(quarter: str) -> str:
    year, month = parse_quarter(quarter)
    return quarter_label(year - 1, 12) if month == 3 else quarter_label(year, month - 3)


def next_quarter(quarter: str) -> str:
    year, month = parse_quarter(quarter)
    return quarter_label(year + 1, 3) if month == 12 else quarter_label(year, month + 3)


def quarter_end_date(quarter: str):
    """The last day of the quarter, for asking whether publication is overdue."""
    from datetime import date

    year, month = parse_quarter(quarter)
    return date(year, month, 30 if month in (6, 9) else 31)


# ------------------------------------------------------------------- the file


def _coerce(value: str, *, field: str, line: int) -> int:
    """Numbers are printed with thousands separators at source; tolerate them."""
    text = value.strip().replace(",", "")
    try:
        return int(text)
    except ValueError as exc:
        raise HistoryError(
            f"line {line}: {field} is not a whole number: {value!r}",
            line=line,
            field=field,
            value=value,
        ) from exc


def read(path: Path | None = None) -> list[QuarterRow]:
    """Every row, newest first. A missing file is an error, not an empty list.

    The file is the point of this bundle. Silently treating its absence as "no
    history yet" would let a mistyped ``HK_SUPPLY_HISTORY`` quietly restart the
    record from one quarter, and the loss would only be noticed a year later.
    """
    from . import settings

    target = Path(path) if path is not None else settings.history_file()
    if not target.exists():
        raise HistoryError(
            f"history file not found: {target}",
            path=str(target),
            remedy="check HK_SUPPLY_HISTORY, or restore data/hk_units_supply_history.csv",
        )

    rows: list[QuarterRow] = []
    # utf-8-sig: the file has been round-tripped through Excel before now.
    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        for number, record in enumerate(csv.reader(handle), start=1):
            if not record or not any(cell.strip() for cell in record):
                continue
            if number == 1:
                continue  # the header, whatever state it is in
            if len(record) < 5:
                raise HistoryError(
                    f"line {number}: expected 5 columns, found {len(record)}",
                    line=number,
                    path=str(target),
                )
            quarter = record[0].strip()
            parse_quarter(quarter)  # raises with the bad label attached
            rows.append(
                QuarterRow(
                    quarter=quarter,
                    land_ready=_coerce(record[1], field="LandReady", line=number),
                    being_built=_coerce(record[2], field="BeingBuilt", line=number),
                    built_not_sold=_coerce(record[3], field="BuiltNotSold", line=number),
                    total=_coerce(record[4], field="Total", line=number),
                )
            )

    if not rows:
        raise HistoryError(f"history file has no data rows: {target}", path=str(target))
    return sorted(rows, key=lambda row: quarter_key(row.quarter), reverse=True)


def write(rows: list[QuarterRow], path: Path | None = None) -> Path:
    """Rewrite the whole file, newest first, through a temporary file.

    Eighteen rows are not worth appending in place, and a crash midway through a
    rewrite of the only copy of the record is worth ruling out entirely.
    """
    from . import settings

    target = Path(path) if path is not None else settings.history_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: quarter_key(row.quarter), reverse=True)

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", delete=False, dir=str(target.parent),
        prefix=target.name, suffix=".tmp",
    )
    try:
        with handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(HEADER)
            for row in ordered:
                writer.writerow([
                    row.quarter, row.land_ready, row.being_built,
                    row.built_not_sold, row.total,
                ])
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return target


def latest(rows: list[QuarterRow]) -> QuarterRow:
    return rows[0]


def find(rows: list[QuarterRow], quarter: str) -> QuarterRow | None:
    for row in rows:
        if row.quarter == quarter:
            return row
    return None


def has_quarter(rows: list[QuarterRow], quarter: str) -> bool:
    return find(rows, quarter) is not None


def append(quarter: str, figures, path: Path | None = None,
           total: int | None = None) -> list[QuarterRow]:
    """Add a quarter and rewrite the file. Adding one that exists is refused.

    Refused rather than overwritten: the published figures for a quarter do not
    change, so a second arrival means either this ran twice or the source
    restated something, and both deserve a person rather than an overwrite.

    ``total`` is the figure the PDF prints in prose, when it prints one. It is
    stored in preference to the sum of the three components because that is what
    the Bureau publishes as the headline and what the inherited rows already
    hold: four of the eighteen differ from their own components by a thousand or
    two, which is the source rounding each part and the total separately. Left
    to compute its own sum, this column would quietly mean one thing for rows
    written before today and another for rows written after.
    """
    rows = read(path)
    if has_quarter(rows, quarter):
        raise HistoryError(
            f"{quarter} is already in the history",
            quarter=quarter,
            remedy="use `hk-supply report --quarter <q>` to re-send it",
        )
    rows.append(
        QuarterRow(
            quarter=quarter,
            land_ready=figures.land_ready,
            being_built=figures.being_built,
            built_not_sold=figures.built_not_sold,
            total=total if total is not None else figures.total,
        )
    )
    write(rows, path)
    return sorted(rows, key=lambda row: quarter_key(row.quarter), reverse=True)


# ------------------------------------------------------------------------ QoQ

FIELDS = ("land_ready", "being_built", "built_not_sold", "total")


def quarter_on_quarter(rows: list[QuarterRow], quarter: str) -> dict[str, dict]:
    """Percentage change against the immediately preceding quarter.

    Returns one entry per field: ``{"from", "to", "delta", "pct", "direction",
    "basis"}``. ``pct`` is ``None`` when there is nothing to compare against, and
    ``direction`` is then ``"none"``.

    **The comparison is against the calendar-preceding quarter, not against the
    previous row.** If 2025/Sep were missing from the file, 2025/Dec would get no
    percentage rather than a six-month change presented as a three-month one.
    ``basis`` says which it was: ``"prior_quarter"`` or ``"unavailable"``.

    A caveat the caller passes on rather than smooths over: the source rounds
    every figure to the nearest thousand, so on a 16,000 base the smallest
    representable move is already 6.25%. These percentages are the change in the
    published rounded figures, not a measurement of the market to two decimals.
    """
    current = find(rows, quarter)
    if current is None:
        from .errors import NotFoundError

        raise NotFoundError(f"{quarter} is not in the history", quarter=quarter)

    prior = find(rows, previous_quarter(quarter))
    out: dict[str, dict] = {}
    for field in FIELDS:
        value = getattr(current, field)
        if prior is None:
            out[field] = {
                "from": None, "to": value, "delta": None, "pct": None,
                "direction": "none", "basis": "unavailable",
            }
            continue
        before = getattr(prior, field)
        delta = value - before
        pct = (delta / before * 100.0) if before else None
        out[field] = {
            "from": before,
            "to": value,
            "delta": delta,
            "pct": pct,
            "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
            "basis": "prior_quarter",
        }
    return out


def table(rows: list[QuarterRow], limit: int,
          end_quarter: str | None = None) -> list[dict]:
    """``limit`` quarters ending at ``end_quarter``, newest first, each with its QoQ.

    ``end_quarter`` defaults to the newest in the file. It exists because a
    report about 2023/Mar must show a table that *contains* 2023/Mar: windowing
    on the newest rows regardless of the subject produced a table headed
    "— 2023/Mar" whose oldest row was 2023/Sep, with nothing highlighted.

    QoQ is always computed against the full history, never against the window,
    so the oldest row in a short table still gets its percentage from the
    quarter before it rather than losing it to the slice.
    """
    newest_first = sorted(rows, key=lambda row: quarter_key(row.quarter), reverse=True)

    window = newest_first
    if end_quarter is not None:
        cutoff = quarter_key(end_quarter)
        window = [row for row in window if quarter_key(row.quarter) <= cutoff]

    return [
        {**row.as_dict(), "qoq": quarter_on_quarter(newest_first, row.quarter)}
        for row in window[:limit]
    ]
