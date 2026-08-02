"""Render the daily price report as a single portrait PNG per scope.

Three stacked panels (phone-friendly, ~1:2 portrait -- see the
matplotlib-reports skill):

1. **Daily trend** -- one line per grouping (airline | dep bucket | ret
   bucket), ranked by the latest price, cheapest first (cap ``top`` lines).
2. **Week-over-week** -- table of the same groupings with price now vs
   ``wow_days`` ago, biggest drops first; green = cheaper than a week ago.
3. **Cheapest right now** -- the ranking table, cheapest first.

Never invents numbers: every value comes from the database via db.py.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from . import db

# Per-airline colour map; unknown carriers fall back to tab10 by index.
_AIRLINE_COLORS = {
    "Cathay Pacific": "#1f77b4",
    "Emirates": "#e6194b",
    "Qatar Airways": "#7d3c98",
    "Singapore Airlines": "#d62728",
    "Air India": "#ff7f0e",
    "IndiGo": "#2ca02c",
    "AirAsia": "#9467bd",
    "Malaysia Airlines": "#8c564b",
    "Thai Airways": "#17becf",
    "British Airways": "#bcbd22",
}
_FALLBACK_COLORS = list(plt.get_cmap("tab10").colors) + list(plt.get_cmap("tab20").colors)


def _color_for(airline: str, index: int) -> str:
    return _AIRLINE_COLORS.get(airline, _FALLBACK_COLORS[index % len(_FALLBACK_COLORS)])


def _grouping_label(row: sqlite3.Row | dict) -> str:
    return f"{row['airline']} {row['dep_bucket']}\u2192{row['ret_bucket']}"


def _currency_symbol(currency: str) -> str:
    return {"HKD": "HK$", "USD": "$", "SGD": "S$", "EUR": "\u20ac", "GBP": "\u00a3"}.get(currency, f"{currency} ")


def _format_price(value: float | None, currency: str) -> str:
    if value is None:
        return "\u2014"
    return f"{_currency_symbol(currency)}{value:,.0f}"


def build_report_data(conn: sqlite3.Connection, scope: str, run_date: str,
                      wow_days: int = 7) -> dict:
    """Aggregate everything the figure needs. Pure data -- testable without rendering."""
    cells = db.latest_cells(conn, scope, run_date)
    series = db.cell_series(conn, scope, since=(date.fromisoformat(run_date) - timedelta(days=60)).isoformat())
    wow = db.wo_w_movement(conn, scope, run_date, days=wow_days)
    dates = db.run_dates(conn, scope)

    currency = cells[0]["currency"] if cells else "HKD"

    # Groupings ranked by latest price (cheapest first); stable by grouping label.
    rankings = sorted(
        cells,
        key=lambda r: (r["min_price"], r["airline"], r["dep_bucket"], r["ret_bucket"]),
    )

    # Series keyed by grouping label -> [(run_date, min_price)].
    by_grouping: dict[str, list[tuple[str, float]]] = {}
    for row in series:
        label = _grouping_label(row)
        by_grouping.setdefault(label, []).append((row["run_date"], row["min_price"]))

    wow_rows = [
        {
            "grouping": _grouping_label(r),
            "depart_date": r["depart_date"],
            "return_date": r["return_date"],
            "price": r["price"],
            "price_7d_ago": r["price_7d_ago"],
            "delta": r["delta"],
            "delta_pct": r["delta_pct"],
        }
        for r in wow
    ]

    return {
        "scope": scope,
        "run_date": run_date,
        "currency": currency,
        "dates": dates,
        "rankings": [{**_row_dict(r), "grouping": _grouping_label(r)} for r in rankings],
        "by_grouping": by_grouping,
        "wow": wow_rows,
    }


def _row_dict(row: sqlite3.Row | dict) -> dict:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def render_report(data: dict, out_path: Path, *, top_lines: int = 8, top_rows: int = 20) -> Path:
    """Render the portrait PNG. Returns the path written."""
    scope = data["scope"]
    run_date = data["run_date"]
    currency = data["currency"]
    dates = data["dates"]
    rankings = data["rankings"]
    by_grouping = data["by_grouping"]
    wow = data["wow"]

    plt.rcParams.update({"font.family": "DejaVu Sans"})
    fig = plt.figure(figsize=(7.0, 14.0), dpi=150)
    gs = fig.add_gridspec(
        3, 1, figure=fig, height_ratios=[5.0, 2.2, 2.6],
        left=0.12, right=0.96, top=0.94, bottom=0.045, hspace=0.34,
    )

    # -- Panel 1: daily trend, ranked by latest price -----------------------
    ax1 = fig.add_subplot(gs[0])
    ax1.set_title(f"{scope} \u2014 price trend", fontsize=17, loc="left", pad=12)
    ax1.set_ylabel(f"Round-trip price ({currency})", fontsize=10)

    lines = rankings[:top_lines]
    legend_handles: list[Patch] = []
    for index, row in enumerate(lines):
        label = row["grouping"]
        points = by_grouping.get(label, [])
        if not points:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        colour = _color_for(row["airline"], index)
        ax1.plot(xs, ys, marker="o", markersize=3.5, linewidth=1.4, color=colour,
                 label=f"{label} \u00b7 {_format_price(row['min_price'], currency)}")
        legend_handles.append(Patch(color=colour))

    if dates:
        ax1.set_xticks(range(len(dates)))
        ax1.set_xticklabels([d[5:] for d in dates], fontsize=8)
    ax1.tick_params(axis="y", labelsize=9)
    ax1.grid(True, axis="y", alpha=0.25)
    if legend_handles:
        ax1.legend(handles=legend_handles, loc="upper right", fontsize=7.5, ncol=2,
                   framealpha=0.9, title=f"cheapest {min(top_lines, len(lines))} groupings",
                   title_fontsize=8)
    else:
        ax1.text(0.5, 0.5, "No price data for this scope yet", ha="center",
                 va="center", transform=ax1.transAxes, fontsize=12, color="#888888")

    # -- Panel 2: week-over-week movements ----------------------------------
    ax2 = fig.add_subplot(gs[1])
    ax2.set_title(f"Week-over-week movements ({'7' if not wow else ''}d)", fontsize=14, loc="left", pad=8)
    ax2.axis("off")

    if len(dates) < 2:
        ax2.text(0.5, 0.5, "Not enough history \u2014 need at least two run days.",
                 ha="center", va="center", transform=ax2.transAxes, fontsize=11, color="#888888")
    elif not wow:
        ax2.text(0.5, 0.5, "No cells with a week-ago comparison yet.",
                 ha="center", va="center", transform=ax2.transAxes, fontsize=11, color="#888888")
    else:
        rows = wow[:top_rows]
        headers = ["Grouping", "Now", "7d ago", "\u0394", "\u0394%"]
        cell_text = [
            [r["grouping"],
             _format_price(r["price"], currency),
             _format_price(r["price_7d_ago"], currency),
             _format_price(r["delta"], currency),
             f"{r['delta_pct']:+.1f}%" if r["delta_pct"] is not None else "\u2014"]
            for r in rows
        ]
        colours = [["white"] * len(headers) for _ in rows]
        for i, r in enumerate(rows):
            if r["delta"] is not None and r["delta"] < 0:
                colours[i][3] = "#d9f2d9"   # drop = good = green
                colours[i][4] = "#d9f2d9"
            elif r["delta"] is not None and r["delta"] > 0:
                colours[i][3] = "#f9dcdc"   # rise = red
                colours[i][4] = "#f9dcdc"
        table = ax2.table(cellText=cell_text, colLabels=headers, cellColours=colours,
                          colWidths=[0.42, 0.14, 0.14, 0.15, 0.15], loc="center")
        table.auto_set_font_size(False)
        table.set_fontsize(8.5)
        table.scale(1, 1.35)
        for (row_i, col_i), cell in table.get_celld().items():
            cell.set_edgecolor("#dddddd")
            if row_i == 0:
                cell.set_facecolor("#f0f0f0")
                cell.set_text_props(fontweight="bold")

    # -- Panel 3: cheapest right now ----------------------------------------
    ax3 = fig.add_subplot(gs[2])
    ax3.set_title("Cheapest right now", fontsize=14, loc="left", pad=8)
    ax3.axis("off")

    if not rankings:
        ax3.text(0.5, 0.5, "No price data yet.", ha="center", va="center",
                 transform=ax3.transAxes, fontsize=11, color="#888888")
    else:
        rows = rankings[:top_rows]
        headers = ["#", "Grouping", "Dep", "Ret", "Price"]
        cell_text = [
            [str(i + 1), r["grouping"], r["depart_date"][5:], r["return_date"][5:],
             _format_price(r["min_price"], currency)]
            for i, r in enumerate(rows)
        ]
        table = ax3.table(cellText=cell_text, colLabels=headers,
                          colWidths=[0.05, 0.42, 0.13, 0.13, 0.17], loc="center")
        table.auto_set_font_size(False)
        table.set_fontsize(8.5)
        table.scale(1, 1.4)
        for (row_i, col_i), cell in table.get_celld().items():
            cell.set_edgecolor("#dddddd")
            if row_i == 0:
                cell.set_facecolor("#f0f0f0")
                cell.set_text_props(fontweight="bold")
            elif row_i == 1:
                cell.set_facecolor("#e8f4e8")

    fig.suptitle(f"{scope} \u00b7 run {run_date}", fontsize=14, y=0.985, fontweight="bold")
    fig.text(0.5, 0.015,
             f"Google Flights round-trip fares \u00b7 generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             ha="center", fontsize=8, color="#999999")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path
