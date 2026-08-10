"""Single-PNG monthly report: category pie, top-5-days table, year-to-date bars.

Portrait, vertically stacked layout for phone viewing."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter

from . import categories, config, queries

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

# Fixed slot per category: colour follows the entity, never its rank.
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CATEGORY_COLOR = {name: SLOTS[i] for i, name in enumerate(categories.CATEGORIES[: len(SLOTS)])}
CATEGORY_COLOR["Other"] = INK_MUTED
CATEGORY_COLOR[categories.UNCATEGORIZED] = BASELINE

MAX_PIE_SLICES = 8
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Segoe UI", "Arial"],
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "text.color": INK,
    }
)


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _segment_text_color(hex_color: str) -> str:
    """Pick readable text colour (dark or light) for a filled segment."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return INK if 0.299 * r + 0.587 * g + 0.114 * b > 150 else SURFACE


def _color(category: str) -> str:
    return CATEGORY_COLOR.get(category, INK_MUTED)


def _fold_to_slices(by_category: list[dict]) -> list[dict]:
    """Keep the biggest categories; anything past the palette folds into Other."""
    if len(by_category) <= MAX_PIE_SLICES:
        return by_category
    head, tail = by_category[: MAX_PIE_SLICES - 1], by_category[MAX_PIE_SLICES - 1 :]
    folded = {
        "category": "Other",
        "total": sum(r["total"] for r in tail),
        "n": sum(r["n"] for r in tail),
    }
    folded["pct"] = sum(r["pct"] for r in tail)
    return [*head, folded]


def _blank_panel(ax, title: str, message: str) -> None:
    ax.set_title(title, loc="left", fontsize=14, fontweight="bold", color=INK, pad=12)
    ax.text(0.5, 0.5, message, ha="center", va="center", color=INK_MUTED, fontsize=11, transform=ax.transAxes)
    ax.set_axis_off()


def _draw_pie(ax_pie, ax_labels, summary: dict) -> None:
    """Donut plus a value list. Every slice is labelled, so identity never rests
    on colour alone and the sub-3:1 palette slots keep their relief."""
    slices = _fold_to_slices(summary["by_category"])
    ax_pie.set_title("Spending by category", loc="left", fontsize=14, fontweight="bold", color=INK, pad=12)
    ax_labels.set_axis_off()

    if not slices:
        ax_pie.text(0.5, 0.5, "No expenses recorded", ha="center", va="center", color=INK_MUTED, fontsize=11)
        ax_pie.set_axis_off()
        return

    ax_pie.pie(
        [r["total"] for r in slices],
        colors=[_color(r["category"]) for r in slices],
        startangle=90,
        counterclock=False,
        radius=1.0,
        wedgeprops={"linewidth": 2, "edgecolor": SURFACE, "width": 0.40},
    )
    ax_pie.set(aspect="equal")
    ax_pie.text(0, 0.10, _money(summary["total"]), ha="center", va="center", fontsize=21, fontweight="bold", color=INK)
    ax_pie.text(0, -0.16, f"{summary['currency']} total", ha="center", va="center", fontsize=10.5, color=INK_SECONDARY)

    top = 0.5 + (len(slices) - 1) * 0.055
    for i, row in enumerate(slices):
        y = top - i * 0.11
        ax_labels.add_patch(
            Rectangle(
                (0.0, y - 0.018), 0.035, 0.036,
                transform=ax_labels.transAxes, facecolor=_color(row["category"]), edgecolor="none", clip_on=False,
            )
        )
        ax_labels.text(0.075, y, row["category"], ha="left", va="center", fontsize=11, color=INK,
                       transform=ax_labels.transAxes)
        ax_labels.text(0.86, y, _money(row["total"]), ha="right", va="center", fontsize=11, color=INK_SECONDARY,
                       transform=ax_labels.transAxes)
        ax_labels.text(1.0, y, f"{row['pct']:.0f}%", ha="right", va="center", fontsize=11, color=INK_MUTED,
                       transform=ax_labels.transAxes)


TABLE_CATEGORY_COLUMNS = 3


TABLE_HEADERS = {
    "Food & Drinks": "Food &\nDrinks",
    "Housing & Utilities": "Housing &\nUtilities",
    "Transportation": "Transport",
    "Entertainment": "Entertain.",
    categories.UNCATEGORIZED: "Uncat.",
}


def _table_header(name: str) -> str:
    """Keep table columns narrow"""
    return TABLE_HEADERS.get(name, name.replace(" & ", " &\n"))


def _draw_top_days(ax, rows: list[dict], month: str, currency: str) -> None:
    title = f"Top {len(rows) or 5} days — {month}"
    if not rows:
        _blank_panel(ax, title, "No expenses recorded")
        return

    ax.set_title(title, loc="left", fontsize=14, fontweight="bold", color=INK, pad=12)
    ax.set_axis_off()

    present = {}
    for row in rows:
        for category, amount in row["by_category"].items():
            present[category] = present.get(category, 0) + amount
    ranked = sorted(present, key=lambda c: -present[c])
    shown, rest = ranked[:TABLE_CATEGORY_COLUMNS], ranked[TABLE_CATEGORY_COLUMNS:]

    headers = ["Date", f"Total\n({currency})", *[_table_header(c) for c in shown]] + (["Other"] if rest else [])
    colors = [None, None, *[_color(c) for c in shown]] + ([INK_MUTED] if rest else [])
    step = 0.68 / (len(headers) - 2)  # last column right-aligns on x=1.0, flush with the rule
    x_positions = [0.0] + [0.32 + step * i for i in range(len(headers) - 1)]

    header_y, rule_y, row_h = 0.885, 0.845, 0.135

    for i, (header, x, color) in enumerate(zip(headers, x_positions, colors)):
        ax.text(
            x, header_y, header,
            ha="left" if i == 0 else "right",
            va="bottom", fontsize=10.5, linespacing=1.35, color=INK_SECONDARY, transform=ax.transAxes,
        )
        if color:
            ax.add_patch(
                Rectangle(
                    (x - 0.07, header_y - 0.028), 0.07, 0.011,
                    transform=ax.transAxes, facecolor=color, edgecolor="none", clip_on=False,
                )
            )
    ax.plot([0, 1], [rule_y, rule_y], color=BASELINE, lw=1.2, transform=ax.transAxes, clip_on=False)

    for r, row in enumerate(rows):
        y = rule_y - 0.055 - r * row_h
        values = [
            date.fromisoformat(row["day"]).strftime("%a %d %b"),
            _money(row["total"]),
            *[_money(row["by_category"][c]) if c in row["by_category"] else "–" for c in shown],
        ]
        if rest:
            other = sum(row["by_category"].get(c, 0) for c in rest)
            values.append(_money(other) if other else "–")

        for i, (value, x) in enumerate(zip(values, x_positions)):
            ax.text(
                x, y, value,
                ha="left" if i == 0 else "right", va="center",
                fontsize=11, color=INK if i <= 1 else INK_SECONDARY,
                fontweight="bold" if i == 1 else "normal", transform=ax.transAxes,
            )
        if r < len(rows) - 1:
            ax.plot([0, 1], [y - row_h / 2, y - row_h / 2], color=GRID, lw=0.8,
                    transform=ax.transAxes, clip_on=False)


def _draw_year_bars(ax, months: list[dict], year: str, currency: str) -> None:
    title = f"Monthly Total — {year}"
    if not months:
        _blank_panel(ax, title, "No expenses recorded this year")
        return

    ax.set_title(title, loc="left", fontsize=14, fontweight="bold", color=INK, pad=10)

    # Consistent stacking order: categories ranked by their yearly total.
    yearly: dict[str, float] = {}
    for entry in months:
        for category, amount in entry["by_category"].items():
            yearly[category] = yearly.get(category, 0.0) + amount
    order = sorted(yearly, key=lambda c: -yearly[c])
    max_total = max(entry["total"] for entry in months)

    x = range(len(months))
    bottoms = [0.0] * len(months)
    for category in order:
        amounts = [entry["by_category"].get(category, 0.0) for entry in months]
        bars = ax.bar(
            list(x), amounts, width=0.58, bottom=bottoms,
            color=_color(category), edgecolor=SURFACE, linewidth=2, label=category,
        )
        for i, (bar, amount) in enumerate(zip(bars, amounts)):
            if amount <= 0:
                continue
            if amount >= max_total * 0.05:  # segment tall enough to hold the value
                ax.text(
                    bar.get_x() + bar.get_width() / 2, bottoms[i] + amount / 2,
                    _money(amount), ha="center", va="center",
                    fontsize=9, fontweight="bold", color=_segment_text_color(_color(category)),
                )
            bottoms[i] += amount

    for i, entry in enumerate(months):
        ax.text(
            i, entry["total"], _money(entry["total"]),
            ha="center", va="bottom", fontsize=10, color=INK_SECONDARY,
        )

    ax.set_xticks(list(x))
    ax.set_xticklabels([MONTH_NAMES[int(m["month"].split("-")[1]) - 1] for m in months], fontsize=11, color=INK_SECONDARY)
    ax.set_ylabel(currency, fontsize=9.5, color=INK_MUTED)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.tick_params(axis="y", labelsize=10, colors=INK_MUTED, length=0)
    ax.tick_params(axis="x", length=0)
    ax.set_ylim(0, max_total * 1.16)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)


def build_report(
    conn: sqlite3.Connection, month: str, member: str | None = None, out_path: str | Path | None = None
) -> Path:
    summary = month_summary_with_currency(conn, month, member)
    days = queries.top_days(conn, month, member, limit=5)
    year = month.split("-")[0]
    months = queries.year_months_by_category(conn, year, member)

    if out_path is None:
        directory = config.report_dir()
        directory.mkdir(parents=True, exist_ok=True)
        suffix = f"_{member.lower().replace(' ', '-')}" if member else ""
        out_path = directory / f"expenses_{month}{suffix}.png"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(7.0, 14.0))
    gs = GridSpec(3, 1, figure=fig, height_ratios=[1.15, 0.9, 1.1],
                  left=0.07, right=0.965, top=0.855, bottom=0.045, hspace=0.12)
    pie_gs = GridSpecFromSubplotSpec(1, 2, subplot_spec=gs[0], width_ratios=[1.0, 0.85], wspace=0.05)

    who = member or "Household"
    fig.text(0.07, 0.958, f"{who} — Expense Report", fontsize=22, fontweight="bold", color=INK)
    fig.text(
        0.07, 0.918,
        f"{month}  ·  {summary['count']} items  ·  {summary['currency']} {_money(summary['total'])} total",
        fontsize=13, color=INK_SECONDARY,
    )

    _draw_pie(fig.add_subplot(pie_gs[0]), fig.add_subplot(pie_gs[1]), summary)
    _draw_top_days(fig.add_subplot(gs[1]), days, month, summary["currency"])
    _draw_year_bars(fig.add_subplot(gs[2]), months, year, summary["currency"])

    fig.savefig(out_path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return out_path


def month_summary_with_currency(conn: sqlite3.Connection, month: str, member: str | None) -> dict:
    summary = queries.month_summary(conn, month, member)
    row = conn.execute("SELECT currency FROM expenses ORDER BY id DESC LIMIT 1").fetchone()
    summary["currency"] = row["currency"] if row else config.currency()
    return summary
