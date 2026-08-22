"""The daily check, the ledger, and the report payload.

The network is monkeypatched at the two functions that touch it, and the
renderer is stubbed out -- what is under test here is the decision to write a
row and the decision to wake somebody, not the drawing.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from hk_estates_supply import extract, fetch, history, report, state
from hk_estates_supply.errors import FetchError, NotFoundError
from hk_estates_supply.models import Figures, Publication

PUBLISHED_JUN = Publication(
    href="stat202606.pdf",
    url="https://www.hb.gov.hk/tc/publications/housing/private/pshpm/stat202606.pdf",
    quarter="2026/Jun", year=2026, month=6, label="2026年6月",
)
PUBLISHED_SEP = Publication(
    href="stat202609.pdf",
    url="https://www.hb.gov.hk/tc/publications/housing/private/pshpm/stat202609.pdf",
    quarter="2026/Sep", year=2026, month=9, label="2026年9月",
)


@pytest.fixture
def no_render(monkeypatch):
    monkeypatch.setattr(
        report.render, "render_all",
        lambda rows, table_rows, quarter, out_dir=None: [
            {"kind": "table", "path": f"/tmp/{quarter}.png", "caption": quarter}
        ],
    )
    monkeypatch.setattr(report.render, "cjk_font", lambda: "Noto Sans CJK TC")


def publish(monkeypatch, publication, figures=Figures(15000, 60000, 18000),
            printed_total=93000):
    monkeypatch.setattr(fetch, "latest_publication", lambda url=None: publication)
    monkeypatch.setattr(fetch, "download_pdf", lambda pub: b"%PDF-1.4 pretend")
    monkeypatch.setattr(extract, "parse_pdf", lambda data: (figures, printed_total))


# ------------------------------------------------------------------ the check


def test_the_first_run_ever_absorbs_the_back_catalogue_silently(
    monkeypatch, history_file, state_file, runs_file
):
    """Installing the bundle must not fire a report for every quarter in the file."""
    publish(monkeypatch, PUBLISHED_JUN)
    payload = report.check(history_path=history_file, state_path=state_file,
                           runs_path=runs_file)
    assert payload["seeded"] is True
    assert payload["pending"] == 0


def test_a_quarter_published_on_the_first_run_is_still_reported(
    monkeypatch, history_file, state_file, runs_file
):
    """Seeding happens before the append, or the one quarter that matters is swallowed."""
    publish(monkeypatch, PUBLISHED_SEP)
    payload = report.check(history_path=history_file, state_path=state_file,
                           runs_path=runs_file)
    assert payload["seeded"] is True
    assert payload["pending_quarters"] == ["2026/Sep"]


def test_a_quarter_already_recorded_writes_nothing_and_pends_nothing(
    monkeypatch, history_file, state_file, runs_file
):
    """89 days out of 90. The daily check must be completely silent."""
    publish(monkeypatch, PUBLISHED_JUN)
    state.mark_reported("2026/Jun", datetime.now(timezone.utc), state_file)
    before = history_file.read_text(encoding="utf-8")

    payload = report.check(history_path=history_file, state_path=state_file,
                           runs_path=runs_file)

    assert payload["new_quarter"] is False
    assert payload["pending"] == 0
    assert "next_command" not in payload
    assert history_file.read_text(encoding="utf-8") == before


def test_a_new_quarter_is_written_once_and_becomes_pending(
    monkeypatch, history_file, state_file, runs_file
):
    publish(monkeypatch, PUBLISHED_SEP)
    payload = report.check(history_path=history_file, state_path=state_file,
                           runs_path=runs_file)

    assert payload["new_quarter"] is True
    assert payload["figures"]["total"] == 93000
    assert payload["pending_quarters"] == ["2026/Sep"]
    assert payload["next_command"] == "hk-supply report --commit"
    assert history.read(history_file)[0].quarter == "2026/Sep"


def test_a_second_check_the_same_day_does_not_duplicate_the_row(
    monkeypatch, history_file, state_file, runs_file
):
    """Cron retries, and a duplicated quarter would poison every QoQ after it."""
    publish(monkeypatch, PUBLISHED_SEP)
    report.check(history_path=history_file, state_path=state_file, runs_path=runs_file)
    again = report.check(history_path=history_file, state_path=state_file,
                         runs_path=runs_file)

    assert again["new_quarter"] is False
    quarters = [row.quarter for row in history.read(history_file)]
    assert quarters.count("2026/Sep") == 1


def test_an_unsent_quarter_stays_pending_over_days(
    monkeypatch, history_file, state_file, runs_file
):
    """A send that failed on Monday is still pending on Tuesday, with no retry logic."""
    publish(monkeypatch, PUBLISHED_SEP)
    report.check(history_path=history_file, state_path=state_file, runs_path=runs_file)
    tuesday = report.check(history_path=history_file, state_path=state_file,
                           runs_path=runs_file)
    assert tuesday["pending_quarters"] == ["2026/Sep"]


def test_a_printed_total_that_disagrees_is_reported_not_raised(
    monkeypatch, history_file, state_file, runs_file
):
    """Each component is independently rounded, so the sum may legitimately differ."""
    publish(monkeypatch, PUBLISHED_SEP, printed_total=94000)
    payload = report.check(history_path=history_file, state_path=state_file,
                           runs_path=runs_file)
    assert payload["total_matches_printed"] is False
    assert payload["figures"]["total"] == 93000


def test_a_fetch_failure_is_recorded_before_it_is_raised(
    monkeypatch, history_file, state_file, runs_file
):
    """The days this could not run are exactly the days nobody would otherwise hear about."""
    def explode(url=None):
        raise FetchError("could not reach the index page", url="…")

    monkeypatch.setattr(fetch, "latest_publication", explode)
    with pytest.raises(FetchError):
        report.check(history_path=history_file, state_path=state_file, runs_path=runs_file)

    runs = state.recent_runs(5, runs_file)
    assert runs[0]["status"] == "error"
    assert state.consecutive_failures(state_file) == 1


def test_a_success_clears_the_failure_streak(
    monkeypatch, history_file, state_file, runs_file
):
    monkeypatch.setattr(fetch, "latest_publication",
                        lambda url=None: (_ for _ in ()).throw(FetchError("down")))
    with pytest.raises(FetchError):
        report.check(history_path=history_file, state_path=state_file, runs_path=runs_file)

    publish(monkeypatch, PUBLISHED_JUN)
    report.check(history_path=history_file, state_path=state_file, runs_path=runs_file)
    assert state.consecutive_failures(state_file) == 0


def test_no_download_reports_what_is_published_without_writing(
    monkeypatch, history_file, state_file, runs_file
):
    publish(monkeypatch, PUBLISHED_SEP)
    payload = report.check(download=False, history_path=history_file,
                           state_path=state_file, runs_path=runs_file)
    assert payload["published_quarter"] == "2026/Sep"
    assert payload["appended"] is False
    assert history.has_quarter(history.read(history_file), "2026/Sep") is False


# --------------------------------------------------------------------- overdue


def test_a_series_that_stopped_publishing_is_eventually_overdue():
    assert report.is_overdue("2026/Jun", date(2026, 8, 21)) is False
    assert report.is_overdue("2026/Jun", date(2027, 1, 1)) is False
    assert report.is_overdue("2026/Jun", date(2027, 2, 1)) is True


# ----------------------------------------------------------------- the report


def test_the_report_defaults_to_the_newest_quarter(history_file, state_file, no_render):
    payload = report.build(history_path=history_file, state_path=state_file)
    assert payload["quarter"] == "2026/Jun"
    assert payload["prior_quarter"] == "2026/Mar"
    assert payload["figures"]["total"] == 96000
    assert payload["qoq"]["total"]["direction"] == "down"


def test_the_report_carries_finished_lines_to_relay(history_file, state_file, no_render):
    lines = report.build(history_path=history_file, state_path=state_file)["summary_lines"]
    assert any("96,000" in line for line in lines)
    assert any("-4.95% QoQ" in line for line in lines)
    assert any("rounded to the nearest thousand" in line for line in lines)


def test_committing_clears_the_pending_queue(history_file, state_file, no_render):
    rows = history.read(history_file)
    assert state.pending(rows, state_file) != []
    report.build(commit=True, history_path=history_file, state_path=state_file)
    assert "2026/Jun" not in state.pending(rows, state_file)


def test_a_report_without_commit_leaves_the_queue_alone(history_file, state_file, no_render):
    """Somebody asking for the current picture must not consume the quarter's alert."""
    report.build(history_path=history_file, state_path=state_file)
    assert "2026/Jun" in state.pending(history.read(history_file), state_file)


def test_committing_twice_keeps_the_first_timestamp(history_file, state_file, no_render):
    report.build(commit=True, history_path=history_file, state_path=state_file)
    first = state.reported_quarters(state_file)["2026/Jun"]
    report.build(commit=True, history_path=history_file, state_path=state_file)
    assert state.reported_quarters(state_file)["2026/Jun"] == first


def test_a_re_sent_report_says_it_was_sent_before(history_file, state_file, no_render):
    report.build(commit=True, history_path=history_file, state_path=state_file)
    again = report.build(history_path=history_file, state_path=state_file)
    assert again["previously_reported"] is True


def test_an_older_quarter_can_be_asked_for_by_name(history_file, state_file, no_render):
    payload = report.build(quarter="2025/Dec", history_path=history_file,
                           state_path=state_file)
    assert payload["quarter"] == "2025/Dec"
    assert payload["figures"]["built_not_sold"] == 23000


def test_a_report_about_an_older_quarter_shows_that_quarter(history_file, state_file,
                                                            no_render):
    """The table must contain its own subject, and lead with it."""
    payload = report.build(quarter="2025/Sep", limit=3, history_path=history_file,
                           state_path=state_file)
    assert payload["table"][0]["quarter"] == "2025/Sep"
    assert all(row["quarter"] <= "2025/Sep" or row["quarter"].startswith("2025")
               for row in payload["table"])
    assert "2026/Jun" not in [row["quarter"] for row in payload["table"]]


def test_the_charts_stop_where_the_report_does(monkeypatch, history_file, state_file):
    """Trend lines running past the subject show years the text never mentions."""
    seen = {}

    def capture(rows, table_rows, quarter, out_dir=None):
        seen["quarters"] = [row.quarter for row in rows]
        return []

    monkeypatch.setattr(report.render, "render_all", capture)
    monkeypatch.setattr(report.render, "cjk_font", lambda: None)
    report.build(quarter="2025/Sep", history_path=history_file, state_path=state_file)
    assert "2025/Sep" in seen["quarters"]
    assert "2025/Dec" not in seen["quarters"]
    assert "2026/Jun" not in seen["quarters"]


def test_the_publishers_printed_total_is_what_gets_stored(
    monkeypatch, history_file, state_file, runs_file
):
    """A thousand or two apart is the source rounding parts and total separately."""
    publish(monkeypatch, PUBLISHED_SEP, figures=Figures(15000, 60000, 18000),
            printed_total=94000)
    report.check(history_path=history_file, state_path=state_file, runs_path=runs_file)
    assert history.read(history_file)[0].total == 94000


def test_a_wildly_wrong_printed_total_is_not_stored(
    monkeypatch, history_file, state_file, runs_file
):
    """Tens of thousands out is a figure read off the wrong row, not rounding."""
    publish(monkeypatch, PUBLISHED_SEP, figures=Figures(15000, 60000, 18000),
            printed_total=930000)
    payload = report.check(history_path=history_file, state_path=state_file,
                           runs_path=runs_file)
    assert history.read(history_file)[0].total == 93000
    assert payload["total_matches_printed"] is False


def test_an_unknown_quarter_is_not_found(history_file, state_file, no_render):
    with pytest.raises(NotFoundError):
        report.build(quarter="2019/Mar", history_path=history_file, state_path=state_file)


def test_the_pdf_url_is_derived_from_the_quarter(history_file, state_file, no_render):
    payload = report.build(history_path=history_file, state_path=state_file)
    assert payload["source"]["pdf_url"].endswith("stat202606.pdf")


def test_the_table_is_capped_and_still_newest_first(history_file, state_file, no_render):
    payload = report.build(limit=3, history_path=history_file, state_path=state_file)
    assert [row["quarter"] for row in payload["table"]] == [
        "2026/Jun", "2026/Mar", "2025/Dec",
    ]


def test_a_corrupt_ledger_costs_a_duplicate_not_the_report(history_file, state_file):
    """Refusing to run until a cache file is repaired would cost the report itself."""
    state_file.write_text("{ not json", encoding="utf-8")
    assert state.pending(history.read(history_file), state_file)[0] == "2025/Mar"


# ------------------------------------------------------------------- settings


def test_a_mistyped_environment_override_is_a_closed_error(monkeypatch):
    """A typo in a cron file should reach the agent as ERR_CONFIG, not a traceback."""
    from hk_estates_supply import settings
    from hk_estates_supply.errors import ConfigError

    monkeypatch.setenv("HK_SUPPLY_QUARTERS", "twelve")
    with pytest.raises(ConfigError) as caught:
        settings.table_quarters()
    assert caught.value.detail["variable"] == "HK_SUPPLY_QUARTERS"


def test_a_table_of_zero_quarters_is_refused_at_the_edge(monkeypatch):
    from hk_estates_supply import settings
    from hk_estates_supply.errors import ConfigError

    monkeypatch.setenv("HK_SUPPLY_QUARTERS", "0")
    with pytest.raises(ConfigError):
        settings.table_quarters()


def test_an_empty_override_falls_back_to_the_default(monkeypatch):
    """An unset variable exported as "" is the usual shape of a broken cron env."""
    from hk_estates_supply import settings

    monkeypatch.setenv("HK_SUPPLY_QUARTERS", "")
    assert settings.table_quarters() == settings.DEFAULT_TABLE_QUARTERS
