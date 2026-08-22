"""Assembling the daily watchlist report.

The shape of this payload is the whole token argument. It has three parts:

* ``setups`` -- the few tickers that earned a paragraph. Usually zero to two.
  The agent writes prose for these and only these.
* ``status_lines`` -- one pre-rendered string per remaining ticker, built by
  :func:`stock_desk.setups.status_line` in Python. The agent relays them
  verbatim. Forty tickers cost forty strings and not one model token.
* ``fresh_news`` -- stories never reported before, clustered. Empty is the normal
  case, and an empty list means the section is **skipped entirely**, not written
  up as "no news".

A ticker earns a paragraph when it is `coiled`, `triggered` or `failed` **and**
its base formed inside its configured horizon. Out-of-horizon setups are real and
still detected -- they drop to a status line rather than vanishing, because a
four-month coil is a fact about the stock even when the operator has asked to
hear only about fresh ones.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from . import bars as bars_module
from . import db, events, macro, news, sector, setups
from .config.watchlist import WatchlistConfig
from .models import Setup, Thresholds

NARRATIVE_STAGES = frozenset({"coiled", "triggered", "failed"})


def thresholds_for(config: WatchlistConfig, ticker: str) -> Thresholds:
    """Per-ticker overrides on top of the shared defaults."""
    entry = config.find(ticker)
    if entry is None:
        return Thresholds()
    return Thresholds(
        technical_horizon_days=entry.technical_horizon_days,
        min_avg_dollar_volume=(
            entry.min_avg_dollar_volume
            if entry.min_avg_dollar_volume is not None
            else config.defaults.min_avg_dollar_volume
        ),
    )


def scan(
    conn: sqlite3.Connection,
    config: WatchlistConfig,
    commit: bool = False,
) -> list[Setup]:
    """Run the detector over every enabled ticker that wants technical analysis.

    ``commit`` writes each verdict to ``setup_state``. That is what lets the
    *next* run recognise a failed breakout, so a scan whose result was never
    committed leaves tomorrow blind to today.
    """
    results: list[Setup] = []
    for entry in config.tickers:
        if not entry.wants("technical"):
            continue
        history = bars_module.history(conn, entry.ticker)
        previous = db.load_setup_state(conn, entry.ticker)
        result = setups.detect(
            entry.ticker,
            history,
            thresholds_for(config, entry.ticker),
            previous_stage=previous["stage"] if previous else None,
            previous_pivot=previous["pivot"] if previous else None,
        )
        results.append(result)
        if commit and history:
            db.store_setup_state(
                conn, entry.ticker, result.as_of, result.stage, result.score, result.pivot
            )
    return results


def _sentiment_payload(story) -> dict | None:
    """The vendor's ticker-level reading, when Alpha Vantage carried the story.

    Averaged across the cluster only when more than one copy was scored, and
    labelled `derived` wherever it goes. A sentiment score is a vendor model's
    output over an article, not an observation about the company, and it must
    never travel unlabelled into a place where it reads as a fact.
    """
    scored = [i for i in story.items if i.sentiment_score is not None]
    if not scored:
        return None
    return {
        "score": round(sum(i.sentiment_score for i in scored) / len(scored), 3),
        "label": scored[0].sentiment_label,
        "scored_copies": len(scored),
        "vendor": "alphavantage",
        "basis": "derived: vendor model over the article text, not an observation",
    }


def _story_payload(story) -> dict:
    peers = sorted({item.peer_of for item in story.items if item.peer_of})
    verdict = story.verdict
    return {
        "title": story.title,
        "url": story.url,
        "sources": list(story.sources),
        "published_text": story.items[0].published_text,
        "published_at": story.items[0].published_at,
        "about_competitor": peers[0] if len(peers) == 1 else (peers or None),
        "carried_by": len(story.items),
        "event_class": verdict.event_class if verdict else None,
        "materiality": verdict.score if verdict else None,
        "band": verdict.band if verdict else None,
        "why": list(verdict.reason) if verdict else [],
        "sentiment": _sentiment_payload(story),
    }


def sector_section(
    conn: sqlite3.Connection, config: WatchlistConfig
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Each sector measured, and each watched ticker's standing within its groups.

    Returns ``(views, standings)``. A sector with too little priced history still
    appears, carrying the line that says so -- a section that silently omits a
    group reads as "nothing happened there", which is a different claim.
    """
    views: list[dict] = []
    standings: dict[str, list[dict]] = {}
    horizon = config.defaults.technical_horizon_days
    watched = {entry.ticker for entry in config.tickers if entry.enabled}

    for spec in config.sectors:
        bars_by = {member: bars_module.history(conn, member) for member in spec.members}
        view = sector.analyse(spec, bars_by, horizon)
        views.append({**view.to_dict(), "line": sector.line(view)})
        for member in spec.members:
            if member not in watched:
                continue
            place = sector.standing(view, member)
            if place is not None:
                standings.setdefault(member, []).append(place)
    return views, standings


def macro_section(
    conn: sqlite3.Connection, config: WatchlistConfig, now: datetime, commit: bool = False
) -> dict:
    """Rates, but only when they moved. Silence here is the normal case.

    ``quiet`` true means write nothing at all about macro -- not a sentence
    saying rates were unchanged. A section that speaks every morning is one the
    reader learns to skip, and then it is not there when it matters.
    """
    if not config.macro.enabled:
        return {"enabled": False, "quiet": True, "moved": [], "curve": None}

    series = macro.series_for(config.macro)
    moved = macro.pending(conn, series)
    if commit:
        macro.mark_notified(conn, moved, now)

    return {
        "enabled": True,
        "quiet": not moved,
        "moved": [
            {
                "series": reading.series,
                "label": reading.label,
                "as_of": reading.as_of,
                "value": reading.value,
                "previous": reading.previous,
                "change": round(reading.change, 4) if reading.change is not None else None,
                "line": macro.line(reading),
            }
            for reading in moved
        ],
        # Always present when both legs exist, moved or not: the shape of the
        # curve is context for every single-name call, not an event in itself.
        "curve": macro.curve(conn),
    }


def build(
    conn: sqlite3.Connection,
    config: WatchlistConfig,
    now: datetime,
    commit: bool = False,
    floor: int = 0,
    held: frozenset[str] | set[str] = frozenset(),
) -> dict:
    """The daily payload. One JSON object, and the agent's whole input.

    ``floor`` cuts the tail of the news list by materiality score. ``held`` are
    the open positions, which lift a story's score -- the same headline matters
    more when there is money on it.
    """
    results = scan(conn, config, commit=commit)

    narrative = [
        result
        for result in results
        if result.stage in NARRATIVE_STAGES and result.within_horizon
    ]
    narrative.sort(key=lambda result: result.score, reverse=True)
    written_up = {result.ticker for result in narrative}

    status = [
        setups.status_line(result) for result in results if result.ticker not in written_up
    ]

    watched = [entry.ticker for entry in config.tickers if entry.enabled]
    found = news.pending(
        conn, config.report.cluster_threshold, tickers=watched, floor=floor, held=held
    )
    due = events.pending(conn, now.date(), config.report.event_horizon_days, tickers=watched)

    # Cap *before* stamping. Marking stories that were never sent would swallow
    # them silently, which is the one thing a dedupe table must never do.
    stories = found[: config.report.max_stories]
    held = len(found) - len(stories)

    sectors, standings = sector_section(conn, config)
    rates = macro_section(conn, config, now, commit=commit)

    if commit:
        news.mark_notified(conn, stories, now)
        events.mark_notified(conn, due, now)

    # date.min is the sentinel a ticker with no cached bars carries. Letting it
    # into the report's as_of would date the whole thing to the year 1.
    dated = [result.as_of for result in results if result.as_of > date.min]

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "as_of": max(dated, default=now.date()).isoformat(),
        "committed": commit,
        "watched": len(watched),
        "setups": [
            # The sector standing rides along with the setup it qualifies. A
            # coil that is really its whole group coiling is a different trade,
            # and separating the two readings into distant sections invites the
            # agent to write the paragraph without ever reaching the second one.
            {**result.to_dict(), "sector_standing": standings.get(result.ticker, [])}
            for result in narrative
        ],
        "status_lines": status,
        "sectors": sectors,
        "macro": rates,
        "fresh_news": [_story_payload(story) for story in stories],
        # Never a silent truncation. If this is above zero, say so in the
        # message -- the held stories stay pending and arrive next time.
        "fresh_news_held": held,
        "events": [
            {
                "ticker": event.ticker,
                "kind": event.kind,
                "date": event.event_date.isoformat(),
                "days_away": event.days_away,
                "line": events.describe(event),
            }
            for event in due
        ],
        "quiet": not narrative and not stories and not due and rates["quiet"],
    }


def alerts(
    conn: sqlite3.Connection,
    config: WatchlistConfig,
    tickers: list[str],
    now: datetime,
    commit: bool = False,
) -> dict:
    """Event-driven portfolio alerts: fresh news and imminent corporate events.

    Scoped to the tickers passed in -- the open positions -- and carrying nothing
    that is not new. ``quiet`` true means send nothing at all.

    A ticker that is both held and watched is reported by whichever of this and
    :func:`build` runs first, because both stamp the same rows. Reported once is
    the intent; which section it lands in is not worth coordinating.
    """
    found = news.pending(conn, config.report.cluster_threshold, tickers=tickers)
    due = events.pending(conn, now.date(), config.report.event_horizon_days, tickers=tickers)

    stories = found[: config.report.max_stories]
    held = len(found) - len(stories)

    if commit:
        news.mark_notified(conn, stories, now)
        events.mark_notified(conn, due, now)

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "committed": commit,
        "tickers": tickers,
        "fresh_news": [_story_payload(story) for story in stories],
        "fresh_news_held": held,
        "events": [
            {
                "ticker": event.ticker,
                "kind": event.kind,
                "date": event.event_date.isoformat(),
                "days_away": event.days_away,
                "line": events.describe(event),
            }
            for event in due
        ],
        "quiet": not stories and not due,
    }


def pending_summary(
    conn: sqlite3.Connection, tickers: list[str] | None, now: datetime, within_days: int
) -> dict:
    """Is anything waiting? The cheap gate the cron wrapper branches on.

    Counts only. Loading and clustering the payload to answer "is there anything"
    would defeat the point of asking.
    """
    news_waiting = news.pending_count(conn, tickers)
    events_waiting = events.pending_count(conn, now.date(), within_days, tickers)
    return {
        "ok": True,
        "pending": news_waiting + events_waiting,
        "news": news_waiting,
        "events": events_waiting,
    }
