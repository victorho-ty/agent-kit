"""The run: seeding silence, the ledger, and the tripwires."""

from __future__ import annotations

import pytest

from hk_transaction_tracker import check as check_module
from hk_transaction_tracker import db, fetch
from hk_transaction_tracker.errors import FetchError


@pytest.fixture
def config(config_factory):
    return config_factory()


@pytest.fixture
def serve(monkeypatch, page_html):
    """Answer every fetch with the captured page, unless told otherwise."""
    def install(html=None, error=None):
        def page(url, **kwargs):
            if error:
                raise error
            return html if html is not None else page_html
        monkeypatch.setattr(check_module.fetch, "page", page)

    install()
    return install


def test_the_first_check_absorbs_the_back_catalogue_silently(config, conn, serve):
    """A year of history is not news because the tracker was installed today."""
    result = check_module.check(config, conn=conn)
    assert result["status"] == "ok"
    assert result["added"] == 23
    assert result["matched"] == 0          # nothing announced
    assert result["pending"] == 0
    assert result["estates"][0]["seeding"] is True


def test_the_second_check_reports_what_is_new(config, conn, serve, page_html):
    check_module.check(config, conn=conn)                      # seed
    conn.execute("DELETE FROM transaction_row WHERE tx_id = '26082401380100'")
    conn.commit()

    result = check_module.check(config, conn=conn)
    assert result["estates"][0]["seeding"] is False
    assert result["added"] == 1
    assert result["matched"] == 1
    assert result["pending"] == 1


def test_a_repeat_check_adds_nothing(config, conn, serve):
    check_module.check(config, conn=conn)
    again = check_module.check(config, conn=conn)
    assert again["added"] == 0
    assert again["estates"][0]["already_known"] == 23
    assert again["pending"] == 0


def test_unmatched_transactions_are_still_stored_for_the_trend(config, conn, serve):
    """The trend is estate-wide, so the archive cannot hold only the matches."""
    check_module.check(config, conn=conn)
    total = conn.execute("SELECT COUNT(*) FROM transaction_row").fetchone()[0]
    matched = conn.execute("SELECT COUNT(*) FROM transaction_row WHERE matched = 1").fetchone()[0]
    assert total == 23
    assert 0 < matched < total


def test_a_fetch_failure_is_partial_not_silent(config, conn, serve):
    serve(error=FetchError("unreachable", url="x"))
    result = check_module.check(config, conn=conn)
    assert result["status"] == "error"
    assert result["estate_failures"][0]["error"] == "ERR_FETCH"
    assert db.estate_state(conn, "泓都")["consecutive_failures"] == 1


def test_a_failure_does_not_mark_the_estate_seeded(config, conn, serve):
    """Otherwise the back catalogue would be announced on the next good run."""
    serve(error=FetchError("unreachable", url="x"))
    check_module.check(config, conn=conn)
    assert db.estate_state(conn, "泓都")["seeded"] == 0

    serve()
    result = check_module.check(config, conn=conn)
    assert result["estates"][0]["seeding"] is True
    assert result["pending"] == 0


def test_a_zero_yield_after_a_good_run_is_a_warning(config, conn, serve):
    """The page parsed but produced nothing where it used to produce plenty."""
    check_module.check(config, conn=conn)
    empty = (
        "window.__NUXT__=(function(a,b){return {state:{transaction:"
        "{transactionList:{count:a,data:[]},transactionSearch:{size:b}}}}}(0,100));"
    )
    serve(html=empty)
    result = check_module.check(config, conn=conn)
    assert result["estates"][0]["zero_yield"] is True
    assert any("payload shape" in warning for warning in result["warnings"])


def test_a_dry_run_writes_nothing(config, conn, serve):
    result = check_module.check(config, conn=conn, dry_run=True)
    assert result["dry_run"] is True
    assert result["added"] == 23
    assert result["estates"][0]["candidates"]
    assert conn.execute("SELECT COUNT(*) FROM transaction_row").fetchone()[0] == 0


def test_the_size_parameter_is_appended_once():
    url = "https://hk.centanet.com/findproperty/list/transaction/x_2-Y?q=abc"
    assert fetch.with_size(url, 100) == url + "&size=100"
    assert fetch.with_size(url + "&size=50", 100).endswith("&size=50")
    assert fetch.with_size("https://example.com/list/transaction/x", 100).endswith("?size=100")
