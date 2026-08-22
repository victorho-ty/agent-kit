"""Page 2 of the quarterly PDF, turned into three integers.

The page prints each figure to the right of its label, comma-formatted with a 伙
suffix and rounded to the nearest thousand:

    已落成但未售出的單位數目            19,000 伙

The trap, and the reason this is not three regexes over ``extract_text()``: the
label and its figure are typeset on the same visual row but with baselines a
couple of points apart, so reading-order extraction emits the number *before*
the label on some rows and *after* it on others. Any regex anchored to reading
order gets the right answer for two rows and a neighbouring row's number for the
third -- and the result still looks entirely plausible, because every figure on
the page is a five-digit round number.

So words are grouped into rows by vertical centre and then read left to right,
which is how a person reads it. ``rows_from_words`` takes plain dicts, so the
whole of this module is testable without a PDF.
"""

from __future__ import annotations

import io
import re

from .errors import ParseError
from .models import Figures

# The label substrings. The 建築中 line carries a trailing
# "，減去已預售單位數目" which is omitted here so a prefix match still works.
LABEL_LAND_READY = "已批出土地上可隨時動工的單位數目"
LABEL_BEING_BUILT = "建築中的單位數目"
LABEL_BUILT_NOT_SOLD = "已落成但未售出的單位數目"

LABELS = (
    ("land_ready", LABEL_LAND_READY),
    ("being_built", LABEL_BEING_BUILT),
    ("built_not_sold", LABEL_BUILT_NOT_SOLD),
)

# Rows on this page are about 28pt apart, so 8pt separates them cleanly while
# still absorbing the baseline difference between a label and its figure.
ROW_TOLERANCE = 8.0

NUMBER_RE = re.compile(r"^[\d,]*\d$")
# The page states the three-to-four-year total in a sentence of its own.
PRINTED_TOTAL_RE = re.compile(r"未來三至四年間.*?([\d,]+)\s*伙", re.DOTALL)

# Page 2 is where this table has lived for every issue in the history. Page 1 is
# a cover, and later pages break the figures down by district.
FIGURES_PAGE_INDEX = 1


def rows_from_words(words: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group words into visual rows, top to bottom, each ordered left to right.

    Returns ``[(row_text, words), ...]``. ``row_text`` is the row's words
    concatenated with no separator -- the Chinese text carries no spaces, and
    joining on one would break every label match.
    """
    ordered = sorted(words, key=lambda word: (word["top"] + word["bottom"]) / 2)

    rows: list[list[dict]] = []
    for word in ordered:
        centre = (word["top"] + word["bottom"]) / 2
        if rows:
            last = rows[-1][-1]
            if centre - (last["top"] + last["bottom"]) / 2 <= ROW_TOLERANCE:
                rows[-1].append(word)
                continue
        rows.append([word])

    left_to_right = [sorted(row, key=lambda word: word["x0"]) for row in rows]
    return [("".join(word["text"] for word in row), row) for row in left_to_right]


def figure_for_label(rows: list[tuple[str, list[dict]]], label: str) -> int:
    """The integer printed on the same row as ``label``, to its right.

    To its right specifically: the row also contains the label's own characters,
    and 二 is not a digit but a page carrying "(二)" as a section number is not
    hypothetical. Walking left to right until the label is complete, then taking
    the first number after it, is immune to both.
    """
    for row_text, words in rows:
        if label not in row_text:
            continue
        seen = ""
        label_end = 0.0
        for word in words:
            seen += word["text"]
            if label in seen:
                label_end = word["x1"]
                break
        for word in words:
            if word["x0"] >= label_end and NUMBER_RE.match(word["text"]):
                return int(word["text"].replace(",", ""))
        raise ParseError(
            f"found the label {label!r} but no figure to the right of it",
            label=label, row=row_text,
        )
    raise ParseError(
        f"could not locate the label {label!r} on page {FIGURES_PAGE_INDEX + 1} of the PDF",
        label=label,
        remedy="open the PDF; the layout of the summary page has changed",
    )


def figures_from_rows(rows: list[tuple[str, list[dict]]]) -> Figures:
    return Figures(**{field: figure_for_label(rows, label) for field, label in LABELS})


def cross_check_total(figures: Figures, page_text: str) -> int | None:
    """Compare the sum against the total the page prints in prose.

    Returns the printed total when it is present, ``None`` when it is not. The
    caller decides what a mismatch means; it is reported rather than raised
    because each of the three components is independently rounded to the nearest
    thousand, so their sum and a separately rounded total can legitimately differ
    by a thousand or two.
    """
    match = PRINTED_TOTAL_RE.search(page_text or "")
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def parse_pdf(pdf_bytes: bytes) -> tuple[Figures, int | None]:
    """``(figures, printed_total)`` from page 2 of a quarterly PDF."""
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) <= FIGURES_PAGE_INDEX:
                raise ParseError(
                    f"the PDF has {len(pdf.pages)} page(s); the figures live on page "
                    f"{FIGURES_PAGE_INDEX + 1}",
                    pages=len(pdf.pages),
                )
            page = pdf.pages[FIGURES_PAGE_INDEX]
            rows = rows_from_words(page.extract_words())
            page_text = page.extract_text() or ""
    except ParseError:
        raise
    except Exception as exc:  # pdfplumber raises a zoo of pdfminer errors
        raise ParseError(f"could not read the PDF: {exc}", cause=type(exc).__name__) from exc

    figures = figures_from_rows(rows)
    return figures, cross_check_total(figures, page_text)
