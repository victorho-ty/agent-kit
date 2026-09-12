"""How the price source is labelled. No network here."""

from __future__ import annotations

from fed_watch import quotes

from .conftest import quote


def test_all_intraday_reads_as_intraday():
    used = [quote("ZQU26.CBT", 96.27), quote("ZQX26.CBT", 96.04)]
    assert quotes.describe(used) == quotes.INTRADAY_SOURCE


def test_all_fallback_reads_as_daily():
    used = [
        quote("ZQU26.CBT", 96.27, quotes.DAILY_SOURCE),
        quote("ZQX26.CBT", 96.04, quotes.DAILY_SOURCE),
    ]
    assert quotes.describe(used) == quotes.DAILY_SOURCE


def test_a_partial_fallback_names_the_contract_it_applied_to():
    """A fallback on one month and not another must not vanish into a constant."""
    used = [quote("ZQU26.CBT", 96.27), quote("ZQX26.CBT", 96.04, quotes.DAILY_SOURCE)]
    described = quotes.describe(used)

    assert described.startswith(quotes.INTRADAY_SOURCE)
    assert "ZQX26.CBT" in described
    assert "ZQU26.CBT" not in described
