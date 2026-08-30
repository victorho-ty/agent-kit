"""The images: one table per side of the market, and a 呎價 line per estate.

This is read on a phone, in Telegram, which renders no HTML. So the summary is a
PNG, and it is drawn for a narrow screen rather than shrunk to fit one: six
columns, large type, and 買賣 and 租賃 on separate images instead of side by
side. A wide table that has to be pinch-zoomed is a table nobody reads.

The table is drawn by hand rather than through ``axes.table`` because the
grouping is the content. 買賣 → 屋苑 → 間隔 → 面積 is four levels, and a section
heading has to span the full width to say which bucket the rows beneath it are
in; matplotlib's table cannot merge cells, so a heading would be clipped into
the first column.

Chinese headings need a CJK font, and a headless Ubuntu box may not have one.
Rather than drawing rows of tofu boxes, :func:`cjk_font` looks for one and the
payload reports which font was used, so a summary that lost its Chinese says so
instead of looking broken.

Imports are function-local: matplotlib takes a noticeable moment to load and
builds a font cache on first use, and the daily check draws nothing on most days.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from . import fmt, settings
from .errors import RenderError

INK = "#1a1a1a"
MUTED = "#5f6368"
GRID = "#d0d7de"
HEADER_BG = "#1f3864"
HEADER_FG = "#ffffff"
SECTION_BG = "#e8eef7"
ROW_BG = "#ffffff"
ZEBRA_BG = "#f4f6f9"
PENDING_BG = "#fff8e1"      # the 面積待補 group
COLOUR_UP = "#0b8043"
COLOUR_DOWN = "#c5221f"
COLOUR_FLAT = "#5f6368"
LINE = "#1f3864"
MARKER_FACE = "#ffffff"

# In preference order. The first three ship with Ubuntu's fonts-noto-cjk
# package; the rest cover macOS and Windows.
CJK_CANDIDATES = (
    "Noto Sans CJK TC", "Noto Sans CJK HK", "Noto Sans CJK SC",
    "Noto Sans TC", "Noto Sans HK", "Source Han Sans TC", "Source Han Sans HK",
    "PingFang TC", "PingFang HK", "Heiti TC",
    "Microsoft JhengHei", "Microsoft YaHei", "SimHei", "Arial Unicode MS",
)

# Inches. A phone renders this at about 1140 px wide, which is legible without
# zooming and still sharp when zoomed.
FIGURE_WIDTH = 7.6
DPI = 150
TITLE_H = 0.52
SUBTITLE_H = 0.30
HEAD_H = 0.36
SECTION_H = 0.34
ROW_H = 0.32
FOOTER_H = 0.30
PAD = 0.07

# (key, heading, alignment, weight). The price and unit-price headings are
# filled in per deal type: a rental's column is 月租, not 成交價.
COLUMN_SPEC = (
    ("date", "成交日期", "left", 1.00),
    ("unit", "單位", "left", 1.95),
    ("bedrooms", "間隔", "center", 0.70),
    ("area", "面積(實)", "right", 0.84),
    ("price", None, "right", 1.28),
    ("unit_price", None, "right", 1.18),
)

MAX_X_TICKS = 12
Y_PAD_FRACTION = 0.12
Y_FLAT_PAD_FRACTION = 0.05


def cjk_font() -> str | None:
    """A font family on this machine that can draw Chinese, or ``None``.

    ``HK_TX_FONT`` overrides the search and is trusted without checking, so a
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


def _families(font: str | None) -> list[str]:
    return [font, "DejaVu Sans"] if font else ["DejaVu Sans"]


def _quieten() -> None:
    """Silence the per-glyph font-substitution chatter.

    It goes to stderr so it can never corrupt the JSON on stdout, but eighty
    lines of it will bury the one real error underneath.
    """
    import logging

    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    logging.getLogger("matplotlib.category").setLevel(logging.WARNING)


def prepare(out_dir: Path | None) -> Path:
    target = Path(out_dir) if out_dir else settings.image_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RenderError(f"image directory is not writable: {target}", path=str(target)) from exc
    return target


def sweep(directory: Path, days: int | None = None) -> int:
    """Delete rendered PNGs older than the retention window. Images only."""
    days = settings.image_retention_days() if days is None else days
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for path in directory.glob("*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def slug(text: str) -> str:
    """A filename fragment that survives every filesystem.

    Chinese estate names are kept -- they are valid in a filename everywhere
    this runs -- but the separators that are not are replaced.
    """
    out = []
    for char in text:
        out.append(char if char.isalnum() or char in "-_" else "-")
    return "".join(out).strip("-") or "estate"


# --------------------------------------------------------------------- the table


def columns_for(deal_type: str) -> tuple[tuple[str, str, str, float], ...]:
    """The column spec with the price headings named for this side of the market."""
    filled = []
    for key, heading, align, weight in COLUMN_SPEC:
        if key == "price":
            heading = fmt.price_label(deal_type)
        elif key == "unit_price":
            heading = fmt.unit_price_label(deal_type)
        filled.append((key, heading, align, weight))
    return tuple(filled)


def render_table(
    sections: list[dict],
    deal_type: str,
    *,
    title: str,
    subtitle: str = "",
    out_dir: Path | None = None,
    filename: str | None = None,
) -> Path:
    """One PNG of new transactions for one side of the market.

    ``sections`` is a list of ``{"heading": str, "rows": [...], "pending": bool}``
    already grouped and already ordered by the caller. This function decides
    nothing about the numbers or the grouping; it only draws them.
    """
    import matplotlib

    matplotlib.use("Agg")  # headless: there is no display when cron runs this
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    _quieten()
    target = prepare(out_dir)
    font = cjk_font()
    families = _families(font)

    sections = [section for section in sections if section.get("rows")]
    if not sections:
        raise RenderError("no rows to draw", deal_type=deal_type)

    columns = columns_for(deal_type)
    span = sum(weight for _key, _heading, _align, weight in columns)
    edges: list[float] = [0.0]
    for _key, _heading, _align, weight in columns:
        edges.append(edges[-1] + FIGURE_WIDTH * weight / span)

    body_h = HEAD_H + sum(SECTION_H + ROW_H * len(section["rows"]) for section in sections)
    height = TITLE_H + (SUBTITLE_H if subtitle else 0) + body_h + FOOTER_H

    figure = plt.figure(figsize=(FIGURE_WIDTH, height), dpi=DPI)
    figure.patch.set_facecolor("white")
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_xlim(0, FIGURE_WIDTH)
    axes.set_ylim(height, 0)          # inches, drawn top-down
    axes.axis("off")

    def cell(x, y, width, cell_h, colour):
        axes.add_patch(Rectangle(
            (x, y), width, cell_h, facecolor=colour, edgecolor=GRID, linewidth=0.5,
        ))

    def label(x, y, width, cell_h, text, align, *, colour=INK, size=10, weight="normal"):
        if align == "left":
            position, ha = x + PAD, "left"
        elif align == "right":
            position, ha = x + width - PAD, "right"
        else:
            position, ha = x + width / 2, "center"
        axes.text(
            position, y + cell_h / 2, text, ha=ha, va="center",
            fontsize=size, color=colour, fontweight=weight, fontfamily=families,
        )

    cursor = 0.0
    axes.text(
        FIGURE_WIDTH / 2, cursor + TITLE_H / 2, title, ha="center", va="center",
        fontsize=13.5, fontweight="bold", color=INK, fontfamily=families,
    )
    cursor += TITLE_H
    if subtitle:
        axes.text(
            FIGURE_WIDTH / 2, cursor + SUBTITLE_H / 2, subtitle, ha="center", va="center",
            fontsize=9.5, color=MUTED, fontfamily=families,
        )
        cursor += SUBTITLE_H

    for index, (_key, heading, align, _weight) in enumerate(columns):
        width = edges[index + 1] - edges[index]
        cell(edges[index], cursor, width, HEAD_H, HEADER_BG)
        label(
            edges[index], cursor, width, HEAD_H, heading, align,
            colour=HEADER_FG, size=9.5, weight="bold",
        )
    cursor += HEAD_H

    for section in sections:
        pending = bool(section.get("pending"))
        cell(0, cursor, FIGURE_WIDTH, SECTION_H, PENDING_BG if pending else SECTION_BG)
        label(
            0, cursor, FIGURE_WIDTH, SECTION_H, section["heading"], "left",
            size=10, weight="bold",
        )
        cursor += SECTION_H

        for row_index, row in enumerate(section["rows"]):
            background = PENDING_BG if pending else (ZEBRA_BG if row_index % 2 else ROW_BG)
            for index, (key, _heading, align, _weight) in enumerate(columns):
                width = edges[index + 1] - edges[index]
                cell(edges[index], cursor, width, ROW_H, background)
                label(
                    edges[index], cursor, width, ROW_H, str(row.get(key, fmt.EM_DASH)), align,
                    weight="bold" if key in ("price", "unit_price") else "normal",
                )
            cursor += ROW_H

    axes.text(
        FIGURE_WIDTH / 2, cursor + FOOTER_H / 2,
        "資料來源：中原地產成交紀錄。面積及呎價均為實用面積。",
        ha="center", va="center", fontsize=8, color=MUTED, fontfamily=families,
    )

    name = filename or f"table-{deal_type}.png"
    path = target / name
    try:
        figure.savefig(path, dpi=DPI, facecolor="white")
    except OSError as exc:
        raise RenderError(f"could not write {path}: {exc}", path=str(path)) from exc
    finally:
        plt.close(figure)
    return path


# --------------------------------------------------------------------- the chart


def y_limits(values: list[float]) -> tuple[float, float]:
    """The data's own range plus a buffer at each end.

    Cropped to the series rather than anchored at zero: a block's 呎價 moves
    within a band a few per cent wide, and a zero-based axis flattens the whole
    history into a line in the top tenth of the frame.

    The trade-off is real and is handled elsewhere rather than by the axis: a
    cropped window makes a small percentage move look large, so the exact change
    is in the trend line beside the chart. Never read magnitude off the slope.
    """
    if not values:
        return 0.0, 1.0
    low, high = min(values), max(values)
    spread = high - low
    pad = spread * Y_PAD_FRACTION if spread else max(1.0, abs(high) * Y_FLAT_PAD_FRACTION)
    return max(0.0, low - pad), high + pad


def tick_positions(count: int, maximum: int = MAX_X_TICKS) -> list[int]:
    """Indices to label, at most ``maximum`` of them, counted back from the newest."""
    if count <= 0:
        return []
    step = max(1, math.ceil(count / maximum))
    return list(range(count - 1, -1, -step))[::-1]


def render_chart(series: dict, *, out_dir: Path | None = None) -> Path:
    """Monthly median 呎價(實) for one estate on one side of the market."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _quieten()
    target = prepare(out_dir)
    font = cjk_font()
    families = _families(font)

    points = series.get("points") or []
    if not points:
        raise RenderError(
            "no monthly medians to draw",
            estate=series.get("estate"), deal_type=series.get("deal_type"),
        )

    months = [point["month"] for point in points]
    values = [point["median_unit_price"] for point in points]
    deal_type = series["deal_type"]

    figure = plt.figure(figsize=(FIGURE_WIDTH, 3.5), dpi=DPI)
    figure.patch.set_facecolor("white")
    axes = figure.add_subplot(111)
    axes.set_facecolor("white")

    axes.plot(
        range(len(values)), values,
        color=LINE, linewidth=2.0, marker="o", markersize=4.5,
        markerfacecolor=MARKER_FACE, markeredgecolor=LINE, markeredgewidth=1.4,
    )
    axes.set_ylim(*y_limits(values))
    ticks = tick_positions(len(months))
    axes.set_xticks(ticks)
    axes.set_xticklabels([months[index] for index in ticks], rotation=45, ha="right", fontsize=8.5)
    axes.tick_params(axis="y", labelsize=8.5)
    axes.grid(axis="y", color=GRID, linewidth=0.6, alpha=0.9)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(GRID)

    axes.set_title(
        f"{series['label']} · {series['deal_label']} 呎價(實) 月中位數",
        fontsize=12, fontweight="bold", color=INK, fontfamily=families, pad=10,
    )
    axes.set_ylabel(fmt.unit_price_label(deal_type), fontsize=9, color=MUTED, fontfamily=families)

    # The newest point labelled, because it is the number the reader came for
    # and reading it off a cropped axis is guesswork.
    axes.annotate(
        fmt.unit_price(values[-1], deal_type),
        xy=(len(values) - 1, values[-1]), xytext=(0, 9), textcoords="offset points",
        ha="right", fontsize=9, fontweight="bold", color=LINE, fontfamily=families,
    )
    note = (
        f"每點為該月成交呎價中位數，共 {sum(point['samples'] for point in points)} 宗。"
        "縱軸不由零開始。"
    )
    partial = series.get("partial_first_month")
    if partial:
        note += f"檔案由 {partial['archive_begins']} 起，該月不完整已略去。"
    figure.text(0.01, 0.02, note, fontsize=7.5, color=MUTED, fontfamily=families)
    figure.tight_layout(rect=(0, 0.05, 1, 1))

    path = target / f"chart-{slug(series['estate'])}-{deal_type}.png"
    try:
        figure.savefig(path, dpi=DPI, facecolor="white")
    except OSError as exc:
        raise RenderError(f"could not write {path}: {exc}", path=str(path)) from exc
    finally:
        plt.close(figure)
    return path
