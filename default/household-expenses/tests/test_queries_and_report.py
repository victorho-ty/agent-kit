from expense_tracker import ingest, queries, report

MESSAGES = [
    ("Alice", "rent $12,000", "2026-06-01T09:00:00"),
    ("Alice", "haircut $300; dinner $50", "2026-07-19T21:30:00"),
    ("Alice", "groceries $480; taxi $92", "2026-07-19T11:00:00"),
    ("Bob", "movie $220; popcorn $60", "2026-07-20T19:00:00"),
    ("Bob", "gym $500", "2026-07-02T08:00:00"),
    ("Alice", "MTR $5.6; bus $4.8", "2026-07-21T08:30:00"),
]


def _seed(conn):
    for member, text, ts in MESSAGES:
        ingest.ingest_message(conn, member=member, text=text, timestamp=ts)


def test_month_summary_splits_by_category_and_member(conn):
    _seed(conn)

    summary = queries.month_summary(conn, "2026-07")

    assert summary["total"] == 1712.4
    assert summary["by_category"][0] == {"category": "Food & Drinks", "total": 530.0, "n": 2, "pct": 31.0}
    assert {row["member"] for row in summary["by_member"]} == {"Alice", "Bob"}
    assert sum(row["pct"] for row in summary["by_category"]) == 100.0


def test_month_summary_filters_by_member(conn):
    _seed(conn)

    assert queries.month_summary(conn, "2026-07", "Bob")["total"] == 780.0


def test_top_days_are_ranked_and_broken_down_by_category(conn):
    _seed(conn)

    days = queries.top_days(conn, "2026-07", limit=5)

    assert days[0]["day"] == "2026-07-19"
    assert days[0]["by_category"] == {"Food & Drinks": 530.0, "Beauty": 300.0, "Transportation": 92.0}
    assert [d["total"] for d in days] == sorted((d["total"] for d in days), reverse=True)


def test_year_months_covers_only_the_requested_year(conn):
    _seed(conn)

    months = queries.year_months(conn, "2026")

    assert [m["month"] for m in months] == ["2026-06", "2026-07"]
    assert months[0]["total"] == 12000.0


def test_year_months_by_category_breaks_each_month_down(conn):
    _seed(conn)

    months = queries.year_months_by_category(conn, "2026")

    assert [m["month"] for m in months] == ["2026-06", "2026-07"]
    assert months[0]["by_category"] == {"Housing & Utilities": 12000.0}
    assert months[0]["total"] == 12000.0
    july = months[1]
    assert july["by_category"]["Food & Drinks"] == 530.0
    assert july["by_category"]["Transportation"] == 102.4
    assert july["total"] == 1712.4


def test_report_renders_a_png(conn, tmp_path):
    _seed(conn)

    path = report.build_report(conn, "2026-07", out_path=tmp_path / "report.png")

    assert path.exists()
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert path.stat().st_size > 20_000


def test_report_renders_for_an_empty_month(conn, tmp_path):
    path = report.build_report(conn, "2026-01", out_path=tmp_path / "empty.png")

    assert path.exists()
