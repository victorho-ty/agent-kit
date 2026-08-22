"""The two things this bundle actually does: the daily check, and the report.

They are deliberately separate commands, and the separation is the whole design.

``check`` runs every day on cron, costs one 3KB GET, and says nothing. It fetches
the index page, compares the published quarter against the history CSV, and on
the one day in ninety when a new quarter appears it downloads the PDF, extracts
the figures and writes the row. Then it stops. It draws no images and wakes
nobody; it only leaves a pending quarter behind.

``report`` renders the images and returns the payload to send. It is invoked by
the agent -- either because ``check`` left something pending, or because somebody
asked for the current picture. ``--commit`` stamps the delivery ledger.

Detection is polled; alerting is driven by the ledger. They are coupled through
a file rather than through timing, so the daily check can run at any hour, a
missed day costs nothing, and a send that failed is still pending tomorrow.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from . import clock, extract, fetch, history, render, settings, state
from .errors import NotFoundError

# How long after a quarter ends before its absence is worth mentioning. The
# Housing Bureau has published about two months after the quarter end; 100 days
# is late enough that a real lapse is not called early, and early enough that a
# series which quietly stopped is noticed within the same quarter.
PUBLICATION_LAG_DAYS = 100

# How far the PDF's printed total may sit from the sum of its three components
# and still be treated as the same measurement. Each part is rounded to the
# nearest thousand independently, so a gap of a step or two is expected; a gap
# of tens of thousands is a misread row.
TOTAL_TOLERANCE = 3000


def pdf_name(quarter: str) -> str:
    year, month = history.parse_quarter(quarter)
    return f"stat{year}{month:02d}.pdf"


def _source(quarter: str, publication=None) -> dict:
    return {
        "index_url": settings.index_url(),
        "pdf_url": (publication.url if publication is not None
                    else settings.pdf_base_url() + pdf_name(quarter)),
        "published_label": publication.label if publication is not None else None,
        "publisher": "Housing Bureau, HKSAR Government",
    }


def is_overdue(latest_quarter: str, today: date) -> bool:
    """True when the quarter after ``latest_quarter`` is late enough to remark on."""
    due = history.quarter_end_date(history.next_quarter(latest_quarter))
    return today > due + timedelta(days=PUBLICATION_LAG_DAYS)


# ------------------------------------------------------------------ the check


def check(*, download: bool = True, history_path: Path | None = None,
          state_path: Path | None = None, runs_path: Path | None = None) -> dict:
    """Look at the index page, and write the row if a new quarter has appeared.

    Raises on failure, having recorded the failure in the run log first -- so the
    liveness record covers the days this could not run, which are exactly the
    days nobody would otherwise hear about.
    """
    when = clock.now()
    try:
        publication = fetch.latest_publication()
    except Exception as exc:
        streak = state.note_failure(state_path)
        state.record_run(
            {"at": when.isoformat(), "status": "error",
             "message": str(exc), "consecutive_failures": streak},
            runs_path,
        )
        raise

    rows = history.read(history_path)
    # Before the append, never after: a quarter published on the day this is
    # first installed must still be reported, not absorbed with the back
    # catalogue it arrived alongside.
    seeded = state.ensure_seeded(rows, when, state_path)

    quarter = publication.quarter
    is_new = not history.has_quarter(rows, quarter)
    figures = None
    printed_total = None

    if is_new and download:
        try:
            figures, printed_total = extract.parse_pdf(fetch.download_pdf(publication))
        except Exception as exc:
            streak = state.note_failure(state_path)
            state.record_run(
                {"at": when.isoformat(), "status": "error", "quarter": quarter,
                 "message": str(exc), "consecutive_failures": streak},
                runs_path,
            )
            raise
        # The Bureau's own headline total when it prints one and it is close
        # enough to the components to be the same measurement; otherwise the sum.
        # A printed total further out than a few rounding steps means a figure
        # was read off the wrong row, and the sum is the safer of two suspects.
        stored_total = None
        if printed_total is not None and abs(printed_total - figures.total) <= TOTAL_TOLERANCE:
            stored_total = printed_total
        rows = history.append(quarter, figures, history_path, total=stored_total)

    state.note_success(state_path)
    waiting = state.pending(rows, state_path)
    latest = history.latest(rows)
    overdue = is_overdue(latest.quarter, when.date())

    payload = {
        "ok": True,
        "checked_at": when.isoformat(),
        "published_quarter": quarter,
        "latest_in_history": latest.quarter,
        "new_quarter": bool(is_new and figures is not None),
        "appended": bool(is_new and figures is not None),
        "figures": figures.as_dict() if figures is not None else None,
        "printed_total": printed_total,
        "total_matches_printed": (
            None if printed_total is None or figures is None
            else printed_total == figures.total
        ),
        "pending": len(waiting),
        "pending_quarters": waiting,
        "overdue": overdue,
        "seeded": seeded,
        "history_file": str(history_path or settings.history_file()),
        "source": _source(quarter, publication),
    }
    if waiting:
        payload["next_command"] = "hk-supply report --commit"

    state.record_run(
        {"at": when.isoformat(),
         "status": "new_quarter" if payload["new_quarter"] else "ok",
         "quarter": quarter, "pending": len(waiting), "overdue": overdue},
        runs_path,
    )
    return payload


# ----------------------------------------------------------------- the report


def _pct_phrase(entry: dict) -> str:
    pct = entry.get("pct")
    if pct is None:
        return "no prior quarter to compare"
    if entry.get("direction") == "flat":
        return "unchanged QoQ"
    return f"{pct:+.2f}% QoQ"


def summary_lines(quarter: str, row, qoq: dict, prior: str | None) -> list[str]:
    """Finished strings for the message body. Relayed verbatim, never recomputed."""
    lines = [
        f"HK private residential primary-market supply — {quarter} "
        f"(香港私人住宅一手市場供應)",
        f"Total 未來三至四年潛在供應: {row.total:,} units, {_pct_phrase(qoq['total'])}",
    ]
    for key, chinese, english in render.COLUMNS:
        lines.append(
            f"{english} ({chinese}): {getattr(row, key):,} units, {_pct_phrase(qoq[key])}"
        )
    if prior:
        lines.append(f"QoQ is against {prior}.")
    return lines


def build(*, quarter: str | None = None, commit: bool = False,
          limit: int | None = None, out_dir: Path | None = None,
          history_path: Path | None = None, state_path: Path | None = None,
          publication=None) -> dict:
    """Render the images for one quarter and return everything needed to send it."""
    rows = history.read(history_path)
    target = quarter or history.latest(rows).quarter

    row = history.find(rows, target)
    if row is None:
        raise NotFoundError(
            f"{target} is not in the history",
            quarter=target,
            available=[item.quarter for item in rows[:8]],
        )

    count = limit if limit is not None else settings.table_quarters()
    table_rows = history.table(rows, count, end_quarter=target)
    qoq = history.quarter_on_quarter(rows, target)
    prior = history.previous_quarter(target)
    has_prior = history.has_quarter(rows, prior)

    # The charts stop where the report's subject does, for the same reason the
    # table window does: a report headed "2023/Mar" whose trend lines run on to
    # 2026/Jun shows the reader three years the text never mentions. A no-op for
    # the usual case, where the subject is the newest quarter.
    cutoff = history.quarter_key(target)
    chart_rows = [row for row in rows if history.quarter_key(row.quarter) <= cutoff]

    images = render.render_all(chart_rows, table_rows, target, out_dir)

    already = state.is_reported(target, state_path)
    if commit:
        state.mark_reported(target, clock.now(), state_path)

    return {
        "ok": True,
        "quarter": target,
        "prior_quarter": prior if has_prior else None,
        "figures": row.figures.as_dict(),
        "qoq": qoq,
        "summary_lines": summary_lines(target, row, qoq, prior if has_prior else None),
        "table": table_rows,
        "table_quarters": len(table_rows),
        "images": images,
        "cjk_font": render.cjk_font(),
        "committed": bool(commit),
        "previously_reported": already,
        "history_file": str(history_path or settings.history_file()),
        "source": _source(target, publication),
    }
