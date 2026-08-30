"""Where 呎價(實) has been going, per estate and per side of the market.

Three decisions are baked in here, and each one is a decision:

**Medians, not means.** A block of thirty flats produces three or four
transactions a month, and one penthouse or one distressed sale moves a mean by
ten per cent. The median of four numbers is blunt, but it is not a lie about the
middle of the market.

**Estate-wide, not filtered.** The trend counts every residential transaction in
the estate, including the ones that failed the operator's 間隔 and 面積 filters.
The alternative -- a trend over just the tracked two-bedroom 500-650呎 flats --
is a median over one or two data points a quarter, which is noise wearing a
percentage sign.

**呎價(實) only.** Never 成交價. A quarter that happened to transact larger
flats would show a rising 成交價 and a falling market, and the two are not
distinguishable without the area. Saleable, not gross, throughout: the two bases
differ by roughly a quarter and mixing them would manufacture a trend on its own.

Transactions with no 面積(實) carry no 呎價(實) and are excluded from every
figure in this module. They are still reported to the reader, in their own
group, by :mod:`hk_transaction_tracker.report`.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from . import db, settings
from .models import DEAL_LABELS

# Below this, a percentage is rounding noise on a four-sample median rather than
# a direction, and is reported as flat.
FLAT_BAND_PCT = 0.05


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _window(rows: list[dict], start: date, end: date) -> list[float]:
    return [
        row["saleable_unit_price"]
        for row in rows
        if row["saleable_unit_price"] is not None
        and start.isoformat() <= row["ins_date"] <= end.isoformat()
    ]


def bucket_trend(
    conn,
    estate: str,
    deal_type: str,
    today: date,
    *,
    window_days: int = settings.DEFAULT_TREND_WINDOW_DAYS,
    min_samples: int = settings.DEFAULT_TREND_MIN_SAMPLES,
    label: str | None = None,
) -> dict:
    """The last ``window_days`` of 呎價(實) against the ``window_days`` before them."""
    rows = db.query(conn, estate=estate, deal_type=deal_type, with_unit_price=True)

    recent_start = today - timedelta(days=window_days - 1)
    previous_end = recent_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window_days - 1)

    recent = _window(rows, recent_start, today)
    previous = _window(rows, previous_start, previous_end)

    recent_median = _median(recent)
    previous_median = _median(previous)

    if len(recent) < min_samples or len(previous) < min_samples:
        # Named rather than expressed as a zero change: "not enough transactions
        # to say" and "the market did not move" are different answers, and only
        # one of them is ever true here.
        basis = "no_data" if not rows else "insufficient"
        pct, direction = None, "none"
    else:
        basis = "ok"
        pct = (recent_median - previous_median) / previous_median * 100
        direction = "flat" if abs(pct) < FLAT_BAND_PCT else ("up" if pct > 0 else "down")
        pct = round(pct, 2)

    priced = [row for row in rows if row["saleable_unit_price"] is not None]
    return {
        "estate": estate,
        "label": label or estate,
        "deal_type": deal_type,
        "deal_label": DEAL_LABELS.get(deal_type, deal_type),
        "window_days": window_days,
        "min_samples": min_samples,
        "recent": {
            "from": recent_start.isoformat(), "to": today.isoformat(),
            "median_unit_price": round(recent_median, 1) if recent_median is not None else None,
            "samples": len(recent),
        },
        "previous": {
            "from": previous_start.isoformat(), "to": previous_end.isoformat(),
            "median_unit_price": round(previous_median, 1) if previous_median is not None else None,
            "samples": len(previous),
        },
        "pct": pct,
        "direction": direction,
        "basis": basis,
        "archive": {
            "transactions": len(rows),
            "priced": len(priced),
            "earliest": min((row["ins_date"] for row in rows), default=None),
            "latest": max((row["ins_date"] for row in rows), default=None),
        },
    }


def monthly_series(
    conn,
    estate: str,
    deal_type: str,
    today: date,
    *,
    months: int = settings.DEFAULT_CHART_MONTHS,
    label: str | None = None,
) -> dict:
    """Monthly medians of 呎價(實), oldest first -- the line a chart draws.

    Months with no transaction are omitted rather than zero-filled: a gap in a
    small block's history is an absence of evidence, and a line dropping to zero
    for August would read as a collapse.

    The oldest month is dropped when the archive begins part-way through it.
    Centanet serves the newest hundred records and nothing older, so the first
    check cuts the history on whatever day it happened to run -- and a median
    over the last nine days of a month is a sample of that month, not a reading
    of it. It would sit at the left-hand end of every chart from then on, where
    a first point below the rest is read as the start of a rise that never
    happened.
    """
    cutoff = (today.replace(day=1) - timedelta(days=31 * max(0, months - 1))).replace(day=1)
    rows = db.query(
        conn, estate=estate, deal_type=deal_type, since=cutoff,
        with_unit_price=True, newest_first=False,
    )

    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(row["ins_date"][:7], []).append(row["saleable_unit_price"])

    points = [
        {
            "month": month,
            "median_unit_price": round(statistics.median(values), 1),
            "samples": len(values),
        }
        for month, values in sorted(grouped.items())
    ]

    earliest = conn.execute(
        "SELECT MIN(ins_date) FROM transaction_row WHERE estate = ? AND deal_type = ?",
        (estate, deal_type),
    ).fetchone()[0]
    partial = None
    if points and earliest and earliest[:7] == points[0]["month"] and earliest[8:10] != "01":
        partial = {**points[0], "archive_begins": earliest}
        points = points[1:]

    return {
        "estate": estate,
        "label": label or estate,
        "deal_type": deal_type,
        "deal_label": DEAL_LABELS.get(deal_type, deal_type),
        "months": months,
        "points": points,
        # Reported rather than hidden: "the archive starts here" is a fact about
        # the chart the reader is entitled to.
        "partial_first_month": partial,
    }


def summarise(trend: dict) -> str:
    """One finished line, relayed verbatim. Never re-derived by the model."""
    from . import fmt

    recent = trend["recent"]
    previous = trend["previous"]
    head = f"{trend['label']} · {trend['deal_label']} 呎價(實)"

    if trend["basis"] == "no_data":
        return f"{head}：檔案內未有可計算呎價的成交。"
    if trend["basis"] == "insufficient":
        median = fmt.unit_price(recent["median_unit_price"], trend["deal_type"])
        return (
            f"{head}：近{trend['window_days']}日 {recent['samples']} 宗"
            f"（中位數 {median}）、對上{trend['window_days']}日 {previous['samples']} 宗，"
            f"少於 {trend['min_samples']} 宗，未足以判斷升跌。"
        )

    return (
        f"{head}：近{trend['window_days']}日中位數 "
        f"{fmt.unit_price(recent['median_unit_price'], trend['deal_type'])}，"
        f"較對上{trend['window_days']}日 "
        f"{fmt.unit_price(previous['median_unit_price'], trend['deal_type'])} "
        f"{fmt.pct(trend['pct'])}"
        f"（樣本 {recent['samples']} 對 {previous['samples']} 宗）。"
    )
