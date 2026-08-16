"""Earnings and ex-dividend dates: detected on a schedule, alerted once.

Same shape as :mod:`news`. :func:`refresh` runs daily and writes rows;
:func:`pending` returns only what has not been notified. The ``UNIQUE (ticker,
kind, event_date)`` constraint means a date re-confirmed on twenty consecutive
days is one row, and ``notified_at`` means it is announced once -- not every
morning for ten days running, which is what a naive "within 10 days" query does.

A moved date is genuinely new. If a company shifts earnings from the 12th to the
15th, that is a different ``event_date``, so it inserts, and it alerts again --
which is correct, because the change is the news.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime

from . import settings
from .errors import FetchError
from .models import CorporateEvent
from .providers import PRIMARY

KINDS = ("earnings", "ex_dividend")

LABELS = {"earnings": "earnings", "ex_dividend": "ex-dividend"}


def refresh(
    conn: sqlite3.Connection,
    tickers: list[str],
    now: datetime,
    delay: float | None = None,
) -> dict:
    """Pull the calendar for each ticker and store anything not already known."""
    pause = settings.request_delay() if delay is None else delay
    inserted = 0
    failures: list[dict] = []

    for index, ticker in enumerate(tickers):
        if index and pause:
            time.sleep(pause)
        try:
            events = PRIMARY.corporate_events(ticker)
        except FetchError as exc:
            failures.append({"ticker": ticker, "error": exc.message})
            continue
        inserted += store(conn, events, now)

    if not tickers:
        status = "skipped"
    elif len(failures) == len(tickers):
        status = "error"
    elif failures:
        status = "partial"
    else:
        status = "ok"

    return {"status": status, "tickers": len(tickers), "new": inserted, "failures": failures}


def store(conn: sqlite3.Connection, events: list[CorporateEvent], now: datetime) -> int:
    inserted = 0
    for event in events:
        cursor = conn.execute(
            """
            INSERT INTO events (ticker, kind, event_date, detail, detected_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (ticker, kind, event_date) DO NOTHING
            """,
            (
                event.ticker,
                event.kind,
                event.event_date.isoformat(),
                event.detail,
                now.isoformat(),
            ),
        )
        inserted += cursor.rowcount if cursor.rowcount > 0 else 0
    return inserted


def pending_count(conn: sqlite3.Connection, today: date, within_days: int = 10,
                  tickers: list[str] | None = None) -> int:
    """How many unannounced events fall inside the horizon. The alert gate."""
    return len(pending(conn, today, within_days, tickers))


def pending(
    conn: sqlite3.Connection,
    today: date,
    within_days: int = 10,
    tickers: list[str] | None = None,
) -> list[CorporateEvent]:
    """Unnotified events between today and ``within_days`` out, soonest first.

    Past events are excluded rather than deleted: an earnings date that came and
    went without being announced is no longer worth interrupting anybody for, but
    the row is still the record that it was known about.
    """
    horizon = today.toordinal() + within_days
    params: list = [today.isoformat()]
    sql = "SELECT * FROM events WHERE notified_at IS NULL AND event_date >= ?"
    if tickers:
        sql += f" AND ticker IN ({','.join('?' * len(tickers))})"
        params.extend(tickers)
    sql += " ORDER BY event_date, ticker"

    found: list[CorporateEvent] = []
    for row in conn.execute(sql, params).fetchall():
        event_date = date.fromisoformat(row["event_date"])
        if event_date.toordinal() > horizon:
            continue
        found.append(
            CorporateEvent(
                ticker=row["ticker"],
                kind=row["kind"],
                event_date=event_date,
                days_away=(event_date - today).days,
                detail=row["detail"],
            )
        )
    return found


def mark_notified(conn: sqlite3.Connection, events: list[CorporateEvent], now: datetime) -> int:
    if not events:
        return 0
    conn.executemany(
        """UPDATE events SET notified_at = ?
           WHERE ticker = ? AND kind = ? AND event_date = ? AND notified_at IS NULL""",
        [
            (now.isoformat(), event.ticker, event.kind, event.event_date.isoformat())
            for event in events
        ],
    )
    return len(events)


def describe(event: CorporateEvent) -> str:
    """The one-line rendering, built in Python and relayed verbatim."""
    label = LABELS.get(event.kind, event.kind)
    when = (
        "today"
        if event.days_away == 0
        else "tomorrow"
        if event.days_away == 1
        else f"in {event.days_away} days"
    )
    line = f"{event.ticker} — {label} {when} ({event.event_date.isoformat()})"
    return f"{line}, {event.detail}" if event.detail else line
