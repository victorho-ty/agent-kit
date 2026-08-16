"""The command surface. One JSON object per command, on stdout.

Every command prints exactly one JSON object and exits with a code from
:class:`stock_desk.errors.ExitCode`. The agent parses that object and branches on
the exit code; it never reads stderr and never pattern-matches a sentence.

The one deliberate exception is ``pending --count``, which prints a bare integer
so a shell can test it without a JSON parser. That is the gate the cron wrapper
uses:

    stockctl news poll >/dev/null && \\
      [ "$(stockctl pending --count)" -gt 0 ] && hermes-run stock-desk-alerts

Everything above that line runs on a timer and costs nothing. Only the last part
wakes the agent, and only when there is something to say.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from . import bars as bars_module
from . import brief as brief_module
from . import charts, clock, db, events, markets, news, portfolio, report, settings
from .config import watchlist as watchlist_config
from .errors import ConfigError, DeskError, ExitCode, NotFoundError
from .models import TickerConfig, Trade


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return int(ExitCode.OK)


def _watched_and_held(conn, config) -> list[str]:
    """Everything worth syncing: the enabled watchlist plus every open position."""
    tickers = [entry.ticker for entry in config.tickers if entry.enabled]
    for ticker in portfolio.open_tickers(db.load_trades(conn)):
        if ticker not in tickers:
            tickers.append(ticker)
    return tickers


# ----------------------------------------------------------------------- watch


def cmd_watch_list(args) -> int:
    config = watchlist_config.load()
    return _emit(
        {
            "ok": True,
            "path": str(config.path),
            "timezone": config.timezone,
            "report": {
                "frequency": config.report.frequency,
                "minutes_before_open": config.report.minutes_before_open,
                "event_horizon_days": config.report.event_horizon_days,
            },
            "tickers": [
                {
                    "ticker": entry.ticker,
                    "company_name": entry.company_name,
                    "enabled": entry.enabled,
                    "analysis_types": list(entry.analysis_types),
                    "technical_horizon_days": entry.technical_horizon_days,
                    "competitors": list(entry.competitors),
                    "sector_keywords": list(entry.sector_keywords),
                    "market": markets.market_label(entry.ticker),
                    "notes": entry.notes,
                }
                for entry in config.tickers
            ],
        }
    )


def cmd_watch_add(args) -> int:
    config = watchlist_config.load()
    entry = TickerConfig(
        ticker=args.ticker.strip().upper(),
        enabled=not args.disabled,
        analysis_types=tuple(args.analysis or config.defaults.analysis_types),
        technical_horizon_days=args.horizon or config.defaults.technical_horizon_days,
        competitors=tuple(c.strip().upper() for c in (args.competitor or [])),
        sector_keywords=tuple(args.keyword or []),
        company_name=args.name,
        min_avg_dollar_volume=args.min_dollar_volume,
        notes=args.notes or "",
    )
    updated = watchlist_config.add_ticker(config, entry)
    return _emit(
        {
            "ok": True,
            "added": entry.ticker,
            "market": markets.market_label(entry.ticker),
            "competitors": list(entry.competitors),
            "watchlist_size": len(updated.tickers),
            "next": f"stockctl sync --ticker {entry.ticker} to fetch its history",
        }
    )


def cmd_watch_remove(args) -> int:
    config = watchlist_config.load()
    updated = watchlist_config.remove_ticker(config, args.ticker)
    return _emit(
        {"ok": True, "removed": args.ticker.upper(), "watchlist_size": len(updated.tickers)}
    )


def cmd_watch_update(args) -> int:
    changes: dict = {}
    if args.name is not None:
        changes["company_name"] = args.name
    if args.horizon is not None:
        changes["technical_horizon_days"] = args.horizon
    if args.analysis:
        changes["analysis_types"] = args.analysis
    if args.competitor is not None:
        changes["competitors"] = args.competitor
    if args.keyword is not None:
        changes["sector_keywords"] = args.keyword
    if args.min_dollar_volume is not None:
        changes["min_avg_dollar_volume"] = args.min_dollar_volume
    if args.notes is not None:
        changes["notes"] = args.notes
    if args.enable:
        changes["enabled"] = True
    if args.disable:
        changes["enabled"] = False
    if not changes:
        raise ConfigError("nothing to update; pass at least one field", ticker=args.ticker)

    config = watchlist_config.load()
    updated = watchlist_config.update_ticker(config, args.ticker, **changes)
    entry = updated.require(args.ticker)
    return _emit(
        {
            "ok": True,
            "updated": entry.ticker,
            "changed": sorted(changes),
            "enabled": entry.enabled,
            "technical_horizon_days": entry.technical_horizon_days,
            "competitors": list(entry.competitors),
        }
    )


# ------------------------------------------------------------------- positions


def cmd_positions_add(args) -> int:
    now = clock.now()
    trade = Trade(
        ticker=args.ticker.strip().upper(),
        trade_date=date.fromisoformat(args.date),
        side=args.side,
        quantity=args.quantity,
        price=args.price,
        fee=args.fee,
        note=args.note or "",
    )
    with db.connect() as conn:
        trade_id = db.add_trade(conn, trade, now)
        held = portfolio.net(trade.ticker, db.load_trades(conn, trade.ticker))
        return _emit(
            {
                "ok": True,
                "trade_id": trade_id,
                "ticker": trade.ticker,
                "side": trade.side,
                "quantity": trade.quantity,
                "price": trade.price,
                "position": {
                    "quantity": held.quantity,
                    "avg_cost": held.avg_cost,
                    "realized_pnl": held.realized_pnl,
                    "line": portfolio.summarise(held),
                },
            }
        )


def cmd_positions_list(args) -> int:
    with db.connect() as conn:
        trades = db.load_trades(conn, args.ticker.upper() if args.ticker else None)
        tickers = portfolio.all_tickers(trades) if args.all else portfolio.open_tickers(trades)
        holdings = []
        for ticker in tickers:
            history = bars_module.history(conn, ticker, limit=1)
            fundamentals = db.load_fundamentals(conn, ticker)
            held = portfolio.net(
                ticker,
                [t for t in trades if t.ticker == ticker],
                last_close=history[-1].close if history else None,
                currency=fundamentals.currency if fundamentals else None,
            )
            holdings.append(
                {
                    "ticker": ticker,
                    "quantity": held.quantity,
                    "avg_cost": held.avg_cost,
                    "cost_basis": held.cost_basis,
                    "last_close": held.last_close,
                    "market_value": held.market_value,
                    "unrealized_pnl": held.unrealized_pnl,
                    "unrealized_pct": held.unrealized_pct,
                    "realized_pnl": held.realized_pnl,
                    "currency": held.currency,
                    "line": portfolio.summarise(held),
                }
            )
        return _emit(
            {
                "ok": True,
                "holdings": holdings,
                "trades": len(trades),
                # Never summed. Positions may be in different currencies, and a
                # total across HKD and USD would be a made-up number.
                "note": "values are per position, in each position's own currency",
            }
        )


def cmd_positions_delete(args) -> int:
    with db.connect() as conn:
        if not db.delete_trade(conn, args.trade_id):
            raise NotFoundError(f"no trade with id {args.trade_id}", trade_id=args.trade_id)
        return _emit({"ok": True, "deleted": args.trade_id})


# ------------------------------------------------------------------ data intake


def cmd_sync(args) -> int:
    now = clock.now()
    config = watchlist_config.load()
    with db.connect() as conn:
        tickers = (
            [t.strip().upper() for t in args.ticker]
            if args.ticker
            else _watched_and_held(conn, config)
        )
        run_id = db.start_run(conn, "sync", now)
        result = bars_module.sync(conn, tickers, now.date(), force_full=args.full)
        if args.fundamentals or not args.ticker:
            result["fundamentals"] = _refresh_fundamentals(conn, tickers, now)
        db.finish_run(conn, run_id, clock.now(), result["status"], json.dumps(result["failures"]))
        result["ok"] = result["status"] in {"ok", "partial", "skipped"}
        return _emit(result)


def _refresh_fundamentals(conn, tickers: list[str], now: datetime) -> dict:
    from .providers import PRIMARY

    updated, failed = 0, []
    for ticker in tickers:
        try:
            snapshot = PRIMARY.fundamentals(ticker)
        except DeskError as exc:
            failed.append({"ticker": ticker, "error": exc.message})
            continue
        if snapshot:
            db.store_fundamentals(conn, snapshot)
            updated += 1
    return {"updated": updated, "failures": failed}


def cmd_news_poll(args) -> int:
    now = clock.now()
    config = watchlist_config.load()
    with db.connect() as conn:
        run_id = db.start_run(conn, "news_poll", now)
        entries = [entry for entry in config.tickers if entry.enabled]
        result = news.poll(conn, entries, now)
        db.finish_run(conn, run_id, clock.now(), result["status"], json.dumps(result["failures"]))
        result["ok"] = result["status"] != "error"
        return _emit(result)


def cmd_events_refresh(args) -> int:
    now = clock.now()
    config = watchlist_config.load()
    with db.connect() as conn:
        tickers = _watched_and_held(conn, config)
        run_id = db.start_run(conn, "events_refresh", now)
        result = events.refresh(conn, tickers, now)
        db.finish_run(conn, run_id, clock.now(), result["status"], json.dumps(result["failures"]))
        result["ok"] = result["status"] != "error"
        return _emit(result)


# ---------------------------------------------------------------------- output


def cmd_scan(args) -> int:
    config = watchlist_config.load()
    with db.connect() as conn:
        results = report.scan(conn, config, commit=args.commit)
        return _emit(
            {
                "ok": True,
                "committed": args.commit,
                "setups": [result.to_dict() for result in results],
            }
        )


def cmd_report(args) -> int:
    now = clock.now()
    config = watchlist_config.load()
    with db.connect() as conn:
        return _emit(report.build(conn, config, now, commit=args.commit))


def cmd_alerts(args) -> int:
    now = clock.now()
    config = watchlist_config.load()
    with db.connect() as conn:
        tickers = (
            [t.strip().upper() for t in args.ticker]
            if args.ticker
            else portfolio.open_tickers(db.load_trades(conn))
        )
        return _emit(report.alerts(conn, config, tickers, now, commit=args.commit))


def cmd_pending(args) -> int:
    now = clock.now()
    config = watchlist_config.load()
    with db.connect() as conn:
        scope = [t.strip().upper() for t in args.ticker] if args.ticker else None
        summary = report.pending_summary(conn, scope, now, config.report.event_horizon_days)
        if args.count:
            # The documented exception: a bare integer, for shell arithmetic.
            print(summary["pending"])
            return int(ExitCode.OK)
        return _emit(summary)


def cmd_brief(args) -> int:
    now = clock.now()
    config = watchlist_config.load()
    with db.connect() as conn:
        return _emit(
            brief_module.build(
                conn,
                args.ticker,
                now,
                lookback_days=args.lookback,
                config=config,
                with_charts=not args.no_charts,
            )
        )


def cmd_chart(args) -> int:
    ticker = args.ticker.strip().upper()
    with db.connect() as conn:
        history = bars_module.history(conn, ticker)
        if not history:
            raise NotFoundError(f"no cached bars for {ticker}; run a sync first", ticker=ticker)
        charts.sweep()
        if args.kind == "candles":
            payload = charts.candles(ticker, bars_module.window(history, args.lookback), args.lookback)
        else:
            payload = charts.lines(ticker, history, args.lookback)
        payload["ok"] = True
        return _emit(payload)


def cmd_schedule(args) -> int:
    """When the next report is due, per market group.

    A watchlist spanning two markets has two report times. This is what the cron
    entries are built from, and it is worth re-running after a DST change.
    """
    now = clock.now()
    config = watchlist_config.load()
    watched = [entry.ticker for entry in config.tickers if entry.enabled]
    groups = markets.group_by_market(watched)

    schedule = []
    for label, tickers in sorted(groups.items()):
        due_at = markets.report_due_at(tickers[0], now, config.report.minutes_before_open)
        schedule.append(
            {
                "market": label,
                "tickers": tickers,
                "next_open": (
                    markets.next_open(tickers[0], now).isoformat()
                    if markets.next_open(tickers[0], now)
                    else None
                ),
                "report_due_at": due_at.isoformat() if due_at else None,
                "minutes_before_open": config.report.minutes_before_open,
            }
        )
    return _emit({"ok": True, "now": now.isoformat(), "schedule": schedule})


def cmd_runs(args) -> int:
    with db.connect() as conn:
        return _emit(
            {"ok": True, "runs": db.recent_runs(conn, limit=args.limit, kind=args.kind)}
        )


# ---------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stockctl",
        description="Swing-trading watchlist and portfolio tools. Every command prints one JSON object.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser("watch", help="the watchlist config").add_subparsers(
        dest="watch_command", required=True
    )
    watch.add_parser("list", help="show the watchlist").set_defaults(func=cmd_watch_list)

    add = watch.add_parser("add", help="add a ticker")
    add.add_argument("ticker")
    add.add_argument("--name", help="company name, used to build news queries")
    add.add_argument("--competitor", action="append", help="repeatable")
    add.add_argument("--keyword", action="append", help="sector search term, repeatable")
    add.add_argument("--analysis", action="append", choices=["technical", "competitor"])
    add.add_argument("--horizon", type=int, help="setup freshness window in days")
    add.add_argument("--min-dollar-volume", type=float, dest="min_dollar_volume")
    add.add_argument("--notes")
    add.add_argument("--disabled", action="store_true")
    add.set_defaults(func=cmd_watch_add)

    remove = watch.add_parser("remove", help="drop a ticker")
    remove.add_argument("ticker")
    remove.set_defaults(func=cmd_watch_remove)

    update = watch.add_parser("update", help="change one ticker's config")
    update.add_argument("ticker")
    update.add_argument("--name")
    update.add_argument("--competitor", action="append")
    update.add_argument("--keyword", action="append")
    update.add_argument("--analysis", action="append", choices=["technical", "competitor"])
    update.add_argument("--horizon", type=int)
    update.add_argument("--min-dollar-volume", type=float, dest="min_dollar_volume")
    update.add_argument("--notes")
    update.add_argument("--enable", action="store_true")
    update.add_argument("--disable", action="store_true")
    update.set_defaults(func=cmd_watch_update)

    positions = sub.add_parser("positions", help="the trade log").add_subparsers(
        dest="positions_command", required=True
    )
    trade_add = positions.add_parser("add", help="record a trade")
    trade_add.add_argument("ticker")
    trade_add.add_argument("--side", choices=["buy", "sell"], required=True)
    trade_add.add_argument("--quantity", type=float, required=True)
    trade_add.add_argument("--price", type=float, required=True)
    trade_add.add_argument("--date", required=True, help="trade date, ISO8601")
    trade_add.add_argument("--fee", type=float, default=0.0)
    trade_add.add_argument("--note")
    trade_add.set_defaults(func=cmd_positions_add)

    trade_list = positions.add_parser("list", help="open positions")
    trade_list.add_argument("--ticker")
    trade_list.add_argument("--all", action="store_true", help="include closed positions")
    trade_list.set_defaults(func=cmd_positions_list)

    trade_delete = positions.add_parser("delete", help="remove a mistaken entry")
    trade_delete.add_argument("trade_id", type=int)
    trade_delete.set_defaults(func=cmd_positions_delete)

    sync = sub.add_parser("sync", help="fetch new bars (silent; wakes nobody)")
    sync.add_argument("--ticker", action="append")
    sync.add_argument("--full", action="store_true", help="re-fetch all history")
    sync.add_argument("--fundamentals", action="store_true")
    sync.set_defaults(func=cmd_sync)

    news_parser = sub.add_parser("news", help="news intake").add_subparsers(
        dest="news_command", required=True
    )
    news_parser.add_parser("poll", help="fetch feeds (silent; wakes nobody)").set_defaults(
        func=cmd_news_poll
    )

    events_parser = sub.add_parser("events", help="corporate events").add_subparsers(
        dest="events_command", required=True
    )
    events_parser.add_parser("refresh", help="pull calendars (silent)").set_defaults(
        func=cmd_events_refresh
    )

    scan = sub.add_parser("scan", help="run the setup detector over the watchlist")
    scan.add_argument("--commit", action="store_true", help="store today's verdicts")
    scan.set_defaults(func=cmd_scan)

    report_parser = sub.add_parser("report", help="the daily watchlist report")
    report_parser.add_argument(
        "--commit", action="store_true", help="stamp what is returned as reported"
    )
    report_parser.set_defaults(func=cmd_report)

    alerts = sub.add_parser("alerts", help="event-driven portfolio alerts")
    alerts.add_argument("--ticker", action="append")
    alerts.add_argument("--commit", action="store_true")
    alerts.set_defaults(func=cmd_alerts)

    pending = sub.add_parser("pending", help="is anything waiting? the cron gate")
    pending.add_argument("--ticker", action="append")
    pending.add_argument("--count", action="store_true", help="print a bare integer")
    pending.set_defaults(func=cmd_pending)

    brief = sub.add_parser("brief", help="on-demand: charts plus metrics for one ticker")
    brief.add_argument("--ticker", required=True)
    brief.add_argument("--lookback", type=int, default=brief_module.DEFAULT_LOOKBACK_DAYS)
    brief.add_argument("--no-charts", action="store_true")
    brief.set_defaults(func=cmd_brief)

    chart = sub.add_parser("chart", help="render one image")
    chart.add_argument("kind", choices=["candles", "lines"])
    chart.add_argument("--ticker", required=True)
    chart.add_argument("--lookback", type=int, default=brief_module.DEFAULT_LOOKBACK_DAYS)
    chart.set_defaults(func=cmd_chart)

    sub.add_parser("schedule", help="when the next report is due, per market").set_defaults(
        func=cmd_schedule
    )

    runs = sub.add_parser("runs", help="recent run health")
    runs.add_argument("--limit", type=int, default=10)
    runs.add_argument("--kind")
    runs.set_defaults(func=cmd_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DeskError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, default=str))
        return int(exc.exit_code)
    except BrokenPipeError:  # pragma: no cover - a closed stdout is not an error
        return int(ExitCode.OK)
    except Exception as exc:  # pragma: no cover - last resort, still valid JSON
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "ERR_UNEXPECTED",
                    "exit_code": int(ExitCode.ERR_CONFIG),
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
        )
        return int(ExitCode.ERR_CONFIG)


if __name__ == "__main__":
    sys.exit(main())
