"""Rendering the probability history, and sweeping up after it.

One chart per meeting: time of each reported change along the x-axis, the
probability of each target-range outcome up the y-axis, one line per band. The
x-axis is the reported changes, not wall-clock time, so the points are evenly
spaced -- an even spacing is honest here, because what is being read off the
chart is the sequence of moves, not their tempo.

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

# No-change is the reference line every other band is read against, so it is
# drawn differently rather than left to the colour cycle.
NO_CHANGE = "no change"


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

    import matplotlib

    matplotlib.use("Agg")  # no display on a cron box
    import matplotlib.pyplot as plt

    target = _prepare(out_dir)
    path = target / f"fedwatch_{meeting_date}_{len(timestamps)}pts_{timestamps[-1][:10]}.png"

    figure, axes = plt.subplots(figsize=figure_size())
    plt.rcParams.update({"font.size": base_font()})

    positions = range(len(timestamps))
    for label in sorted(series, key=_order):
        points = series[label]
        axes.plot(
            positions,
            points,
            marker="o",
            markersize=4,
            linewidth=1.2 if label == NO_CHANGE else 1.8,
            linestyle=":" if label == NO_CHANGE else "-",
            label=label,
        )

    axes.set_title(f"FOMC {meeting_date} — implied probabilities")
    axes.set_ylabel("probability (%)")
    axes.set_ylim(-2, 102)
    axes.grid(True, linestyle=":", alpha=0.5)
    axes.set_xticks(list(positions))
    axes.set_xticklabels([stamp[5:16].replace("T", " ") for stamp in timestamps], rotation=60)
    axes.legend(loc="best", frameon=False, fontsize=base_font() - 2)
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
        "series": sorted(series, key=_order),
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
