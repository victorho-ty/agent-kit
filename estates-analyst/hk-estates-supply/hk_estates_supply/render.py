"""The three images: two trend charts and the coloured quarter table.

The report is read on a phone, in Telegram, which renders no HTML. So the table
that used to be an HTML e-mail body is a PNG like the charts are, and it is
drawn for a small screen: few columns, large type, one row per quarter, newest
at the top.

**Green means the figure went up and red means it went down. Nothing here reads
that as good or bad**, and the agent must not either -- rising unsold stock and
rising land-ready stock are not the same news, and a colour that meant "good"
would have to take a view on which. Up is green because the operator asked for
up-is-green; that is the whole of the semantics.

Chinese column headings need a CJK font, and a headless Ubuntu box may not have
one. Rather than drawing a row of tofu boxes, :func:`cjk_font` looks for one and
the labels fall back to English when there is none. The payload says which
happened, so a report that lost its Chinese says so instead of looking broken.

Imports are function-local: matplotlib takes a noticeable moment to load and
builds a font cache on first use, and the daily check draws nothing on 89 days
out of 90.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from . import settings
from .errors import RenderError

# Up, down, unchanged. Text colours first, then the cell washes behind them.
COLOUR_UP = "#0b8043"
COLOUR_DOWN = "#c5221f"
COLOUR_FLAT = "#5f6368"
WASH_UP = "#e6f4ea"
WASH_DOWN = "#fce8e6"
WASH_FLAT = "#ffffff"

HEADER_BG = "#1f3864"
HEADER_FG = "#ffffff"
ZEBRA_BG = "#f4f6f9"
ROW_BG = "#ffffff"
# The quarter the report is about, so the eye lands on it first.
FOCUS_BG = "#fff8e1"
GRID = "#d0d7de"
INK = "#1a1a1a"

# At most this many x labels on a chart. Four a year means ten years of
# history is forty quarters; every one labelled is a solid grey band.
MAX_X_TICKS = 14

# In preference order. The first three ship with Ubuntu's
# fonts-noto-cjk package; the rest cover macOS and Windows.
CJK_CANDIDATES = (
    "Noto Sans CJK TC", "Noto Sans CJK HK", "Noto Sans CJK SC",
    "Noto Sans TC", "Noto Sans HK", "Source Han Sans TC", "Source Han Sans HK",
    "PingFang TC", "PingFang HK", "Heiti TC",
    "Microsoft JhengHei", "Microsoft YaHei", "SimHei", "Arial Unicode MS",
)

# (key, Chinese heading, English heading) for the three component columns.
COLUMNS = (
    ("land_ready", "可隨時動工", "Land ready"),
    ("being_built", "建築中未售", "Being built"),
    ("built_not_sold", "現樓貨尾", "Completed unsold"),
)

CHART_SERIES = (
    ("built_not_sold", "現樓貨尾", "Completed but unsold"),
    ("being_built", "建築中未售", "Under construction, unsold"),
)

# Relative column widths: Quarter, then (units, QoQ) three times, then Total.
# Not equal eighths -- "Completed unsold" is the widest heading in the deck and
# was clipped to "Completed unsol" on a machine with no CJK font, where the
# heading is English-only and rendered in a font that has a real bold face.
COLUMN_WEIGHTS = (1.05, 1.42, 1.12, 1.42, 1.12, 1.42, 1.12, 1.25)


def cjk_font() -> str | None:
    """A font family on this machine that can draw Chinese, or ``None``.

    ``HK_SUPPLY_FONT`` overrides the search and is trusted without checking, so a
    font matplotlib knows by an unexpected name can still be used.
    """
    override = settings.font_override()
    if override:
        return override

    from matplotlib import font_manager

    try:
        available = set(font_manager.get_font_names())
    except AttributeError:  # matplotlib < 3.6
        available = {font.name for font in font_manager.fontManager.ttflist}
    for name in CJK_CANDIDATES:
        if name in available:
            return name
    return None


def _heading(chinese: str, english: str, font: str | None) -> str:
    """Both languages when the glyphs will render, English alone when they will not."""
    return f"{chinese}\n{english}" if font else english


def _families(font: str | None) -> list[str]:
    base = ["DejaVu Sans"]
    return [font, *base] if font else base


def _quieten() -> None:
    """Silence the per-glyph font-substitution chatter.

    It goes to stderr so it can never corrupt the JSON on stdout, but eighty
    lines of it will bury the one real error underneath.
    """
    import logging

    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    logging.getLogger("matplotlib.category").setLevel(logging.WARNING)


def _prepare(out_dir: Path | None) -> Path:
    target = Path(out_dir) if out_dir else settings.image_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RenderError(
            f"image directory is not writable: {target}", path=str(target)
        ) from exc
    return target


def _slug(quarter: str) -> str:
    return quarter.replace("/", "-")


def _format_units(value) -> str:
    return f"{value:,.0f}" if value is not None else "—"


def format_pct(entry: dict) -> str:
    """A QoQ cell's text. ``—`` when there is no prior quarter to compare against."""
    pct = entry.get("pct")
    if pct is None:
        return "—"
    return f"{pct:+.2f}%"


def y_limits(values: list[int]) -> tuple[float, float]:
    """``(0, max * 1.12)``. The zero is the point, and it is not negotiable.

    These are counts of flats, not an index. Matplotlib's default window crops to
    the data, which renders 61,000-77,000 edge to edge and turns a 20% band into
    a picture of something doubling and halving. A desk whose whole discipline is
    not overstating a movement cannot ship an axis that overstates every movement
    by construction. The headroom above the maximum is for the end label.
    """
    top = max(values) if values else 1
    return 0.0, top * 1.12


def tick_positions(count: int, maximum: int = MAX_X_TICKS) -> list[int]:
    """Indices to label on the x axis, at most ``maximum`` of them.

    Counted back from the end, so the newest quarter -- the one the report is
    about -- always carries a label, and the thinning eats into the old end of
    the series where it costs least. Four quarters a year means ten years of
    history is forty labels, which at 45 degrees overlap into a grey band.
    """
    if count <= 0:
        return []
    step = max(1, math.ceil(count / maximum))
    return list(range(count - 1, -1, -step))[::-1]


def pct_colours(entry: dict) -> tuple[str, str]:
    """``(text_colour, cell_wash)`` for a QoQ cell, keyed on direction alone."""
    direction = entry.get("direction", "none")
    if direction == "up":
        return COLOUR_UP, WASH_UP
    if direction == "down":
        return COLOUR_DOWN, WASH_DOWN
    return COLOUR_FLAT, WASH_FLAT


# ------------------------------------------------------------------ the table


def render_table(table_rows: list[dict], quarter: str, out_dir: Path | None = None) -> Path:
    """The quarter table as a PNG: one row per quarter, newest first, QoQ coloured.

    ``table_rows`` comes from :func:`hk_estates_supply.history.table` -- already
    trimmed, already newest-first, each row carrying its own ``qoq`` block. This
    function decides nothing about the numbers; it only draws them.
    """
    import matplotlib

    matplotlib.use("Agg")  # headless: there is no display when cron runs this
    import matplotlib.pyplot as plt

    _quieten()
    target = _prepare(out_dir)
    font = cjk_font()

    headings = ["Quarter"]
    for _key, chinese, english in COLUMNS:
        headings.extend([_heading(chinese, english, font), "QoQ %"])
    headings.append(_heading("總數", "Total", font))

    cells: list[list[str]] = []
    for row in table_rows:
        line = [row["quarter"]]
        for key, _chinese, _english in COLUMNS:
            line.append(_format_units(row[key]))
            line.append(format_pct(row["qoq"][key]))
        line.append(_format_units(row["total"]))
        cells.append(line)

    if not cells:
        raise RenderError("no rows to draw", quarter=quarter)

    # The figure is built in inches from the row count rather than scaled to fit
    # afterwards, so a 4-quarter table and a 12-quarter table have identical row
    # heights and identical type sizes. Width is fixed for the same reason: the
    # report should look the same every quarter, and a phone can be trusted to
    # zoom. The table is then pinned to its axes with an explicit bbox, which is
    # what keeps a band of empty white from opening up under the title.
    title_h, footer_h = 0.72, 0.42
    header_h, row_h = 0.60, 0.40
    table_h = header_h + row_h * len(cells)
    figure_h = title_h + table_h + footer_h
    figure_w = 10.2

    figure = plt.figure(figsize=(figure_w, figure_h), dpi=150)
    figure.patch.set_facecolor("white")
    axes = figure.add_axes((0.02, footer_h / figure_h, 0.96, table_h / figure_h))
    axes.axis("off")

    try:
        span = sum(COLUMN_WEIGHTS)
        drawn = axes.table(
            cellText=cells,
            colLabels=headings,
            colWidths=[weight / span for weight in COLUMN_WEIGHTS],
            cellLoc="right",
            colLoc="center",
            bbox=(0, 0, 1, 1),
        )
        drawn.auto_set_font_size(False)
        drawn.set_fontsize(11)

        families = _families(font)
        focus_row = next(
            (index for index, row in enumerate(table_rows) if row["quarter"] == quarter),
            None,
        )

        for (row_index, column_index), cell in drawn.get_celld().items():
            cell.set_edgecolor(GRID)
            cell.set_linewidth(0.6)
            text = cell.get_text()
            text.set_fontfamily(families)

            if row_index == 0:  # the heading row
                cell.set_facecolor(HEADER_BG)
                # Relative within the bbox: the header carries two lines.
                cell.set_height(header_h / table_h)
                text.set_color(HEADER_FG)
                text.set_fontweight("bold")
                text.set_fontsize(10)
                continue

            data_index = row_index - 1
            row = table_rows[data_index]
            is_focus = data_index == focus_row
            cell.set_height(row_h / table_h)

            if column_index == 0:
                cell.set_facecolor(FOCUS_BG if is_focus else
                                   (ZEBRA_BG if data_index % 2 else ROW_BG))
                text.set_color(INK)
                text.set_ha("left")
                text.set_fontweight("bold" if is_focus else "normal")
                continue

            # Columns run: 1 units, 2 QoQ, 3 units, 4 QoQ, 5 units, 6 QoQ, 7 total.
            is_pct = column_index in (2, 4, 6)
            if is_pct:
                key = COLUMNS[(column_index - 2) // 2][0]
                colour, wash = pct_colours(row["qoq"][key])
                cell.set_facecolor(wash if wash != WASH_FLAT else
                                   (FOCUS_BG if is_focus else
                                    (ZEBRA_BG if data_index % 2 else ROW_BG)))
                text.set_color(colour)
                text.set_fontweight("bold")
            else:
                cell.set_facecolor(FOCUS_BG if is_focus else
                                   (ZEBRA_BG if data_index % 2 else ROW_BG))
                text.set_color(INK)
                text.set_fontweight("bold" if column_index == 7 or is_focus else "normal")

        figure.text(
            0.5, 1 - 0.44 / figure_h,
            f"HK private residential primary-market supply — {quarter}",
            ha="center", va="center", fontsize=13, fontweight="bold", color=INK,
            fontfamily=_families(None),
        )
        figure.text(
            0.5, 0.17 / figure_h,
            "Units, rounded to the nearest thousand at source. "
            "QoQ green = higher than the prior quarter, red = lower.",
            ha="center", va="center", fontsize=8, color=COLOUR_FLAT,
            fontfamily=_families(None),
        )

        path = target / f"hk_supply_table_{_slug(quarter)}.png"
        figure.savefig(path, facecolor="white")
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"could not draw the table: {exc}", cause=type(exc).__name__) from exc
    finally:
        plt.close(figure)

    return path


# ----------------------------------------------------------------- the charts


def render_chart(rows, field: str, chinese: str, english: str, quarter: str,
                 out_dir: Path | None = None) -> Path:
    """One series over the whole history, oldest to newest, last point labelled."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _quieten()
    target = _prepare(out_dir)
    font = cjk_font()

    from .history import quarter_key

    chrono = sorted(rows, key=lambda row: quarter_key(row.quarter))
    labels = [row.quarter for row in chrono]
    values = [getattr(row, field) for row in chrono]

    if not values:
        raise RenderError("no points to plot", field=field)

    count = len(values)
    positions = list(range(count))

    figure, axes = plt.subplots(figsize=(9.0, 4.2), dpi=150)
    figure.patch.set_facecolor("white")
    try:
        axes.plot(positions, values, marker="o", markersize=4.5, linewidth=2.0,
                  color="#1a73e8")
        axes.set_ylabel("Units", fontfamily=_families(None))
        axes.grid(True, alpha=0.3, color=GRID)
        axes.tick_params(axis="y", labelsize=9)
        axes.yaxis.set_major_formatter(lambda value, _pos: f"{value:,.0f}")

        axes.set_ylim(*y_limits(values))

        # Room on the right for the end label, in category units.
        right_pad = max(0.9, count * 0.05)
        axes.set_xlim(-0.6, count - 1 + right_pad)

        ticks = tick_positions(count)
        axes.set_xticks(ticks)
        axes.set_xticklabels([labels[index] for index in ticks],
                             rotation=45, ha="right", fontsize=8)

        # To the *right* of the last marker, inside the padding reserved above.
        # Above it collided with the line on a steep final segment, and with the
        # title whenever the last point was the series maximum.
        axes.annotate(
            f"{values[-1]:,.0f}",
            xy=(count - 1, values[-1]),
            xytext=(9, 0), textcoords="offset points",
            fontsize=10, fontweight="bold", color="#1a73e8",
            ha="left", va="center", fontfamily=_families(None),
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white",
                  "edgecolor": "none", "alpha": 0.85},
        )

        heading = f"{chinese}  {english}" if font else english
        axes.set_title(f"{heading} — {quarter}", fontsize=12, fontweight="bold",
                       color=INK, fontfamily=_families(font))
        figure.tight_layout()

        path = target / f"hk_supply_{field}_{_slug(quarter)}.png"
        figure.savefig(path, facecolor="white", bbox_inches="tight", pad_inches=0.15)
    except Exception as exc:
        raise RenderError(f"could not draw the {field} chart: {exc}",
                          field=field, cause=type(exc).__name__) from exc
    finally:
        plt.close(figure)

    return path


def render_all(rows, table_rows: list[dict], quarter: str,
               out_dir: Path | None = None) -> list[dict]:
    """Every image for one report, in the order they should be sent.

    The table leads: it carries the numbers, and the charts are context for it.
    """
    images = [{
        "kind": "table",
        "path": str(render_table(table_rows, quarter, out_dir)),
        "caption": f"HK primary-market supply — {quarter}",
    }]
    for field, chinese, english in CHART_SERIES:
        images.append({
            "kind": f"chart_{field}",
            "path": str(render_chart(rows, field, chinese, english, quarter, out_dir)),
            "caption": f"{english} ({chinese})" if cjk_font() else english,
        })
    sweep(out_dir)
    return images


def sweep(out_dir: Path | None = None) -> int:
    """Delete PNGs older than the retention window. Never raises."""
    target = Path(out_dir) if out_dir else settings.image_dir()
    if not target.exists():
        return 0
    cutoff = time.time() - settings.image_retention_days() * 86400
    removed = 0
    for path in target.glob("hk_supply_*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
