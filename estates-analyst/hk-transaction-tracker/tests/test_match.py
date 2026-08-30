"""The criteria: ANDed, and what an unpublished dimension does to them."""

from __future__ import annotations

from hk_transaction_tracker import match
from hk_transaction_tracker.config.estates import EstateEntry, SizeRange

from .conftest import make_transaction

URL = "https://hk.centanet.com/findproperty/list/transaction/x_2-Y"


def entry(**overrides) -> EstateEntry:
    base = dict(
        name="泓都", url=URL,
        bedrooms=(2, 3),
        size_ranges=(SizeRange(500, 700),),
    )
    base.update(overrides)
    return EstateEntry(**base)


def test_both_dimensions_must_pass():
    result = match.judge(make_transaction(bedrooms=2, saleable_area=507), entry())
    assert result.matched
    assert result.size_range.label == "500-700呎"


def test_the_right_size_in_the_wrong_layout_is_rejected():
    result = match.judge(make_transaction(bedrooms=1, saleable_area=507), entry())
    assert not result.matched
    assert "間隔" in result.reason


def test_the_right_layout_at_the_wrong_size_is_rejected():
    result = match.judge(make_transaction(bedrooms=3, saleable_area=1200), entry())
    assert not result.matched
    assert "面積" in result.reason


def test_four_bedrooms_means_four_or_more():
    """Centanet's own filter reads 4 as 4房或以上, and so does the config."""
    wanted = entry(bedrooms=(4,), size_ranges=())
    assert match.judge(make_transaction(bedrooms=6), wanted).matched
    assert not match.judge(make_transaction(bedrooms=3), wanted).matched


def test_studio_is_zero():
    wanted = entry(bedrooms=(0,), size_ranges=())
    assert match.judge(make_transaction(bedrooms=0), wanted).matched


def test_no_criteria_reports_everything():
    result = match.judge(make_transaction(bedrooms=1, saleable_area=200), entry(bedrooms=(), size_ranges=()))
    assert result.matched


def test_a_missing_area_cannot_reject_a_matching_layout():
    """A quarter of sale rows arrive with no 面積(實). They are still news."""
    result = match.judge(
        make_transaction(bedrooms=3, saleable_area=None, saleable_unit_price=None), entry()
    )
    assert result.matched
    assert result.area_missing
    assert result.size_range is None
    assert "未公布" in result.reason


def test_nothing_to_go_on_is_not_a_match():
    """No 間隔 and no 面積 means no evidence, and absence must not match by default."""
    result = match.judge(
        make_transaction(bedrooms=None, saleable_area=None, saleable_unit_price=None), entry()
    )
    assert not result.matched


def test_an_untracked_side_never_matches():
    result = match.judge(make_transaction(deal_type="rental"), entry(track=("sale",)))
    assert not result.matched
    assert not result.tracked_side


def test_the_first_overlapping_band_wins_so_nothing_is_double_counted():
    wanted = entry(size_ranges=(SizeRange(400, 600), SizeRange(500, 700)), bedrooms=())
    result = match.judge(make_transaction(saleable_area=550), wanted)
    assert result.size_range.label == "400-600呎"


def test_open_ended_bands():
    assert SizeRange(high=400).contains(399)
    assert not SizeRange(high=400).contains(401)
    assert SizeRange(low=900).contains(2000)
    assert SizeRange(low=900).label == "900呎以上"
