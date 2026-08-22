"""Rendering the two images, and sweeping up after them.

Both charts are deterministic: the same bars and the same lookback produce the
same PNG. Nothing here decides *what* to draw -- the caller passes bars already
windowed -- so a chart can never disagree with the metrics printed beside it.

The agent receives a **file path**, not an image. It must not describe a curve,
a candle or a trend from one of these; the numbers that go alongside come from
the payload. That rule lives in the SOUL, and this docstring is where it bites.

Imports are function-local. matplotlib takes a noticeable moment to load and
pulls in a font cache on first use, and the scan, the poller and the whole test
suite never draw anything.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import indicators, settings
from .errors import ChartError
from .models import Bar

# Portrait is the default because the delivery surface is Telegram on a phone.
# 8 x 13.5 at 120dpi is 960 x 1620 -- close to 9:16, under Telegram's
# recompression ceiling, and tall enough that sixty candles stay distinguishable.
PORTRAIT_SIZE = (8.0, 13.5)
LANDSCAPE_SIZE = (11.0, 7.0)
DPI = 120

# A phone at arm's length, not a monitor. Nine-point type is legible in a
# desktop preview and gone entirely on a handset.
PORTRAIT_FONT = 13
LANDSCAPE_FONT = 9


def figure_size() -> tuple[float, float]:
    return PORTRAIT_SIZE if settings.chart_orientation() == "portrait" else LANDSCAPE_SIZE


def base_font() -> int:
    return PORTRAIT_FONT if settings.chart_orientation() == "portrait" else LANDSCAPE_FONT


def _quieten_matplotlib() -> None:
    """Silence the font-substitution chatter.

    mplfinance's stock styles ask for weights most systems do not ship, and
    matplotlib logs a line per glyph. It goes to stderr so it never corrupts the
    JSON on stdout, but it buries a real error in eighty lines of noise.
    """
    import logging

    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)


def _prepare(out_dir: Path | None) -> Path:
    target = Path(out_dir) if out_dir else settings.chart_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ChartError(f"chart directory is not writable: {target}", path=str(target)) from exc
    return target


def _filename(ticker: str, kind: str, lookback: int, as_of) -> str:
    """Includes the as-of date, so yesterday's chart is never mistaken for today's."""
    safe = ticker.replace("/", "-").replace("\\", "-")
    return f"{safe}_{kind}_{lookback}d_{as_of.isoformat()}.png"


def _frame(bars: list[Bar]):
    """Bars as the DataFrame mplfinance expects. pandas arrives with mplfinance."""
    import pandas as pd

    return pd.DataFrame(
        {
            "Open": [bar.open for bar in bars],
            "High": [bar.high for bar in bars],
            "Low": [bar.low for bar in bars],
            "Close": [bar.close for bar in bars],
            "Volume": [bar.volume for bar in bars],
        },
        index=pd.DatetimeIndex([bar.day for bar in bars], name="Date"),
    )


def candles(
    ticker: str,
    bars: list[Bar],
    lookback_days: int,
    out_dir: Path | None = None,
    pivot: float | None = None,
) -> dict:
    """Daily candles with a volume panel along the bottom.

    ``pivot`` draws the level the setup is coiling under, which is the one line
    that makes a breakout chart worth sending. Without it the reader has a price
    history; with it they can see the distance the payload is describing.
    """
    if not bars:
        raise ChartError(f"no bars to chart for {ticker}", ticker=ticker)

    import matplotlib

    matplotlib.use("Agg")  # no display on a cron box
    _quieten_matplotlib()
    import mplfinance as mpf

    target = _prepare(out_dir)
    path = target / _filename(ticker, "candles", lookback_days, bars[-1].day)

    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        gridstyle=":",
        y_on_right=False,
        rc={"font.size": base_font()},
    )
    import matplotlib.pyplot as plt

    try:
        # returnfig, so the title can be set as a real suptitle. Passing `title`
        # to mpf.plot draws it inside the axes, where it lands on top of the
        # candles whenever price is near the high of the window -- which is
        # exactly when a breakout chart is worth looking at.
        extras = {}
        if pivot is not None:
            extras["hlines"] = dict(
                hlines=[pivot], colors=["#c02020"], linestyle="--", linewidths=1.2
            )
        figure, _ = mpf.plot(
            _frame(bars),
            type="candle",
            volume=True,
            style=style,
            figsize=figure_size(),
            # 3:1 keeps the volume bars readable without stealing the price panel.
            panel_ratios=(3, 1),
            returnfig=True,
            ylabel="",
            ylabel_lower="Volume",
            **extras,
        )
        heading = f"{ticker} — daily, {lookback_days}d to {bars[-1].day.isoformat()}"
        if pivot is not None:
            heading += f"  ·  pivot {pivot:,.2f}"
        figure.suptitle(heading, y=0.98, fontsize=base_font() + 2)
        figure.savefig(path, dpi=DPI, bbox_inches="tight")
    except Exception as exc:
        raise ChartError(f"could not render candles for {ticker}", ticker=ticker) from exc
    finally:
        plt.close("all")

    return {
        "path": str(path),
        "kind": "candles",
        "orientation": settings.chart_orientation(),
        "pivot": pivot,
        "ticker": ticker,
        "lookback_days": lookback_days,
        "bars": len(bars),
        "from": bars[0].day.isoformat(),
        "to": bars[-1].day.isoformat(),
    }


def lines(
    ticker: str, bars: list[Bar], lookback_days: int, out_dir: Path | None = None
) -> dict:
    """Close with its 20 and 50 day simple moving averages.

    The averages are computed on the **full** history passed in and only then
    windowed for display. Computing them on the visible window alone would leave
    the first fifty days of a ninety-day chart with no SMA50 at all, and the line
    would start in mid-air.
    """
    if not bars:
        raise ChartError(f"no bars to chart for {ticker}", ticker=ticker)

    import matplotlib

    matplotlib.use("Agg")
    _quieten_matplotlib()
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    closes = [bar.close for bar in bars]
    sma20 = indicators.sma(closes, 20)
    sma50 = indicators.sma(closes, 50)

    cutoff = bars[-1].day.toordinal() - lookback_days
    visible = [index for index, bar in enumerate(bars) if bar.day.toordinal() >= cutoff]
    if not visible:
        raise ChartError(f"no bars inside a {lookback_days}d window for {ticker}", ticker=ticker)

    days = [bars[index].day for index in visible]
    target = _prepare(out_dir)
    path = target / _filename(ticker, "lines", lookback_days, bars[-1].day)

    figure, axes = plt.subplots(figsize=figure_size())
    plt.rcParams.update({"font.size": base_font()})
    axes.plot(days, [closes[index] for index in visible], linewidth=1.6, label="Close")
    axes.plot(
        days, [sma20[index] for index in visible], linewidth=1.2, label="SMA 20", alpha=0.9
    )
    axes.plot(
        days, [sma50[index] for index in visible], linewidth=1.2, label="SMA 50", alpha=0.9
    )
    axes.set_title(f"{ticker} — close and moving averages, {lookback_days}d")
    axes.grid(True, linestyle=":", alpha=0.5)
    axes.legend(loc="best", frameon=False)
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    figure.autofmt_xdate()

    try:
        figure.savefig(path, dpi=DPI, bbox_inches="tight")
    except Exception as exc:
        raise ChartError(f"could not save the line chart for {ticker}", ticker=ticker) from exc
    finally:
        plt.close(figure)

    return {
        "path": str(path),
        "kind": "lines",
        "ticker": ticker,
        "lookback_days": lookback_days,
        "series": ["close", "sma20", "sma50"],
        "bars": len(visible),
        "from": days[0].isoformat(),
        "to": days[-1].isoformat(),
    }


def sweep(out_dir: Path | None = None, retention_days: int | None = None) -> dict:
    """Delete PNGs older than the retention window.

    One image per request accumulates forever otherwise. Only files this module's
    naming produces are touched, and only inside the chart directory -- a stray
    file somebody put there is left alone.
    """
    target = _prepare(out_dir)
    days = settings.chart_retention_days() if retention_days is None else retention_days
    cutoff = time.time() - days * 86400

    removed = []
    for path in target.glob("*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue  # a locked or vanished file is not worth failing a brief over
    return {"swept": len(removed), "retention_days": days, "directory": str(target)}
