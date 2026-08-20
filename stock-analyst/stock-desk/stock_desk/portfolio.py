"""Netting a trade log into positions, and pricing them.

Pure functions over a list of :class:`Trade`. No network, no database, no clock
-- :mod:`cli` loads the trades and passes the last close in.

**Two cost bases, because they answer different questions.** Open positions are
carried at average cost, which is what "am I up on this" means to somebody
holding one line. Realised profit is computed FIFO, lot by lot, because that is
how a tax authority reads the same trades. Reporting one number for both would
be wrong for one of the two purposes, always.

Fees are folded in on both sides: added to the cost of a buy, subtracted from
the proceeds of a sell. A position's break-even therefore includes what it cost
to get in, which is the only version of the number worth looking at.

Selling more than is held opens a short rather than raising an error. A swing
trader shorts, the arithmetic is symmetric, and refusing to record a trade that
really happened would make the log a worse record than the broker's.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Holding, Trade


@dataclass(slots=True)
class _Lot:
    quantity: float  # signed: positive is long, negative is short
    price: float


def _sign(quantity: float) -> int:
    return 1 if quantity >= 0 else -1


def net(ticker: str, trades: list[Trade], last_close: float | None = None,
        currency: str | None = None) -> Holding:
    """Reduce a trade log to one position.

    Walks chronologically. A trade in the same direction as the open position
    adds a lot; a trade against it consumes lots oldest-first and books the
    difference as realised.
    """
    lots: list[_Lot] = []
    realized = 0.0

    for trade in sorted(trades, key=lambda t: (t.trade_date, t.trade_id or 0)):
        signed = trade.quantity if trade.side == "buy" else -trade.quantity
        # Fees always cost money, whichever way the trade goes: they raise the
        # effective price paid on a buy and lower the price received on a sell.
        per_share_fee = (trade.fee / trade.quantity) if trade.quantity else 0.0
        effective = trade.price + per_share_fee if signed > 0 else trade.price - per_share_fee

        remaining = signed
        while remaining and lots and _sign(lots[0].quantity) != _sign(remaining):
            lot = lots[0]
            closed = min(abs(lot.quantity), abs(remaining))
            # Long lot closed by a sell earns (exit - entry); a short lot closed
            # by a buy earns the reverse. The lot's own sign gives the direction.
            realized += closed * (effective - lot.price) * _sign(lot.quantity)
            lot.quantity -= closed * _sign(lot.quantity)
            remaining = (abs(remaining) - closed) * _sign(signed)
            if abs(lot.quantity) < 1e-9:
                lots.pop(0)
        if remaining:
            lots.append(_Lot(quantity=remaining, price=effective))

    quantity = sum(lot.quantity for lot in lots)
    cost_basis = sum(lot.quantity * lot.price for lot in lots)
    avg_cost = (cost_basis / quantity) if quantity else 0.0

    market_value = unrealized = unrealized_pct = None
    if last_close is not None and quantity:
        market_value = quantity * last_close
        unrealized = market_value - cost_basis
        if cost_basis:
            unrealized_pct = 100.0 * unrealized / abs(cost_basis)

    return Holding(
        ticker=ticker,
        quantity=quantity,
        avg_cost=avg_cost,
        cost_basis=cost_basis,
        realized_pnl=realized,
        last_close=last_close,
        market_value=market_value,
        unrealized_pnl=unrealized,
        unrealized_pct=unrealized_pct,
        currency=currency,
    )


def open_tickers(trades: list[Trade]) -> list[str]:
    """Tickers with a non-zero position, in first-traded order.

    A closed-out ticker still has trades and still has realised profit, but it is
    not a holding and must not be alerted on.
    """
    by_ticker: dict[str, list[Trade]] = {}
    for trade in trades:
        by_ticker.setdefault(trade.ticker, []).append(trade)
    return [
        ticker
        for ticker, group in by_ticker.items()
        if abs(net(ticker, group).quantity) > 1e-9
    ]


def all_tickers(trades: list[Trade]) -> list[str]:
    """Every ticker in the log, open or closed."""
    seen: dict[str, None] = {}
    for trade in trades:
        seen.setdefault(trade.ticker, None)
    return list(seen)


def summarise(holding: Holding) -> str:
    """The one-line rendering, built in Python and relayed verbatim."""
    if abs(holding.quantity) < 1e-9:
        return f"{holding.ticker} — flat, realised {holding.realized_pnl:+,.2f}"

    direction = "long" if holding.quantity > 0 else "short"
    line = (
        f"{holding.ticker} — {direction} {abs(holding.quantity):,.0f} "
        f"@ {holding.avg_cost:,.2f} avg"
    )
    if holding.unrealized_pnl is not None and holding.unrealized_pct is not None:
        line += f", {holding.unrealized_pnl:+,.2f} ({holding.unrealized_pct:+.1f}%)"
    if abs(holding.realized_pnl) > 1e-9:
        line += f", realised {holding.realized_pnl:+,.2f}"
    return line
