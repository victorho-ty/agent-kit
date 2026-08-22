"""The two scheduled runs, each behind one command.

Hermes cron invokes exactly one thing per entry and sends what comes back. That
is the whole interface, and it exists so the *sequencing* — sync before scan,
poll before report, stamp only after the message is built — lives in Python
where it is testable, rather than in a skill instruction the agent may follow
differently on a Tuesday.

| profile | when | scope |
|---|---|---|
| `morning-hkt` | 09:00 Asia/Hong_Kong | the full digest: overnight US tape, every sector, rates |
| `pre-us-open` | 30 min before the NYSE open | what changed since the morning run, ahead of the bell |

Both cover the whole watchlist. They differ in budget and in what has happened
since they last ran, not in which tickers they look at — splitting the watchlist
by listing venue would mean an HK holder hears nothing about the US names they
also hold.

## The Alpha Vantage ledger

25 calls a day for the entire profile, shared between news sentiment and macro.
Two runs at nine and eight calls leaves eight spare for whatever the operator
asks during the day, which is the point of not spending them all on a schedule.

The budget is passed down as a hard ceiling rather than a hint, and every step
reports what it actually spent. A run that quietly used twenty would take the
evening's macro reading down with it and there would be nothing in the payload
to say why.

## Failure is partial, never fatal

Yahoo is free and uncapped, so news intake survives an Alpha Vantage outage with
no sentiment scores attached. A macro refresh that fails leaves the last stored
readings in place. Every step's failures are collected and returned; the report
is still built. A desk that produces nothing because one of four feeds was down
is worse than one that says which reading is missing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from . import bars as bars_module
from . import db, events, macro, news, portfolio, report
from .config.watchlist import WatchlistConfig
from .errors import ConfigError


@dataclass(frozen=True, slots=True)
class RunProfile:
    name: str
    label: str
    # Alpha Vantage calls this run may spend, per purpose. Yahoo is uncapped and
    # deliberately not budgeted -- it is what keeps the desk working when the
    # key is missing or the quota is gone.
    av_news_calls: int
    av_macro_calls: int
    # Materiality floor for the news list. Zero keeps everything that survived
    # intake suppression, which is already the aggressive filter.
    news_floor: int = 0


PROFILES: dict[str, RunProfile] = {
    "morning-hkt": RunProfile(
        name="morning-hkt",
        label="09:00 HKT — the daily digest",
        av_news_calls=6,
        av_macro_calls=3,
    ),
    "pre-us-open": RunProfile(
        name="pre-us-open",
        label="30 minutes before the US open",
        av_news_calls=6,
        av_macro_calls=2,
    ),
}


def profile(name: str) -> RunProfile:
    if name not in PROFILES:
        raise ConfigError(
            f"no run profile named {name!r}",
            profile=name,
            known=sorted(PROFILES),
        )
    return PROFILES[name]


def sync_scope(conn: sqlite3.Connection, config: WatchlistConfig) -> list[str]:
    """Every ticker that needs cached bars.

    Three groups, and the third is easy to forget: the watchlist, the open
    positions, and **every sector member**. A sector member that is not itself
    watched still needs prices, because the comparison is the whole point of the
    sector section -- without its bars it shows up as `missing` and the group it
    belongs to silently reports on fewer names than it claims.

    Competitors are *not* here. They are a news relationship, not a priced one.
    """
    scope: list[str] = [entry.ticker for entry in config.tickers if entry.enabled]
    for ticker in portfolio.open_tickers(db.load_trades(conn)):
        if ticker not in scope:
            scope.append(ticker)
    for spec in config.sectors:
        for member in spec.members:
            if member not in scope:
                scope.append(member)
    return scope


def execute(
    conn: sqlite3.Connection,
    config: WatchlistConfig,
    spec: RunProfile,
    now: datetime,
    commit: bool = False,
) -> dict:
    """Run one scheduled profile end to end and return the payload to send.

    ``commit`` is what stamps news, events and macro as reported. Run it, then
    send. If sending fails, say so -- the items are recoverable but they will
    not come round again by themselves.
    """
    held = frozenset(portfolio.open_tickers(db.load_trades(conn)))
    steps: dict[str, dict] = {}
    failures: list[dict] = []

    def note(name: str, result: dict) -> None:
        steps[name] = result
        for failure in result.get("failures", []) or []:
            failures.append({"step": name, **failure})

    scope = sync_scope(conn, config)
    note("sync", bars_module.sync(conn, scope, now.date()))

    note("events", events.refresh(conn, scope, now))

    since = db.last_success(conn, "news_poll")
    note(
        "news",
        news.poll(
            conn,
            [entry for entry in config.tickers if entry.enabled],
            now,
            av_budget=spec.av_news_calls,
            since=since,
            held=held,
        ),
    )

    if config.macro.enabled:
        note(
            "macro",
            macro.refresh(conn, macro.series_for(config.macro), now, budget=spec.av_macro_calls),
        )
        # The first sight of a level is a starting point, not an event. Seeding
        # stamps whatever is already stored so the first report after setup does
        # not announce six yields as though they were six moves.
        if not db.last_success(conn, f"run:{spec.name}"):
            macro.seed(conn, macro.series_for(config.macro), now)

    payload = report.build(
        conn, config, now, commit=commit, floor=spec.news_floor, held=held
    )

    spent = steps.get("news", {}).get("alphavantage_calls", 0) + steps.get("macro", {}).get(
        "calls", 0
    )
    payload["run"] = {
        "profile": spec.name,
        "label": spec.label,
        "alphavantage_calls_spent": spent,
        "alphavantage_budget": spec.av_news_calls + spec.av_macro_calls,
        "news_since": since.isoformat() if since else None,
        "steps": {
            name: {k: v for k, v in result.items() if k != "failures"}
            for name, result in steps.items()
        },
        # Named, never silent. A reading that is absent because a feed was down
        # must not look like a reading that is absent because nothing moved.
        "degraded": bool(failures),
        "failures": failures,
    }
    return payload
