"""Read-only aggregations over the expenses table."""

from __future__ import annotations

import sqlite3


def _filters(month: str | None, member: str | None, year: str | None) -> tuple[str, list]:
    clauses, params = [], []
    if month:
        clauses.append("substr(ts, 1, 7) = ?")
        params.append(month)
    if year:
        clauses.append("substr(ts, 1, 4) = ?")
        params.append(year)
    if member:
        clauses.append("member = ? COLLATE NOCASE")
        params.append(member)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def month_summary(conn: sqlite3.Connection, month: str, member: str | None = None) -> dict:
    where, params = _filters(month, member, None)

    total_row = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n FROM expenses{where}", params
    ).fetchone()

    by_category = [
        dict(r)
        for r in conn.execute(
            f"SELECT category, SUM(amount) AS total, COUNT(*) AS n FROM expenses{where}"
            " GROUP BY category ORDER BY total DESC",
            params,
        )
    ]
    by_member = [
        dict(r)
        for r in conn.execute(
            f"SELECT member, SUM(amount) AS total, COUNT(*) AS n FROM expenses{where}"
            " GROUP BY member ORDER BY total DESC",
            params,
        )
    ]

    total = total_row["total"]
    for row in by_category:
        row["pct"] = round(100 * row["total"] / total, 1) if total else 0.0
        row["total"] = round(row["total"], 2)
    for row in by_member:
        row["total"] = round(row["total"], 2)

    return {
        "month": month,
        "member": member,
        "total": round(total, 2),
        "count": total_row["n"],
        "by_category": by_category,
        "by_member": by_member,
    }


def top_days(conn: sqlite3.Connection, month: str, member: str | None = None, limit: int = 5) -> list[dict]:
    """Highest-spend days in the month, each broken down by category."""
    where, params = _filters(month, member, None)

    days = conn.execute(
        f"SELECT substr(ts, 1, 10) AS day, SUM(amount) AS total FROM expenses{where}"
        " GROUP BY day ORDER BY total DESC, day DESC LIMIT ?",
        [*params, limit],
    ).fetchall()

    result = []
    for day in days:
        breakdown = conn.execute(
            f"SELECT category, SUM(amount) AS total FROM expenses{where}"
            f"{' AND' if where else ' WHERE'} substr(ts, 1, 10) = ? GROUP BY category ORDER BY total DESC",
            [*params, day["day"]],
        ).fetchall()
        result.append(
            {
                "day": day["day"],
                "total": round(day["total"], 2),
                "by_category": {r["category"]: round(r["total"], 2) for r in breakdown},
            }
        )
    return result


def year_months(conn: sqlite3.Connection, year: str, member: str | None = None) -> list[dict]:
    where, params = _filters(None, member, year)
    rows = conn.execute(
        f"SELECT substr(ts, 1, 7) AS month, SUM(amount) AS total, COUNT(*) AS n FROM expenses{where}"
        " GROUP BY month ORDER BY month",
        params,
    ).fetchall()
    return [{"month": r["month"], "total": round(r["total"], 2), "count": r["n"]} for r in rows]


def year_months_by_category(conn: sqlite3.Connection, year: str, member: str | None = None) -> list[dict]:
    """Per-month category breakdown for every month in the year (stacked bars)."""
    where, params = _filters(None, member, year)
    rows = conn.execute(
        f"SELECT substr(ts, 1, 7) AS month, category, SUM(amount) AS total FROM expenses{where}"
        " GROUP BY month, category ORDER BY month, total DESC",
        params,
    ).fetchall()
    months: dict[str, dict] = {}
    for r in rows:
        entry = months.setdefault(r["month"], {"month": r["month"], "by_category": {}})
        entry["by_category"][r["category"]] = round(r["total"], 2)
    result = [months[m] for m in sorted(months)]
    for entry in result:
        entry["total"] = round(sum(entry["by_category"].values()), 2)
    return result


def list_expenses(
    conn: sqlite3.Connection, month: str | None = None, member: str | None = None, limit: int = 200
) -> list[dict]:
    where, params = _filters(month, member, None)
    rows = conn.execute(
        f"SELECT id, ts, member, description, category, amount, currency FROM expenses{where}"
        " ORDER BY ts DESC, id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [dict(r) for r in rows]


def members(conn: sqlite3.Connection) -> list[str]:
    return [r["member"] for r in conn.execute("SELECT DISTINCT member FROM expenses ORDER BY member")]
