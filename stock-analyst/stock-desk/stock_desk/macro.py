"""Rates, the curve, and the prints that move them.

Macro enters this desk as a *conviction* input, never a direction one. It is a
reason to wait for a trigger that would otherwise be taken, or to distrust a
breakout -- very rarely a reason to buy anything. So this module's job is not to
have a view. It is to notice when a number moved enough to matter and to say so
once.

## Reported on change, never on a schedule

The failure mode this exists to avoid is the obvious one: query the 10-year on a
daily cron, print it every morning, and within a fortnight the reader skips the
macro section forever. So ``macro.notified_at`` works exactly like
``news.notified_at`` -- the refresher writes every reading it sees and says
nothing; a reading is only surfaced when it has moved past the last *reported*
one by more than that series' threshold.

Comparing against the last reported reading rather than the previous day's is
deliberate. Three consecutive four-basis-point days are a twelve-basis-point
move, and a day-over-day comparison reports none of them.

## Thresholds are absolute, not proportional

``MacroSeries.move`` is in the series' own units. Ten basis points on the
10-year is the same event whether the yield is 2% or 5%; a percentage-of-level
threshold would make an identical move register differently in different
regimes, which is backwards.

## The call budget is the binding constraint

Alpha Vantage's free tier allows 25 calls a day for the whole profile, shared
with news. Six series refreshed twice daily would spend twelve of them to learn
that three monthly figures had not changed. Each series therefore declares how
stale it may get, and :func:`refresh` skips anything read recently and stops
dead when its budget runs out -- returning what it spent so the caller can keep
the ledger.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from .errors import FetchError
from .feeds import ALPHAVANTAGE
from .models import MacroReading, MacroSeries, MacroSettings
from .providers import mcp_client

# Yields move daily and are the reason this module exists. The monthly figures
# change twelve times a year, so refreshing them hourly buys nothing and costs
# the news poller its budget.
DEFAULT_SERIES: tuple[MacroSeries, ...] = (
    MacroSeries(
        key="ust_2y",
        tool="treasury_yield",
        label="US 2-year",
        unit="percent",
        move=0.10,
        arguments={"maturity": "2year", "interval": "daily"},
    ),
    MacroSeries(
        key="ust_10y",
        tool="treasury_yield",
        label="US 10-year",
        unit="percent",
        move=0.10,
        arguments={"maturity": "10year", "interval": "daily"},
    ),
    MacroSeries(
        key="ust_30y",
        tool="treasury_yield",
        label="US 30-year",
        unit="percent",
        move=0.10,
        arguments={"maturity": "30year", "interval": "daily"},
    ),
    MacroSeries(
        key="fed_funds",
        tool="federal_funds_rate",
        label="Fed funds",
        unit="percent",
        # A quarter point. The Fed does not move in smaller increments, so a
        # lower threshold can only report rounding in the effective rate.
        move=0.25,
        arguments={"interval": "monthly"},
    ),
    MacroSeries(
        key="cpi",
        tool="cpi",
        label="US CPI",
        unit="index",
        move=0.5,
        arguments={"interval": "monthly"},
    ),
    MacroSeries(
        key="unemployment",
        tool="unemployment",
        label="US unemployment",
        unit="percent",
        move=0.2,
        arguments={},
    ),
)

# How stale each series may get before it is worth a call.
REFRESH_HOURS: dict[str, int] = {
    "ust_2y": 6,
    "ust_10y": 6,
    "ust_30y": 6,
    "fed_funds": 24,
    "cpi": 24,
    "unemployment": 24,
}

DAILY_KEYS = frozenset({"ust_2y", "ust_10y", "ust_30y"})


def series_for(settings: MacroSettings | None = None) -> tuple[MacroSeries, ...]:
    """The tracked series, with any operator threshold overrides applied."""
    if settings is None or not settings.moves:
        return DEFAULT_SERIES
    overrides = settings.moves
    return tuple(
        MacroSeries(
            key=s.key,
            tool=s.tool,
            label=s.label,
            unit=s.unit,
            move=float(overrides.get(s.key, s.move)),
            arguments=dict(s.arguments),
        )
        for s in DEFAULT_SERIES
    )


def _value(raw) -> float | None:
    """Alpha Vantage writes a missing observation as ``"."``, not as null."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == ".":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _newest(payload: dict) -> tuple[str, float] | None:
    """The most recent usable ``(date, value)`` in a series response.

    The vendor returns newest-first, but that is not promised anywhere, so the
    maximum date is taken rather than the first row. A run of missing
    observations at the head is normal around a holiday.
    """
    rows = payload.get("data") or []
    best: tuple[str, float] | None = None
    for row in rows:
        day = str(row.get("date", "")).strip()
        value = _value(row.get("value"))
        if not day or value is None:
            continue
        if best is None or day > best[0]:
            best = (day, value)
    return best


def _last_fetch(conn: sqlite3.Connection, key: str) -> datetime | None:
    row = conn.execute(
        "SELECT MAX(fetched_at) AS t FROM macro WHERE series = ?", (key,)
    ).fetchone()
    if row is None or not row["t"]:
        return None
    try:
        return datetime.fromisoformat(row["t"])
    except (TypeError, ValueError):
        return None


def is_stale(conn: sqlite3.Connection, key: str, now: datetime) -> bool:
    last = _last_fetch(conn, key)
    if last is None:
        return True
    return (now - last) >= timedelta(hours=REFRESH_HOURS.get(key, 24))


def refresh(
    conn: sqlite3.Connection,
    series: tuple[MacroSeries, ...],
    now: datetime,
    budget: int,
    force: bool = False,
) -> dict:
    """Read every stale series the budget allows. Writes rows; tells nobody.

    Stops when the budget is exhausted and names what it skipped, because a
    macro section that quietly covers four of six series looks identical to one
    where the other two did not move.
    """
    wanted = [s for s in series if force or is_stale(conn, s.key, now)]
    fresh = [s.key for s in series if s not in wanted]
    affordable = wanted[: max(0, budget)]
    deferred = [s.key for s in wanted[max(0, budget) :]]

    if not affordable:
        return {
            "status": "skipped",
            "calls": 0,
            "stored": 0,
            "fresh": fresh,
            "deferred": deferred,
            "failures": [],
        }

    calls = [(s.tool, dict(s.arguments)) for s in affordable]
    try:
        payloads = mcp_client.call_batch(ALPHAVANTAGE, calls)
    except FetchError as exc:
        return {
            "status": "error",
            "calls": 0,
            "stored": 0,
            "fresh": fresh,
            "deferred": deferred,
            "failures": [{"server": ALPHAVANTAGE, "error": exc.message}],
        }

    stored = 0
    failures: list[dict] = []
    for spec, payload in zip(affordable, payloads):
        if not isinstance(payload, dict):
            failures.append({"series": spec.key, "error": "unexpected payload shape"})
            continue
        note = payload.get("Information") or payload.get("Note") or payload.get("Error Message")
        if note:
            failures.append(
                {"series": spec.key, "error": mcp_client.redact(str(note))[:200], "quota": True}
            )
            continue
        newest = _newest(payload)
        if newest is None:
            failures.append({"series": spec.key, "error": "no usable observation"})
            continue
        day, value = newest
        cursor = conn.execute(
            """INSERT INTO macro (series, as_of, value, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (series, as_of) DO UPDATE SET fetched_at = excluded.fetched_at""",
            (spec.key, day, value, now.isoformat()),
        )
        stored += 1 if cursor.rowcount else 0

    if failures and stored == 0:
        status = "error"
    elif failures:
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "calls": len(affordable),
        "stored": stored,
        "fresh": fresh,
        "deferred": deferred,
        "failures": failures,
    }


def latest(conn: sqlite3.Connection, key: str) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT as_of, value FROM macro WHERE series = ? ORDER BY as_of DESC LIMIT 1", (key,)
    ).fetchone()
    return None if row is None else (row["as_of"], float(row["value"]))


def _last_reported(conn: sqlite3.Connection, key: str) -> tuple[str, float] | None:
    row = conn.execute(
        """SELECT as_of, value FROM macro
           WHERE series = ? AND notified_at IS NOT NULL
           ORDER BY as_of DESC LIMIT 1""",
        (key,),
    ).fetchone()
    return None if row is None else (row["as_of"], float(row["value"]))


def pending(
    conn: sqlite3.Connection, series: tuple[MacroSeries, ...]
) -> list[MacroReading]:
    """Series that have moved past the last reported level by more than ``move``.

    A series never reported before is *not* pending. The first sight of a level
    is a starting point, not a move -- announcing "the 10-year is 4.65%" on day
    one tells the reader nothing they can act on, and burns the one thing this
    section has, which is that it only speaks when something happened.
    """
    moved: list[MacroReading] = []
    for spec in series:
        newest = latest(conn, spec.key)
        if newest is None:
            continue
        day, value = newest
        baseline = _last_reported(conn, spec.key)
        if baseline is None:
            continue
        if abs(value - baseline[1]) + 1e-12 < spec.move:
            continue
        moved.append(
            MacroReading(
                series=spec.key,
                as_of=day,
                value=value,
                label=spec.label,
                unit=spec.unit,
                previous=baseline[1],
            )
        )
    return moved


def seed(conn: sqlite3.Connection, series: tuple[MacroSeries, ...], now: datetime) -> int:
    """Stamp the current level of every series as reported, saying nothing.

    The macro equivalent of the silent first poll. Without it the first report
    after setup either says nothing at all (every series lacks a baseline) or,
    worse, announces six levels as though they were six events.
    """
    stamped = 0
    for spec in series:
        newest = latest(conn, spec.key)
        if newest is None:
            continue
        cursor = conn.execute(
            "UPDATE macro SET notified_at = ? WHERE series = ? AND as_of = ? AND notified_at IS NULL",
            (now.isoformat(), spec.key, newest[0]),
        )
        stamped += cursor.rowcount or 0
    return stamped


def curve(conn: sqlite3.Connection) -> dict | None:
    """The 2s10s spread, and whether it is inverted.

    Derived, not observed: it is a subtraction of two readings this desk stored,
    and is labelled as such wherever it is shown. Both legs must come from the
    same date or the spread is comparing two different days and means nothing.
    """
    two, ten = latest(conn, "ust_2y"), latest(conn, "ust_10y")
    if two is None or ten is None or two[0] != ten[0]:
        return None
    spread = ten[1] - two[1]
    return {
        "as_of": ten[0],
        "spread_bp": round(spread * 100, 1),
        "inverted": spread < 0,
        "two_year": two[1],
        "ten_year": ten[1],
        "basis": "derived: 10-year minus 2-year, same session",
    }


def line(reading: MacroReading) -> str:
    """One finished sentence, relayed verbatim.

    Yields are quoted in basis points because that is how they are discussed;
    an index level is quoted in its own units because basis points of a CPI
    index would be meaningless.
    """
    change = reading.change or 0.0
    if reading.unit == "percent":
        moved = f"{change * 100:+.0f}bp"
        level = f"{reading.value:.2f}%"
        was = f"{reading.previous:.2f}%" if reading.previous is not None else "?"
    else:
        moved = f"{change:+.2f}"
        level = f"{reading.value:.2f}"
        was = f"{reading.previous:.2f}" if reading.previous is not None else "?"
    return f"{reading.label} {level} ({reading.as_of}), {moved} from {was} when last reported."


def mark_notified(
    conn: sqlite3.Connection, readings: list[MacroReading], now: datetime
) -> int:
    """Stamp reported readings. Call **after** the message is sent."""
    if not readings:
        return 0
    conn.executemany(
        "UPDATE macro SET notified_at = ? WHERE series = ? AND as_of = ?",
        [(now.isoformat(), r.series, r.as_of) for r in readings],
    )
    return len(readings)


def snapshot(conn: sqlite3.Connection, series: tuple[MacroSeries, ...]) -> dict:
    """Every current level, reported or not. The on-demand read path.

    Answering "where are rates" must not consume anything the next scheduled
    macro section would have carried, so nothing here stamps anything.
    """
    levels = {}
    for spec in series:
        newest = latest(conn, spec.key)
        if newest is not None:
            levels[spec.key] = {
                "label": spec.label,
                "as_of": newest[0],
                "value": newest[1],
                "unit": spec.unit,
            }
    return {"levels": levels, "curve": curve(conn)}
