"""Page 2, and the baseline trap that makes reading order useless.

Every case here is built from word boxes rather than a PDF, because the bug this
module exists to prevent is invisible in extracted text: the figures are all
five-digit round numbers, so a row that picks up its neighbour's number still
looks completely plausible.
"""

from __future__ import annotations

import pytest
from conftest import word

from hk_estates_supply import extract
from hk_estates_supply.errors import ParseError

LABEL_LAND = extract.LABEL_LAND_READY
LABEL_BUILDING = extract.LABEL_BEING_BUILT
LABEL_SOLD = extract.LABEL_BUILT_NOT_SOLD


def page_words():
    """Three label/figure rows, each with the figure's baseline a few points off.

    Row two has its number *above* the label's baseline and row three has it
    below -- which is what the real PDF does, and what defeats reading order.
    """
    return [
        # row 1, y ~100
        word(LABEL_LAND, x0=60, top=100),
        word("伙", x0=430, top=100),
        word("16,000", x0=360, top=103),
        # row 2, y ~130
        word("62,000", x0=360, top=128),
        word(LABEL_BUILDING, x0=60, top=131),
        word("，減去已預售單位數目", x0=250, top=131),
        word("伙", x0=430, top=131),
        # row 3, y ~160
        word(LABEL_SOLD, x0=60, top=160),
        word("19,000", x0=360, top=164),
        word("伙", x0=430, top=160),
    ]


def test_words_group_into_visual_rows_not_reading_order():
    rows = extract.rows_from_words(page_words())
    assert len(rows) == 3
    assert rows[0][0].startswith(LABEL_LAND)
    assert LABEL_BUILDING in rows[1][0]


def test_each_figure_comes_from_its_own_row():
    rows = extract.rows_from_words(page_words())
    figures = extract.figures_from_rows(rows)
    assert figures.land_ready == 16000
    assert figures.being_built == 62000
    assert figures.built_not_sold == 19000
    assert figures.total == 97000


def test_the_being_built_label_matches_despite_its_trailing_clause():
    """The page prints "建築中的單位數目，減去已預售單位數目"; a prefix match must still land."""
    rows = extract.rows_from_words(page_words())
    assert extract.figure_for_label(rows, LABEL_BUILDING) == 62000


def test_a_number_left_of_the_label_is_not_the_answer():
    """A section number or a page number to the left must never be read as the figure."""
    words = [
        word("2", x0=20, top=100),
        word(LABEL_LAND, x0=60, top=100),
        word("16,000", x0=360, top=102),
    ]
    rows = extract.rows_from_words(words)
    assert extract.figure_for_label(rows, LABEL_LAND) == 16000


def test_word_order_within_a_row_does_not_matter():
    """The same row, words handed over in a different order, gives the same answer."""
    forwards = extract.figures_from_rows(extract.rows_from_words(page_words()))
    backwards = extract.figures_from_rows(extract.rows_from_words(list(reversed(page_words()))))
    assert forwards == backwards


def test_a_missing_label_is_a_parse_error_naming_the_label():
    rows = extract.rows_from_words([w for w in page_words() if LABEL_SOLD not in w["text"]])
    with pytest.raises(ParseError) as caught:
        extract.figure_for_label(rows, LABEL_SOLD)
    assert caught.value.detail["label"] == LABEL_SOLD


def test_a_label_with_no_figure_beside_it_is_also_a_parse_error():
    words = [word(LABEL_LAND, x0=60, top=100), word("伙", x0=430, top=100)]
    with pytest.raises(ParseError):
        extract.figure_for_label(extract.rows_from_words(words), LABEL_LAND)


def test_rows_far_apart_never_merge():
    """28pt apart in the real document; the tolerance must not swallow a whole row."""
    words = [
        word(LABEL_LAND, x0=60, top=100),
        word("16,000", x0=360, top=100),
        word(LABEL_SOLD, x0=60, top=128),
        word("19,000", x0=360, top=128),
    ]
    rows = extract.rows_from_words(words)
    assert len(rows) == 2


def test_the_printed_total_is_read_when_present():
    text = "預計未來三至四年間一手私人住宅物業market供應量為 96,000 伙。"
    assert extract.cross_check_total(None, text) == 96000


def test_an_absent_printed_total_is_none_not_zero():
    assert extract.cross_check_total(None, "no such sentence here") is None
