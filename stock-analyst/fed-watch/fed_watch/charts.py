"""Rendering the probability history, and sweeping up after it.

One chart per meeting: time of each reported change along the x-axis, the
probability of each *move* up the y-axis, one line per band.

No-change is not drawn. The bands sum to 1, so it is whatever the moves leave
over -- a line for it would spend a colour and a legend row saying nothing. It
stays in the payload, where the number is exact and free.

Each line is labelled with its latest value, so the current reading is on the
chart itself and not only in the payload beside it.

The y axis is **fitted to the data**, not pinned to 0-100, with the tick step
chosen to suit whatever span that produces. Probabilities often sit in a narrow
band for days, and on a full-height axis that is a flat line saying nothing; on
a fitted axis the same data is a readable move. The axis is still clamped to
0-100, so it never implies headroom that a probability cannot have.

The x-axis is the reported changes, not wall-clock time, so the points are
evenly spaced -- an even spacing is honest here, because what is being read off
the chart is the sequence of moves, not their tempo.

The agent receives a **file path**, not an image. It must not describe a line, a
slope or a trend from one of these; every number it says comes from the payload
rendered beside it.

Imports are function-local. matplotlib takes a noticeable moment to load and
pulls in a font cache on first use, and snapping, diffing and the whole test
suite never draw anything.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import settings
from .errors import ChartError

# Portrait by default because the delivery surface is Telegram on a phone.
PORTRAIT_SIZE = (8.0, 11.0)
LANDSCAPE_SIZE = (11.0, 7.0)
DPI = 120

PORTRAIT_FONT = 13
LANDSCAPE_FONT = 9

# Filtered out before anything is drawn; see the module docstring.
NO_CHANGE = "no change"

# The y axis is fitted to the data rather than pinned to 0-100. A band that
# spends a week between 84% and 89% is a flat line on a full-height axis and a
# legible one on a fitted axis, and on a phone that is the whole difference.
Y_BUFFER_FRACTION = 0.12
# Percentage points, so a dead-flat line still gets air above and below it
# instead of being drawn along the frame.
Y_BUFFER_MIN = 0.5
# A probability cannot sit outside this, so the axis never shows that it might.
Y_FLOOR, Y_CEILING = 0.0, 100.0

# (major, minor) tick spacing, picked by how wide the fitted axis turned out.
# A fixed step cannot serve a fitted axis: 20 points is five gridlines across
# the full scale and none at all across a range four points wide.
TICK_LADDER = (
    (0.1, 0.02),
    (0.2, 0.05),
    (0.25, 0.05),
    (0.5, 0.1),
    (1.0, 0.25),
    (2.0, 0.5),
    (2.5, 0.5),
    (5.0, 1.0),
    (10.0, 2.0),
    (20.0, 5.0),
    (25.0, 5.0),
    (50.0, 10.0),
)
# The most major gridlines a fitted axis may carry. The step chosen is the
# finest that stays inside this, so a narrow range is read off a fine ruler
# rather than three widely spaced labels.
TARGET_MAJOR_INTERVALS = 8

# Two end labels closer than this would overlap, so the upper one is nudged. A
# fraction of the visible span, not an absolute number of points: the span is no
# longer always 100, and a fixed gap would push labels off a narrow axis.
LABEL_GAP_FRACTION = 0.07

# Above this many snapshots the x labels collide at phone width, so only every
# nth is drawn. The dropped ones are still in the payload.
MAX_X_LABELS = 6


def figure_size() -> tuple[float, float]:
    return PORTRAIT_SIZE if settings.chart_orientation() == "portrait" else LANDSCAPE_SIZE


def base_font() -> int:
    return PORTRAIT_FONT if settings.chart_orientation() == "portrait" else LANDSCAPE_FONT


def _prepare(out_dir: Path | None) -> Path:
    target = Path(out_dir) if out_dir else settings.chart_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ChartError(f"chart directory is not writable: {target}", path=str(target)) from exc
    return target


def plottable(series: dict[str, list[float]]) -> dict[str, list[float]]:
    """The bands worth drawing: every move, and never no-change.

    Separated from the renderer so the decision can be tested without matplotlib
    and without writing a file.
    """
    return {label: points for label, points in series.items() if label != NO_CHANGE}


def bounds(plotted: dict[str, list[float]]) -> tuple[float, float]:
    """The y limits: the data's own range, plus air, clamped to a real probability.

    Clamping matters. A band at 99.6% with a buffer added would otherwise draw an
    axis running past 100%, implying headroom that cannot exist.
    """
    values = [value for points in plotted.values() for value in points]
    low, high = min(values), max(values)
    buffer = max((high - low) * Y_BUFFER_FRACTION, Y_BUFFER_MIN)
    return max(Y_FLOOR, low - buffer), min(Y_CEILING, high + buffer)


def ticks(span: float) -> tuple[float, float]:
    """(major, minor) spacing for a fitted axis of this width."""
    for major, minor in TICK_LADDER:
        if span / major <= TARGET_MAJOR_INTERVALS:
            return major, minor
    return TICK_LADDER[-1]


def _end_labels(
    plotted: dict[str, list[float]], gap: float
) -> list[tuple[float, float, str]]:
    """``(y_text, y_true, label)`` for the final point of each line, un-overlapped.

    Walking upwards and pushing any label that lands within ``gap`` of the one
    below it keeps the text legible when two bands finish close together. Only
    the text moves; the annotation still points at the true value and prints it.
    """
    placed: list[tuple[float, float, str]] = []
    for y_true, label in sorted((points[-1], label) for label, points in plotted.items()):
        y_text = y_true
        if placed and y_text - placed[-1][0] < gap:
            y_text = placed[-1][0] + gap
        placed.append((y_text, y_true, label))
    return placed


def _x_labels(timestamps: list[str]) -> list[str]:
    """Tick text for each snapshot, thinned so it stays readable at phone width.

    The most recent is always kept -- it is the one being read -- and the rest
    are thinned backwards from it.
    """
    step = max(1, -(-len(timestamps) // MAX_X_LABELS))
    last = len(timestamps) - 1
    return [
        stamp[5:16].replace("T", " ") if (last - index) % step == 0 else ""
        for index, stamp in enumerate(timestamps)
    ]


def history(
    meeting_date: str,
    timestamps: list[str],
    series: dict[str, list[float]],
    out_dir: Path | None = None,
) -> dict:
    """One meeting's probability paths across the reported changes."""
    if len(timestamps) < 2:
        raise ChartError(
            f"need at least two reported changes to plot {meeting_date}, have {len(timestamps)}",
            meeting_date=meeting_date,
            points=len(timestamps),
        )

    plotted = plottable(series)
    if not plotted:
        raise ChartError(
            f"nothing to plot for {meeting_date}: only 'no change' was ever priced",
            meeting_date=meeting_date,
        )

    import matplotlib

    matplotlib.use("Agg")  # no display on a cron box
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MultipleLocator

    target = _prepare(out_dir)
    path = target / f"fedwatch_{meeting_date}_{len(timestamps)}pts_{timestamps[-1][:10]}.png"

    figure, axes = plt.subplots(figsize=figure_size())
    plt.rcParams.update({"font.size": base_font()})

    positions = list(range(len(timestamps)))
    ordered = sorted(plotted, key=_order)
    colours = {}
    for label in ordered:
        line, = axes.plot(
            positions, plotted[label], marker="o", markersize=4, linewidth=1.8, label=label
        )
        colours[label] = line.get_color()

    low, high = bounds(plotted)
    major, minor = ticks(high - low)

    # Title lifted to clear the legend, which sits above the axes rather than
    # inside them: the axis is fitted, so the top line always finishes near the
    # top-right corner and would sit under an in-plot legend.
    axes.set_title(f"FOMC {meeting_date} — implied probabilities", pad=34)
    axes.set_ylabel("probability (%)")
    axes.set_ylim(low, high)
    axes.yaxis.set_major_locator(MultipleLocator(major))
    axes.yaxis.set_minor_locator(MultipleLocator(minor))
    # Enough decimals for the step to be distinguishable: a 0.5 step needs one
    # or three gridlines all read "86", and a 0.25 step needs two or they read
    # "85.2, 85.5, 85.8" -- wrong numbers, not just coarse ones.
    decimals = 0 if major.is_integer() else (1 if (major * 10).is_integer() else 2)
    axes.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.{decimals}f}"))
    axes.grid(True, which="major", axis="both", linestyle=":", alpha=0.55)
    axes.grid(True, which="minor", axis="y", linestyle=":", alpha=0.25, linewidth=0.6)
    axes.set_xticks(positions)
    axes.set_xticklabels(_x_labels(timestamps), rotation=60)

    axes.set_xlim(-0.35, positions[-1] + 0.35)

    # End labels sit just outside the right spine, in a column: x in axes
    # fractions, y in data. A data-coordinate x offset would shrink as points
    # are added and eventually print the label on top of its own marker.
    from matplotlib.transforms import blended_transform_factory

    edge = blended_transform_factory(axes.transAxes, axes.transData)
    for y_text, y_true, label in _end_labels(plotted, (high - low) * LABEL_GAP_FRACTION):
        axes.text(
            1.015,
            y_text,
            f"{y_true:.1f}%",
            transform=edge,
            va="center",
            ha="left",
            fontsize=base_font() - 1,
            fontweight="bold",
            color=colours[label],
        )

    axes.legend(
        loc="lower left",
        bbox_to_anchor=(0.0, 1.005),
        ncol=len(ordered),
        frameon=False,
        fontsize=base_font() - 2,
        columnspacing=1.6,
        handlelength=1.6,
    )
    figure.tight_layout()

    try:
        figure.savefig(path, dpi=DPI, bbox_inches="tight")
    except Exception as exc:
        raise ChartError(
            f"could not save the history chart for {meeting_date}", meeting_date=meeting_date
        ) from exc
    finally:
        plt.close(figure)

    return {
        "path": str(path),
        "meeting_date": meeting_date,
        "orientation": settings.chart_orientation(),
        "points": len(timestamps),
        "series": ordered,
        "from": timestamps[0],
        "to": timestamps[-1],
    }


def _order(label: str) -> int:
    """Cuts below no-change, hikes above, so the legend reads like the ladder."""
    if label == NO_CHANGE:
        return 0
    magnitude = int("".join(ch for ch in label if ch.isdigit()) or 0)
    return -magnitude if "cut" in label else magnitude


def sweep(out_dir: Path | None = None, retention_days: int | None = None) -> dict:
    """Delete PNGs older than the retention window.

    One image per request accumulates forever otherwise. Only files inside the
    chart directory are touched, and a stray file somebody put there is left
    alone.
    """
    target = _prepare(out_dir)
    days = settings.chart_retention_days() if retention_days is None else retention_days
    cutoff = time.time() - days * 86400

    removed = []
    for path in target.glob("fedwatch_*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue  # a locked or vanished file is not worth failing a report over
    return {"swept": len(removed), "retention_days": days, "directory": str(target)}
