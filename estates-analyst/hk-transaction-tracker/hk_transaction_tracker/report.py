"""Turning pending transactions into a message somebody would actually read.

The grouping is the specification: 買賣 before 租賃, then estate, then 間隔, then
面積 band. Everything the agent says comes out of here already written --
``summary_lines`` are finished strings, relayed verbatim. That is the whole
token argument: nine new transactions across three estates cost nine formatted
lines, not nine paragraphs of a model deciding again how to write a price.

Two things are deliberately kept apart from each other:

* **The new transactions** are exactly what the operator asked to be told about
  -- their 間隔, their 面積, their 成交價 and their 呎價(實).
* **The trend** is the whole estate, matched or not, because a median over the
  handful of transactions passing a narrow filter is not a market level.

A reader who is shown both and told which is which can hold them at once. A
reader shown one labelled as the other cannot, which is why every trend line
names its sample size.
"""

from __future__ import annotations

from datetime import date

from . import clock, db, fmt, render, trend
from .errors import NotFoundError
from .models import DEAL_TYPES, bedroom_label

# A page of new transactions is a digest; a hundred of them is a data dump that
# gets scrolled past. Beyond this the payload says how many were held back, and
# they stay pending for the next run rather than being dropped.
DEFAULT_ROW_CAP = 60


def _row_cells(row: dict) -> dict:
    """One transaction as the six columns the table draws."""
    deal_type = row["deal_type"]
    return {
        "date": row["ins_date"],
        "unit": " ".join(part for part in (row["building"], row["floor"], row["unit"]) if part) or "—",
        "bedrooms": bedroom_label(row["bedrooms"]),
        "area": fmt.area(row["saleable_area"]),
        "price": fmt.price(row["price"], deal_type),
        "unit_price": fmt.unit_price(row["saleable_unit_price"], deal_type),
    }


def row_payload(row: dict) -> dict:
    """One transaction as the agent sees it. Every field already formatted."""
    deal_type = row["deal_type"]
    return {
        "id": row["id"],
        "tx_id": row["tx_id"],
        "estate": row["estate"],
        "deal_type": deal_type,
        "ins_date": row["ins_date"],
        "reg_date": row["reg_date"],
        "unit": _row_cells(row)["unit"],
        "bedrooms": row["bedrooms"],
        "bedroom_label": bedroom_label(row["bedrooms"]),
        "saleable_area": row["saleable_area"],
        "saleable_area_text": fmt.area(row["saleable_area"]),
        "price": row["price"],
        "price_text": fmt.price(row["price"], deal_type),
        "saleable_unit_price": row["saleable_unit_price"],
        "unit_price_text": fmt.unit_price(row["saleable_unit_price"], deal_type),
        "size_range": row["size_range"],
        "area_missing": bool(row["area_missing"]),
        "match_reason": row["match_reason"],
        "data_source": row["data_source"],
        "detail_url": row["detail_url"],
        "line": (
            f"{row['ins_date']}　{_row_cells(row)['unit']}　"
            f"{bedroom_label(row['bedrooms'])}　{fmt.area(row['saleable_area'])}　"
            f"{fmt.price(row['price'], deal_type)}　"
            f"{fmt.unit_price(row['saleable_unit_price'], deal_type)}"
        ),
    }


def _group(rows: list[dict], config) -> list[dict]:
    """Rows into 買賣/租賃 → estate → 間隔 → 面積, in config order throughout."""
    order = {entry.name: index for index, entry in enumerate(config.estates)}
    groups: list[dict] = []

    for deal_type in DEAL_TYPES:
        side = [row for row in rows if row["deal_type"] == deal_type]
        if not side:
            continue

        estates: list[dict] = []
        names = sorted({row["estate"] for row in side}, key=lambda name: order.get(name, 999))
        for name in names:
            entry = config.entry(name)
            display = entry.display if entry else name
            here = [row for row in side if row["estate"] == name]

            complete = [row for row in here if not row["area_missing"]]
            pending_area = [row for row in here if row["area_missing"]]

            bedroom_groups: list[dict] = []
            counts = sorted({row["bedrooms"] for row in complete}, key=lambda x: (x is None, x))
            for count in counts:
                by_count = [row for row in complete if row["bedrooms"] == count]
                band_order = (
                    [band.label for band in entry.size_ranges] if entry else []
                )
                bands = sorted(
                    {row["size_range"] for row in by_count},
                    key=lambda label: band_order.index(label) if label in band_order else 999,
                )
                bedroom_groups.append({
                    "bedrooms": count,
                    "bedroom_label": bedroom_label(count),
                    "size_groups": [
                        {
                            "size_range": band,
                            "size_label": band or "不限面積",
                            "items": [row_payload(row) for row in by_count
                                      if row["size_range"] == band],
                        }
                        for band in bands
                    ],
                })

            estates.append({
                "estate": name,
                "display": display,
                "count": len(here),
                "bedroom_groups": bedroom_groups,
                "area_pending": [row_payload(row) for row in pending_area],
            })

        groups.append({
            "deal_type": deal_type,
            "deal_label": fmt.deal_label(deal_type),
            "count": len(side),
            "estates": estates,
        })
    return groups


def _sections(group: dict) -> list[dict]:
    """One deal type's groups flattened into the table's section list."""
    sections: list[dict] = []
    for estate in group["estates"]:
        for bedroom_group in estate["bedroom_groups"]:
            for size_group in bedroom_group["size_groups"]:
                sections.append({
                    "heading": (
                        f"{estate['display']}　·　{bedroom_group['bedroom_label']}"
                        f"　·　{size_group['size_label']}　({len(size_group['items'])})"
                    ),
                    "rows": [_row_cells_from_payload(item) for item in size_group["items"]],
                })
        if estate["area_pending"]:
            sections.append({
                "heading": (
                    f"{estate['display']}　·　面積待補"
                    f"（來源未公布實用面積，不計入呎價）　({len(estate['area_pending'])})"
                ),
                "rows": [_row_cells_from_payload(item) for item in estate["area_pending"]],
                "pending": True,
            })
    return sections


def _row_cells_from_payload(item: dict) -> dict:
    return {
        "date": item["ins_date"],
        "unit": item["unit"],
        "bedrooms": item["bedroom_label"],
        "area": item["saleable_area_text"],
        "price": item["price_text"],
        "unit_price": item["unit_price_text"],
    }


def _summary_lines(groups: list[dict], trends: list[dict], today: date, held_back: int) -> list[str]:
    total = sum(group["count"] for group in groups)
    counts = "、".join(f"{group['deal_label']} {group['count']} 宗" for group in groups)
    lines = [f"{today.isoformat()} 新增成交 {total} 宗（{counts}）。"]

    for group in groups:
        lines.append(f"【{group['deal_label']}】")
        for estate in group["estates"]:
            for bedroom_group in estate["bedroom_groups"]:
                for size_group in bedroom_group["size_groups"]:
                    lines.append(
                        f"{estate['display']}　{bedroom_group['bedroom_label']}　"
                        f"{size_group['size_label']}"
                    )
                    lines.extend(f"　　{item['line']}" for item in size_group["items"])
            if estate["area_pending"]:
                lines.append(f"{estate['display']}　面積待補（來源未公布實用面積）")
                lines.extend(f"　　{item['line']}" for item in estate["area_pending"])

    if trends:
        lines.append("【呎價(實)走勢．全屋苑成交】")
        lines.extend(trend.summarise(item) for item in trends)

    if held_back:
        lines.append(
            f"另有 {held_back} 宗未列出，仍在待報名單內，下次運行會再提供。"
        )
    return lines


def build(
    config,
    *,
    commit: bool = False,
    limit: int | None = None,
    out_dir=None,
    conn=None,
    draw: bool = True,
) -> dict:
    """The report on everything matched and not yet delivered.

    ``commit`` stamps the transactions as reported *before* returning them, so
    the caller runs it and then sends. If the send fails, say so -- the rows are
    recoverable with ``transactions --estate``, but they will not come round
    again by themselves.
    """
    owned = conn is None
    conn = conn or db.connect()
    today = clock.today()
    try:
        cap = DEFAULT_ROW_CAP if limit is None else limit
        pending_total = db.pending_count(conn)
        rows = db.pending(conn, limit=cap)
        held_back = max(0, pending_total - len(rows))

        if not rows:
            return {
                "ok": True,
                "generated_at": clock.now().isoformat(),
                "new_count": 0,
                "groups": [],
                "trends": [],
                "summary_lines": [],
                "images": [],
                "committed": False,
                "note": "沒有新成交。無需發送訊息。",
            }

        groups = _group(rows, config)

        # The trend is estate-wide, so it is computed for every bucket that has
        # news -- not for every bucket in the config. An estate with nothing new
        # this run does not get a paragraph about its unchanged median.
        trends = [
            trend.bucket_trend(
                conn, estate["estate"], group["deal_type"], today,
                window_days=config.trend_window_days,
                min_samples=config.trend_min_samples,
                label=estate["display"],
            )
            for group in groups for estate in group["estates"]
        ]

        images: list[dict] = []
        font_used = None
        if draw:
            target = render.prepare(out_dir)
            render.sweep(target)
            font_used = render.cjk_font()
            for group in groups:
                sections = _sections(group)
                if sections:
                    path = render.render_table(
                        sections, group["deal_type"],
                        title=f"{group['deal_label']}　新增成交　{today.isoformat()}",
                        subtitle=f"共 {group['count']} 宗　·　按屋苑、間隔、面積(實)分組",
                        out_dir=target,
                    )
                    images.append({
                        "kind": "table",
                        "deal_type": group["deal_type"],
                        "label": f"{group['deal_label']}新增成交",
                        "path": str(path),
                    })
                for estate in group["estates"]:
                    series = trend.monthly_series(
                        conn, estate["estate"], group["deal_type"], today,
                        months=config.chart_months, label=estate["display"],
                    )
                    if len(series["points"]) < config.chart_min_points:
                        continue
                    path = render.render_chart(series, out_dir=target)
                    images.append({
                        "kind": "chart",
                        "deal_type": group["deal_type"],
                        "estate": estate["estate"],
                        "label": f"{estate['display']} {group['deal_label']} 呎價(實)走勢",
                        "path": str(path),
                        "points": len(series["points"]),
                    })

        committed = 0
        if commit:
            committed = db.mark_reported(conn, [row["id"] for row in rows], clock.now())

        return {
            "ok": True,
            "generated_at": clock.now().isoformat(),
            "new_count": len(rows),
            "pending_total": pending_total,
            "held_back": held_back,
            "groups": groups,
            "trends": trends,
            "summary_lines": _summary_lines(groups, trends, today, held_back),
            "images": images,
            "committed": bool(commit),
            "committed_rows": committed,
            "cjk_font": font_used,
        }
    finally:
        if owned:
            conn.close()


def history(
    config,
    estate: str,
    deal_type: str,
    *,
    months: int | None = None,
    limit: int = 20,
    conn=None,
    draw: bool = False,
    out_dir=None,
) -> dict:
    """Past numbers for one bucket, for answering a question in chat.

    Read-only and stamps nothing: asking what 泓都 rentals have been doing must
    never consume a pending report.
    """
    if deal_type not in DEAL_TYPES:
        raise NotFoundError(
            f"deal type must be one of {list(DEAL_TYPES)}, got {deal_type!r}",
            deal_type=deal_type,
        )
    entry = config.entry(estate)
    owned = conn is None
    conn = conn or db.connect()
    today = clock.today()
    try:
        rows = db.query(conn, estate=estate, deal_type=deal_type, limit=limit)
        totals = next(
            (row for row in db.buckets(conn)
             if row["estate"] == estate and row["deal_type"] == deal_type),
            None,
        )
        if totals is None:
            known = sorted({row["estate"] for row in db.buckets(conn)})
            raise NotFoundError(
                f"nothing recorded for {estate!r} {deal_type} yet",
                estate=estate, deal_type=deal_type, recorded_estates=known,
            )

        movement = trend.bucket_trend(
            conn, estate, deal_type, today,
            window_days=config.trend_window_days,
            min_samples=config.trend_min_samples,
            label=entry.display if entry else estate,
        )
        series = trend.monthly_series(
            conn, estate, deal_type, today,
            months=months or config.chart_months,
            label=entry.display if entry else estate,
        )

        images = []
        if draw and len(series["points"]) >= config.chart_min_points:
            images.append({
                "kind": "chart",
                "estate": estate,
                "deal_type": deal_type,
                "path": str(render.render_chart(series, out_dir=out_dir)),
            })

        return {
            "ok": True,
            "estate": estate,
            "display": entry.display if entry else estate,
            "deal_type": deal_type,
            "deal_label": fmt.deal_label(deal_type),
            "archive": totals,
            "trend": movement,
            "trend_line": trend.summarise(movement),
            "monthly": series["points"],
            "transactions": [row_payload(row) for row in rows],
            "images": images,
        }
    finally:
        if owned:
            conn.close()
