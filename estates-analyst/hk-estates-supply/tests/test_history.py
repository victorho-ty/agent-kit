"""The CSV, the quarter arithmetic, and what QoQ refuses to do."""

from __future__ import annotations

import pytest
from conftest import MOJIBAKE_HEADER_CSV

from hk_estates_supply import history
from hk_estates_supply.errors import HistoryError, NotFoundError
from hk_estates_supply.models import Figures, QuarterRow


def test_quarter_arithmetic_wraps_the_year():
    assert history.previous_quarter("2026/Mar") == "2025/Dec"
    assert history.next_quarter("2025/Dec") == "2026/Mar"
    assert history.previous_quarter("2026/Jun") == "2026/Mar"


def test_quarter_sorts_by_calendar_not_alphabet():
    """The one that bites: "Dec" < "Jun" as a string, and 12 > 6 as a month."""
    labels = ["2026/Jun", "2026/Dec", "2026/Mar", "2026/Sep"]
    assert sorted(labels, key=history.quarter_key) == [
        "2026/Mar", "2026/Jun", "2026/Sep", "2026/Dec",
    ]
    assert sorted(labels) != ["2026/Mar", "2026/Jun", "2026/Sep", "2026/Dec"]


def test_read_is_newest_first(history_file):
    rows = history.read(history_file)
    assert [row.quarter for row in rows][:3] == ["2026/Jun", "2026/Mar", "2025/Dec"]
    assert rows[0].land_ready == 16000
    assert rows[0].total == 96000


def test_an_unreadable_header_costs_nothing(tmp_path):
    """The inherited file's Chinese headers were mangled; the numbers were not."""
    path = tmp_path / "mojibake.csv"
    path.write_text(MOJIBAKE_HEADER_CSV, encoding="utf-8")
    rows = history.read(path)
    assert len(rows) == 2
    assert rows[0].quarter == "2026/Jun"
    assert rows[0].being_built == 61000


def test_a_missing_file_is_an_error_not_an_empty_history(tmp_path):
    with pytest.raises(HistoryError) as caught:
        history.read(tmp_path / "nope.csv")
    assert "not found" in caught.value.message


def test_a_short_row_names_its_line_number(tmp_path):
    path = tmp_path / "broken.csv"
    path.write_text(
        "Quarter,LandReady,BeingBuilt,BuiltNotSold,Total\n2026/Jun,16000,61000\n",
        encoding="utf-8",
    )
    with pytest.raises(HistoryError) as caught:
        history.read(path)
    assert caught.value.detail["line"] == 2


def test_append_writes_a_canonical_header_and_lf_endings(history_file):
    history.append("2026/Sep", Figures(15000, 60000, 18000), history_file)
    raw = history_file.read_bytes()
    assert raw.startswith(b"Quarter,LandReady,BeingBuilt,BuiltNotSold,Total\n")
    assert b"\r\n" not in raw
    assert history.read(history_file)[0].quarter == "2026/Sep"


def test_append_keeps_the_file_newest_first(history_file):
    history.append("2026/Sep", Figures(15000, 60000, 18000), history_file)
    lines = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines[1].startswith("2026/Sep")
    assert lines[2].startswith("2026/Jun")


def test_appending_a_quarter_twice_is_refused(history_file):
    """Published figures do not change. A second arrival wants a person, not an overwrite."""
    with pytest.raises(HistoryError) as caught:
        history.append("2026/Jun", Figures(1, 2, 3), history_file)
    assert caught.value.detail["quarter"] == "2026/Jun"
    assert history.read(history_file)[0].land_ready == 16000  # untouched


def test_total_is_the_sum_of_the_three_components():
    assert Figures(16000, 61000, 19000).total == 96000


def test_qoq_is_measured_against_the_prior_quarter(history_file):
    rows = history.read(history_file)
    qoq = history.quarter_on_quarter(rows, "2026/Jun")
    assert qoq["total"]["from"] == 101000
    assert qoq["total"]["direction"] == "down"
    assert qoq["total"]["pct"] == pytest.approx(-4.9505, abs=1e-3)
    assert qoq["land_ready"]["direction"] == "down"
    assert qoq["being_built"]["direction"] == "down"


def test_qoq_refuses_to_span_a_gap(tmp_path):
    """2025/Sep missing means 2025/Dec gets no percentage.

    The alternative -- comparing against whatever row happens to be next in the
    file -- would print a six-month change under a column headed QoQ, and it
    would look entirely normal.
    """
    path = tmp_path / "gap.csv"
    path.write_text(
        "Quarter,LandReady,BeingBuilt,BuiltNotSold,Total\n"
        "2025/Dec,20000,61000,23000,104000\n"
        "2025/Jun,10000,64000,27000,101000\n",
        encoding="utf-8",
    )
    rows = history.read(path)
    qoq = history.quarter_on_quarter(rows, "2025/Dec")
    assert qoq["total"]["pct"] is None
    assert qoq["total"]["basis"] == "unavailable"
    assert qoq["total"]["direction"] == "none"


def test_the_oldest_row_has_nothing_to_compare_against(history_file):
    qoq = history.quarter_on_quarter(history.read(history_file), "2025/Mar")
    assert qoq["built_not_sold"]["pct"] is None


def test_an_unchanged_figure_is_flat_not_up():
    rows = [
        QuarterRow("2026/Jun", 16000, 61000, 19000, 96000),
        QuarterRow("2026/Mar", 16000, 62000, 20000, 98000),
    ]
    qoq = history.quarter_on_quarter(rows, "2026/Jun")
    assert qoq["land_ready"]["direction"] == "flat"
    assert qoq["land_ready"]["pct"] == 0.0


def test_asking_for_an_absent_quarter_is_not_found(history_file):
    with pytest.raises(NotFoundError):
        history.quarter_on_quarter(history.read(history_file), "2024/Jun")


def test_table_is_trimmed_newest_first_with_qoq_attached(history_file):
    rows = history.table(history.read(history_file), 3)
    assert [row["quarter"] for row in rows] == ["2026/Jun", "2026/Mar", "2025/Dec"]
    assert rows[1]["qoq"]["total"]["from"] == 104000


def test_the_table_window_ends_at_the_quarter_it_is_about(history_file):
    """A report headed 2025/Sep must contain 2025/Sep, not the three newest quarters."""
    rows = history.table(history.read(history_file), 3, end_quarter="2025/Sep")
    assert [row["quarter"] for row in rows] == ["2025/Sep", "2025/Jun", "2025/Mar"]


def test_the_oldest_row_of_a_window_keeps_the_qoq_from_outside_it(history_file):
    """QoQ is computed against the whole file, so a slice cannot delete a comparison."""
    rows = history.table(history.read(history_file), 2, end_quarter="2026/Jun")
    oldest = rows[-1]
    assert oldest["quarter"] == "2026/Mar"
    assert oldest["qoq"]["total"]["from"] == 104000  # 2025/Dec, outside the window
    assert oldest["qoq"]["total"]["basis"] == "prior_quarter"


def test_a_window_shorter_than_the_limit_is_not_padded(history_file):
    rows = history.table(history.read(history_file), 12, end_quarter="2025/Jun")
    assert [row["quarter"] for row in rows] == ["2025/Jun", "2025/Mar"]


def test_append_prefers_the_publishers_own_total_over_the_sum(history_file):
    """Four of the eighteen inherited rows differ from their own components.

    The source rounds each part and the total separately, so the Total column has
    always meant "what the Bureau printed". Computing a sum instead would make
    the column mean one thing above a line and another below it.
    """
    history.append("2026/Sep", Figures(15000, 60000, 18000), history_file, total=94000)
    row = history.read(history_file)[0]
    assert row.total == 94000
    assert row.land_ready + row.being_built + row.built_not_sold == 93000


def test_append_falls_back_to_the_sum_when_no_total_was_printed(history_file):
    history.append("2026/Sep", Figures(15000, 60000, 18000), history_file)
    assert history.read(history_file)[0].total == 93000
