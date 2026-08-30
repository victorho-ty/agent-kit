"""The images actually draw. Headless, into a temporary directory."""

from __future__ import annotations

import time

import pytest

from hk_transaction_tracker import render
from hk_transaction_tracker.errors import RenderError

SECTIONS = [
    {
        "heading": "泓都　·　2房　·　500-700呎　(1)",
        "rows": [{
            "date": "2026-08-17", "unit": "2座 57樓 A室", "bedrooms": "2房",
            "area": "507呎", "price": "$1,240萬", "unit_price": "$24,458/呎",
        }],
    },
    {
        "heading": "泓都　·　面積待補　(1)",
        "pending": True,
        "rows": [{
            "date": "2026-08-14", "unit": "1座 30樓 D室", "bedrooms": "3房",
            "area": "—", "price": "$2,950萬", "unit_price": "—",
        }],
    },
]

SERIES = {
    "estate": "泓都", "label": "泓都 Island Harbourview",
    "deal_type": "sale", "deal_label": "買賣",
    "points": [
        {"month": "2026-05", "median_unit_price": 22_000.0, "samples": 3},
        {"month": "2026-06", "median_unit_price": 23_000.0, "samples": 4},
        {"month": "2026-07", "median_unit_price": 22_500.0, "samples": 2},
    ],
    "partial_first_month": None,
}


def test_a_table_is_drawn(tmp_path):
    path = render.render_table(
        SECTIONS, "sale", title="買賣　新增成交", subtitle="共 2 宗", out_dir=tmp_path
    )
    assert path.exists()
    assert path.stat().st_size > 5_000       # a real image, not an empty canvas


def test_a_chart_is_drawn(tmp_path):
    path = render.render_chart(SERIES, out_dir=tmp_path)
    assert path.exists()
    assert path.stat().st_size > 5_000


def test_the_price_headings_follow_the_side_of_the_market():
    """A rental's column is 月租, not 成交價 -- the same number would be a lie."""
    sale = {key: heading for key, heading, _a, _w in render.columns_for("sale")}
    rental = {key: heading for key, heading, _a, _w in render.columns_for("rental")}
    assert sale["price"] == "成交價" and sale["unit_price"] == "呎價(實)"
    assert rental["price"] == "月租" and rental["unit_price"] == "呎租(實)"


def test_nothing_to_draw_is_an_error_not_a_blank_image(tmp_path):
    with pytest.raises(RenderError):
        render.render_table([], "sale", title="x", out_dir=tmp_path)
    with pytest.raises(RenderError):
        render.render_chart({**SERIES, "points": []}, out_dir=tmp_path)


def test_the_sweep_only_takes_old_images(tmp_path):
    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    keep = tmp_path / "archive.db"
    for path in (old, new, keep):
        path.write_bytes(b"x")
    stale = time.time() - 40 * 86400
    import os

    os.utime(old, (stale, stale))

    assert render.sweep(tmp_path, days=30) == 1
    assert not old.exists()
    assert new.exists() and keep.exists()


def test_the_axis_is_cropped_to_the_data_but_never_below_zero():
    low, high = render.y_limits([22_000, 23_000, 22_500])
    assert 0 < low < 22_000 < 23_000 < high
    assert render.y_limits([5.0])[0] >= 0


def test_a_slug_survives_a_chinese_estate_name():
    assert render.slug("港島南岸-3B期-Blue-Coast") == "港島南岸-3B期-Blue-Coast"
    assert "/" not in render.slug("a/b c")
