"""The images. Colour is asserted on the decision, the PNGs on the fact they exist.

Whether green is the right green is not testable here and does not matter. What
matters is that the direction decides the colour and nothing else does -- because
the one thing that would quietly ruin this report is a red cell above a number
that went up.
"""

from __future__ import annotations

import pytest

from hk_estates_supply import history, render


def test_up_is_green_and_down_is_red():
    up_text, up_wash = render.pct_colours({"direction": "up", "pct": 5.0})
    down_text, down_wash = render.pct_colours({"direction": "down", "pct": -5.0})
    assert (up_text, up_wash) == (render.COLOUR_UP, render.WASH_UP)
    assert (down_text, down_wash) == (render.COLOUR_DOWN, render.WASH_DOWN)
    assert up_text != down_text


def test_colour_follows_direction_not_the_sign_of_a_rounded_percentage():
    """`pct` is derived; `direction` comes from the raw delta. Only one of them decides."""
    text, _wash = render.pct_colours({"direction": "up", "pct": 0.004})
    assert text == render.COLOUR_UP


def test_flat_and_missing_are_neither_green_nor_red():
    for entry in ({"direction": "flat", "pct": 0.0}, {"direction": "none", "pct": None}):
        text, _wash = render.pct_colours(entry)
        assert text == render.COLOUR_FLAT


def test_a_missing_percentage_prints_a_dash_not_a_zero():
    assert render.format_pct({"pct": None, "direction": "none"}) == "—"
    assert render.format_pct({"pct": -4.9505, "direction": "down"}) == "-4.95%"
    assert render.format_pct({"pct": 5.2632, "direction": "up"}) == "+5.26%"


def test_the_sign_is_always_printed():
    """"5.26%" beside a red cell reads as a fall of 5.26%. It is not one."""
    assert render.format_pct({"pct": 5.2632, "direction": "up"}).startswith("+")


def test_the_y_axis_frames_the_data_not_the_origin():
    """A zero-based axis pushes a 61,000-77,000 series into a flat band at the top."""
    bottom, top = render.y_limits([61000, 70000, 77000])
    assert bottom < 61000       # the whole series is inside the frame
    assert top > 77000
    assert bottom > 50000       # but the window is the data's, not the origin's


def test_both_ends_get_the_same_buffer():
    bottom, top = render.y_limits([14000, 28000])
    assert 14000 - bottom == pytest.approx(top - 28000)
    assert 14000 - bottom == pytest.approx(14000 * render.Y_PAD_FRACTION)


def test_a_series_that_never_moves_does_not_collapse():
    """Equal min and max would otherwise give a zero-height axis."""
    bottom, top = render.y_limits([61000, 61000, 61000])
    assert bottom < 61000 < top


def test_the_axis_never_goes_below_zero():
    """A count of flats has no negative half, however wide the buffer."""
    assert render.y_limits([500, 40000])[0] >= 0


def test_the_y_axis_survives_a_single_point():
    bottom, top = render.y_limits([19000])
    assert bottom < 19000 < top


def test_x_labels_are_thinned_before_they_can_collide():
    """Ten years of quarters is forty labels; at 45 degrees they overlap into a band."""
    ticks = render.tick_positions(40)
    assert len(ticks) <= render.MAX_X_TICKS
    assert ticks == sorted(ticks)


def test_the_newest_quarter_always_keeps_its_label():
    """It is the one the report is about. Thinning eats the old end, never the new."""
    for count in (2, 5, 18, 19, 40, 137):
        assert render.tick_positions(count)[-1] == count - 1


def test_a_short_series_labels_every_point():
    assert render.tick_positions(6) == [0, 1, 2, 3, 4, 5]


def test_the_table_renders_to_a_png(tmp_path, history_file):
    rows = history.read(history_file)
    path = render.render_table(history.table(rows, 6), "2026/Jun", tmp_path)
    assert path.exists()
    assert path.read_bytes().startswith(b"\x89PNG")
    assert path.stat().st_size > 10_000


def test_every_image_for_a_report_is_produced_in_order(tmp_path, history_file):
    rows = history.read(history_file)
    images = render.render_all(rows, history.table(rows, 6), "2026/Jun", tmp_path)
    assert [image["kind"] for image in images] == [
        "table", "chart_built_not_sold", "chart_being_built",
    ]
    for image in images:
        assert image["path"].endswith(".png")


def test_re_rendering_a_quarter_reuses_its_filenames(tmp_path, history_file):
    """Deterministic names, so a re-sent report cannot leave a directory of near-duplicates."""
    rows = history.read(history_file)
    table = history.table(rows, 6)
    first = render.render_table(table, "2026/Jun", tmp_path)
    render.render_table(table, "2026/Jun", tmp_path)
    assert len(list(tmp_path.glob("hk_supply_table_*.png"))) == 1
    assert first.name == "hk_supply_table_2026-Jun.png"


def test_a_font_that_cannot_draw_chinese_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(render, "cjk_font", lambda: None)
    assert render._heading("可隨時動工", "Land ready", None) == "Land ready"
    assert "\n" in render._heading("可隨時動工", "Land ready", "Noto Sans CJK TC")


def test_an_explicit_font_override_is_trusted(monkeypatch):
    monkeypatch.setenv("HK_SUPPLY_FONT", "Some Local Font")
    assert render.cjk_font() == "Some Local Font"


def test_the_sweep_leaves_fresh_images_alone(tmp_path, history_file):
    rows = history.read(history_file)
    render.render_table(history.table(rows, 3), "2026/Jun", tmp_path)
    render.sweep(tmp_path)
    assert list(tmp_path.glob("*.png"))
