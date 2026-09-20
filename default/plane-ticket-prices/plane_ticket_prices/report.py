"""Render the daily price report as a single portrait PNG per scope.

Three stacked sections, in reading order:

1. **Cheapest by airline and time of day** -- one row per
   (airline, departure bucket, return bucket), showing the date pair that
   achieves that price. Cheapest first.
2. **Heatmaps by airline** -- outbound bucket (rows) x return bucket (columns),
   cell = cheapest round-trip total. One panel per airline, on a shared colour
   scale so panels are directly comparable.
3. **Week-over-week trend** -- the daily series per grouping, with each legend
   entry carrying the latest price and its change against roughly a week ago.

Every price is a **true round-trip total** for all passengers on the booking, so
there is a single price column -- not an outbound/return/total split.

A grouping is (airline, dep_bucket, ret_bucket). The underlying table also keys
on the date pair, so a grouping usually spans several date pairs; everything
here reduces those to the cheapest, which is what makes one line per grouping a
single value per run date rather than a vertical smear.

Never invents numbers: every value comes from the database via db.py.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import db  # noqa: E402

# Per-airline colour map for the trend lines; unknown carriers fall back to tab10.
_AIRLINE_COLORS = {
    "Cathay Pacific": "#1f77b4",
    "Emirates": "#e6194b",
    "Qatar Airways": "#7d3c98",
    "Singapore Airlines": "#d62728",
    "Air India": "#ff7f0e",
    "IndiGo": "#2ca02c",
    "AirAsia": "#9467bd",
    "Hong Kong Express": "#00847d",
    "Malaysia Airlines": "#8c564b",
    "Thai Airways": "#17becf",
    "British Airways": "#bcbd22",
}
_FALLBACK_COLORS = list(plt.get_cmap("tab10").colors) + list(plt.get_cmap("tab20").colors)

_HEAT_CMAP = "RdYlGn_r"          # low price = green, high = red
_WOW_TOLERANCE_DAYS = 3          # a run may have been missed; accept a near-enough baseline


_LINE_STYLES = ("-", "--", "-.", ":")


def _color_for(airline: str, index: int) -> str:
    return _AIRLINE_COLORS.get(airline, _FALLBACK_COLORS[index % len(_FALLBACK_COLORS)])


def _line_style(rank_within_airline: int) -> tuple[str, float]:
    """Distinguish groupings of the same carrier, which share a colour.

    Colour carries the airline, so the bucket combinations under it need another
    channel or five Hong Kong Express lines render as one thick teal band.
    """
    return (_LINE_STYLES[rank_within_airline % len(_LINE_STYLES)],
            1.0 - 0.12 * min(rank_within_airline, 3))


def _grouping_label(row: sqlite3.Row | dict) -> str:
    return f"{row['airline']} {row['dep_bucket']}→{row['ret_bucket']}"


def _currency_symbol(currency: str) -> str:
    return {"HKD": "HK$", "USD": "$", "SGD": "S$", "EUR": "€",
            "GBP": "£"}.get(currency, f"{currency} ")


def _format_price(value: float | None, currency: str) -> str:
    if value is None:
        return "—"
    return f"{_currency_symbol(currency)}{value:,.0f}"


def _fmt_day(iso: str) -> str:
    """'2026-12-18' -> 'Dec 18'."""
    parsed = date.fromisoformat(iso)
    return f"{parsed.strftime('%b')} {parsed.day}"


def _nights(depart: str, returnd: str) -> int:
    return (date.fromisoformat(returnd) - date.fromisoformat(depart)).days


def _bucket_order(bucket: str) -> int:
    return db.BUCKETS.index(bucket) if bucket in db.BUCKETS else len(db.BUCKETS)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def build_report_data(conn: sqlite3.Connection, scope: str, run_date: str,
                      wow_days: int = 7) -> dict:
    """Aggregate everything the figure needs. Pure data -- testable without rendering."""
    cells = db.latest_cells(conn, scope, run_date)
    series = db.cell_series(
        conn, scope,
        since=(date.fromisoformat(run_date) - timedelta(days=60)).isoformat(),
    )
    wow = db.wo_w_movement(conn, scope, run_date, days=wow_days)
    dates = db.run_dates(conn, scope)
    currency = cells[0]["currency"] if cells else "HKD"

    # -- 1. cheapest per grouping, carrying the date pair that achieves it ----
    best: dict[tuple[str, str, str], dict] = {}
    for row in cells:
        key = (row["airline"], row["dep_bucket"], row["ret_bucket"])
        current = best.get(key)
        if current is None or row["min_price"] < current["min_price"]:
            best[key] = _row_dict(row)

    rankings = []
    for key, row in best.items():
        airline, dep_bucket, ret_bucket = key
        rankings.append({
            **row,
            "grouping": _grouping_label(row),
            "nights": _nights(row["depart_date"], row["return_date"]),
        })
    rankings.sort(key=lambda r: (r["min_price"], r["airline"],
                                 _bucket_order(r["dep_bucket"]), _bucket_order(r["ret_bucket"])))

    # -- 2. heatmap: airline -> {(dep_bucket, ret_bucket): price} ------------
    heat: dict[str, dict[tuple[str, str], float]] = {}
    for row in rankings:
        heat.setdefault(row["airline"], {})[(row["dep_bucket"], row["ret_bucket"])] = row["min_price"]
    # Airlines ordered by their cheapest fare; buckets shared across panels so
    # the grids line up and a cell means the same thing in every panel.
    airlines = sorted(heat, key=lambda a: min(heat[a].values()))
    dep_buckets = sorted({r["dep_bucket"] for r in rankings}, key=_bucket_order)
    ret_buckets = sorted({r["ret_bucket"] for r in rankings}, key=_bucket_order)

    # -- 3. trend: one value per (grouping, run_date) -------------------------
    trend: dict[str, dict[str, float]] = {}
    for row in series:
        label = _grouping_label(row)
        by_date = trend.setdefault(label, {})
        price = row["min_price"]
        if row["run_date"] not in by_date or price < by_date[row["run_date"]]:
            by_date[row["run_date"]] = price

    wow_by_grouping = _wow_by_grouping(trend, run_date, wow_days)

    return {
        "scope": scope,
        "run_date": run_date,
        "currency": currency,
        "dates": dates,
        "rankings": rankings,
        "heat": heat,
        "heat_airlines": airlines,
        "dep_buckets": dep_buckets,
        "ret_buckets": ret_buckets,
        "trend": trend,
        "wow_by_grouping": wow_by_grouping,
        # Per-date-pair movements, kept for the CLI's biggest_drops/rises summary.
        "wow": [
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
        ],
    }


def _wow_by_grouping(trend: dict[str, dict[str, float]], run_date: str,
                     wow_days: int) -> dict[str, dict]:
    """Latest price vs the run closest to ``wow_days`` back, per grouping.

    The baseline must fall within ``_WOW_TOLERANCE_DAYS`` of the target, so a
    long gap in the history reports "no comparison" instead of silently
    comparing against an arbitrarily old run.
    """
    target = date.fromisoformat(run_date) - timedelta(days=wow_days)
    out: dict[str, dict] = {}
    for label, by_date in trend.items():
        if run_date not in by_date:
            continue
        candidates = [d for d in by_date
                      if abs((date.fromisoformat(d) - target).days) <= _WOW_TOLERANCE_DAYS]
        latest = by_date[run_date]
        if not candidates:
            out[label] = {"price": latest, "baseline_date": None, "baseline": None,
                          "delta": None, "delta_pct": None}
            continue
        baseline_date = min(candidates, key=lambda d: abs((date.fromisoformat(d) - target).days))
        baseline = by_date[baseline_date]
        out[label] = {
            "price": latest,
            "baseline_date": baseline_date,
            "baseline": baseline,
            "delta": round(latest - baseline, 2),
            "delta_pct": round((latest - baseline) / baseline * 100, 1) if baseline else None,
        }
    return out


def _row_dict(row: sqlite3.Row | dict) -> dict:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return dict(row)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _style_table(table) -> None:
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    for (row_i, _), cell in table.get_celld().items():
        cell.set_edgecolor("#dddddd")
        if row_i == 0:
            cell.set_facecolor("#e8eef7")
            cell.set_text_props(fontweight="bold")
        elif row_i % 2 == 0:
            cell.set_facecolor("#f7f9fc")


def _draw_table(ax, data: dict, rows: list[dict]) -> None:
    currency = data["currency"]
    headers = ["Airline", "Depart Time", "Return Time", "Out Date", "Back Date",
               "Nights", f"Price ({currency})"]
    cell_text = [
        [r["airline"], r["dep_bucket"], r["ret_bucket"],
         _fmt_day(r["depart_date"]), _fmt_day(r["return_date"]),
         str(r["nights"]), f"{r['min_price']:,.0f}"]
        for r in rows
    ]
    table = ax.table(
        cellText=cell_text, colLabels=headers,
        colWidths=[0.26, 0.13, 0.13, 0.12, 0.12, 0.08, 0.16],
        cellLoc="center", bbox=[0, 0, 1, 1],   # fills the axes exactly -- no title overlap
    )
    _style_table(table)
    for row_i in range(len(cell_text) + 1):
        table[row_i, 0].set_text_props(ha="left")
        table[row_i, 0].PAD = 0.04
    # Highlight the cheapest row.
    if cell_text:
        for col in range(len(headers)):
            table[1, col].set_facecolor("#d9f2d9")


def _draw_heatmaps(fig, gridspec_cell, data: dict) -> None:
    airlines = data["heat_airlines"]
    dep_buckets = data["dep_buckets"]
    ret_buckets = data["ret_buckets"]
    currency = data["currency"]

    if not airlines:
        ax = fig.add_subplot(gridspec_cell)
        ax.axis("off")
        ax.text(0.5, 0.5, "No price data yet.", ha="center", va="center",
                transform=ax.transAxes, fontsize=11, color="#888888")
        return

    prices = [p for cells in data["heat"].values() for p in cells.values()]
    vmin, vmax = min(prices), max(prices)
    if vmin == vmax:                       # single price -> avoid a degenerate scale
        vmin, vmax = vmin * 0.95, vmax * 1.05
    cmap = matplotlib.colormaps[_HEAT_CMAP].with_extremes(bad="#f2f2f2")
    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax)

    inner = gridspec_cell.subgridspec(1, len(airlines), wspace=0.28)
    for index, airline in enumerate(airlines):
        ax = fig.add_subplot(inner[0, index])
        matrix = np.full((len(dep_buckets), len(ret_buckets)), np.nan)
        for (dep, ret), price in data["heat"][airline].items():
            matrix[dep_buckets.index(dep), ret_buckets.index(ret)] = price
        ax.imshow(np.ma.masked_invalid(matrix), cmap=cmap, norm=norm, aspect="auto")

        ax.set_title(airline, fontsize=10, pad=6)
        ax.set_xticks(range(len(ret_buckets)))
        ax.set_xticklabels(ret_buckets, fontsize=7, rotation=45, ha="right")
        ax.set_yticks(range(len(dep_buckets)))
        ax.set_yticklabels(dep_buckets if index == 0 else [""] * len(dep_buckets), fontsize=7)
        ax.set_xlabel("Return departs", fontsize=8)
        if index == 0:
            ax.set_ylabel("Outbound departs", fontsize=8)
        ax.tick_params(length=0)

        for row_i in range(len(dep_buckets)):
            for col_i in range(len(ret_buckets)):
                value = matrix[row_i, col_i]
                if np.isnan(value):
                    continue
                red, green, blue, _ = cmap(norm(value))
                luminance = 0.299 * red + 0.587 * green + 0.114 * blue
                ax.text(col_i, row_i, f"{value:,.0f}", ha="center", va="center",
                        fontsize=7.5, color="white" if luminance < 0.55 else "#222222")


def _draw_trend(ax, ax_legend, data: dict, top_lines: int) -> None:
    currency = data["currency"]
    dates = data["dates"]
    trend = data["trend"]
    wow = data["wow_by_grouping"]

    ax.set_title("Week-over-week trend", fontsize=13, loc="left", pad=8)
    ax.set_ylabel(f"Round-trip total ({currency})", fontsize=9)
    ax_legend.axis("off")

    if len(dates) < 2:
        ax.text(0.5, 0.5, "Not enough history — need at least two run days.",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=11, color="#888888")
        ax.set_xticks([])
        ax.set_yticks([])
        return

    positions = {run_date: index for index, run_date in enumerate(dates)}
    seen_per_airline: dict[str, int] = {}
    for index, row in enumerate(data["rankings"][:top_lines]):
        label = row["grouping"]
        points = sorted(trend.get(label, {}).items())
        if not points:
            continue
        xs = [positions[d] for d, _ in points if d in positions]
        ys = [price for d, price in points if d in positions]
        rank = seen_per_airline.get(row["airline"], 0)
        seen_per_airline[row["airline"]] = rank + 1
        style, alpha = _line_style(rank)
        movement = wow.get(label, {})
        suffix = (f"  ({movement['delta_pct']:+.1f}% WoW)"
                  if movement.get("delta_pct") is not None else "")
        # Two lines: the grouping is long enough that a single line overflows the
        # legend column and gets clipped at the figure edge.
        ax.plot(xs, ys, marker="o", markersize=3.2, linewidth=1.4,
                linestyle=style, alpha=alpha,
                color=_color_for(row["airline"], index),
                label=f"{label}\n{_format_price(row['min_price'], currency)}{suffix}")

    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels([d[5:] for d in dates], fontsize=8, rotation=45, ha="right")
    ax.set_xlabel("Run date", fontsize=9, labelpad=2)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(True, axis="y", alpha=0.25)

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        # Hosted in its own axes so it can never be clipped by the figure edge.
        ax_legend.legend(handles, labels, loc="upper left", fontsize=6.8,
                         framealpha=0.9, title="cheapest groupings", title_fontsize=8,
                         borderaxespad=0.0, labelspacing=0.7, handlelength=1.8)
    if not any(m.get("delta") is not None for m in wow.values()):
        ax.text(0.02, 0.04,
                "No run from about a week ago yet — week-over-week starts once the "
                "history is that deep.",
                transform=ax.transAxes, fontsize=7.5, color="#888888", style="italic")


def render_report(data: dict, out_path: Path, *, top_lines: int = 8,
                  top_rows: int = 12) -> Path:
    """Render the portrait PNG. Returns the path written."""
    rankings = data["rankings"]
    table_rows = rankings[:top_rows]

    plt.rcParams.update({"font.family": "DejaVu Sans"})

    # Height scales with the table so rows never overflow their panel.
    table_h = 0.30 * (len(table_rows) + 1) + 0.35
    heat_h = 3.3 if data["heat_airlines"] else 1.0
    trend_h = 3.6
    fig = plt.figure(figsize=(9.0, table_h + heat_h + trend_h + 1.3), dpi=150)
    gs = fig.add_gridspec(
        3, 1, figure=fig, height_ratios=[table_h, heat_h, trend_h],
        left=0.075, right=0.98, top=0.94, bottom=0.075, hspace=0.36,
    )

    # -- 1. cheapest by airline and time of day -----------------------------
    ax_table = fig.add_subplot(gs[0])
    ax_table.axis("off")
    ax_table.set_title("Cheapest by airline and time of day", fontsize=13,
                       loc="left", pad=10)
    if table_rows:
        _draw_table(ax_table, data, table_rows)
    else:
        ax_table.text(0.5, 0.5, "No price data yet.", ha="center", va="center",
                      transform=ax_table.transAxes, fontsize=11, color="#888888")

    # -- 2. heatmaps by airline ---------------------------------------------
    _draw_heatmaps(fig, gs[1], data)

    # -- 3. week-over-week trend --------------------------------------------
    trend_gs = gs[2].subgridspec(1, 2, width_ratios=[2.35, 1.0], wspace=0.04)
    ax_trend = fig.add_subplot(trend_gs[0, 0])
    ax_legend = fig.add_subplot(trend_gs[0, 1])
    _draw_trend(ax_trend, ax_legend, data, top_lines)

    fig.suptitle(f"{data['scope']} · run {data['run_date']}",
                 fontsize=14, y=0.985, fontweight="bold")
    fig.text(0.5, 0.016,
             "Google Flights round-trip totals, all passengers · generated "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M')}",
             ha="center", fontsize=8, color="#999999")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
