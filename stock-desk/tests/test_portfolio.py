"""Position netting: average cost for what is open, FIFO for what is realised."""

from __future__ import annotations

from datetime import date

import pytest

from stock_desk import portfolio
from stock_desk.models import Trade


def trade(side: str, quantity: float, price: float, day: int, fee: float = 0.0) -> Trade:
    return Trade(
        ticker="TEST",
        trade_date=date(2026, 1, day),
        side=side,
        quantity=quantity,
        price=price,
        fee=fee,
        trade_id=day,
    )


class TestOpenPosition:
    def test_single_buy(self):
        held = portfolio.net("TEST", [trade("buy", 100, 50.0, 1)])
        assert held.quantity == 100
        assert held.avg_cost == pytest.approx(50.0)
        assert held.cost_basis == pytest.approx(5000.0)

    def test_two_buys_average(self):
        held = portfolio.net("TEST", [trade("buy", 100, 50.0, 1), trade("buy", 100, 70.0, 2)])
        assert held.quantity == 200
        assert held.avg_cost == pytest.approx(60.0)

    def test_average_is_weighted_not_arithmetic(self):
        held = portfolio.net("TEST", [trade("buy", 100, 50.0, 1), trade("buy", 300, 70.0, 2)])
        assert held.avg_cost == pytest.approx(65.0)

    def test_buy_fees_raise_the_break_even(self):
        held = portfolio.net("TEST", [trade("buy", 100, 50.0, 1, fee=25.0)])
        assert held.avg_cost == pytest.approx(50.25)


class TestRealisedFIFO:
    def test_sale_consumes_the_oldest_lot_first(self):
        trades = [
            trade("buy", 100, 50.0, 1),
            trade("buy", 100, 70.0, 2),
            trade("sell", 100, 80.0, 3),
        ]
        held = portfolio.net("TEST", trades)
        # FIFO sells the 50.0 lot: (80 - 50) * 100 = 3000. Average cost would
        # have given (80 - 60) * 100 = 2000, which is the wrong tax answer.
        assert held.realized_pnl == pytest.approx(3000.0)
        assert held.quantity == 100
        assert held.avg_cost == pytest.approx(70.0)

    def test_partial_lot_consumption(self):
        trades = [trade("buy", 100, 50.0, 1), trade("sell", 40, 60.0, 2)]
        held = portfolio.net("TEST", trades)
        assert held.realized_pnl == pytest.approx(400.0)
        assert held.quantity == 60
        assert held.avg_cost == pytest.approx(50.0)

    def test_sale_spanning_two_lots(self):
        trades = [
            trade("buy", 100, 50.0, 1),
            trade("buy", 100, 60.0, 2),
            trade("sell", 150, 70.0, 3),
        ]
        held = portfolio.net("TEST", trades)
        # 100 @ (70-50) = 2000, then 50 @ (70-60) = 500.
        assert held.realized_pnl == pytest.approx(2500.0)
        assert held.quantity == 50

    def test_sell_fees_reduce_the_proceeds(self):
        trades = [trade("buy", 100, 50.0, 1), trade("sell", 100, 60.0, 2, fee=30.0)]
        held = portfolio.net("TEST", trades)
        assert held.realized_pnl == pytest.approx(970.0)

    def test_closing_out_leaves_no_position(self):
        trades = [trade("buy", 100, 50.0, 1), trade("sell", 100, 55.0, 2)]
        held = portfolio.net("TEST", trades)
        assert held.quantity == pytest.approx(0.0)
        assert held.realized_pnl == pytest.approx(500.0)

    def test_a_loss_is_reported_as_a_loss(self):
        trades = [trade("buy", 100, 50.0, 1), trade("sell", 100, 40.0, 2)]
        assert portfolio.net("TEST", trades).realized_pnl == pytest.approx(-1000.0)


class TestShorts:
    def test_selling_more_than_held_opens_a_short(self):
        """A trade that really happened must be recordable. Refusing it would
        make the log a worse record than the broker statement."""
        held = portfolio.net("TEST", [trade("sell", 100, 50.0, 1)])
        assert held.quantity == -100
        assert held.avg_cost == pytest.approx(50.0)

    def test_covering_a_short_below_entry_is_a_profit(self):
        trades = [trade("sell", 100, 50.0, 1), trade("buy", 100, 40.0, 2)]
        held = portfolio.net("TEST", trades)
        assert held.realized_pnl == pytest.approx(1000.0)
        assert held.quantity == pytest.approx(0.0)

    def test_flipping_long_to_short_in_one_trade(self):
        trades = [trade("buy", 100, 50.0, 1), trade("sell", 150, 60.0, 2)]
        held = portfolio.net("TEST", trades)
        assert held.realized_pnl == pytest.approx(1000.0)
        assert held.quantity == -50
        assert held.avg_cost == pytest.approx(60.0)


class TestPricing:
    def test_unrealised_uses_the_last_close(self):
        held = portfolio.net("TEST", [trade("buy", 100, 50.0, 1)], last_close=60.0)
        assert held.market_value == pytest.approx(6000.0)
        assert held.unrealized_pnl == pytest.approx(1000.0)
        assert held.unrealized_pct == pytest.approx(20.0)

    def test_no_close_means_no_invented_mark(self):
        held = portfolio.net("TEST", [trade("buy", 100, 50.0, 1)])
        assert held.market_value is None
        assert held.unrealized_pnl is None

    def test_short_gains_when_price_falls(self):
        held = portfolio.net("TEST", [trade("sell", 100, 50.0, 1)], last_close=40.0)
        assert held.unrealized_pnl == pytest.approx(1000.0)


class TestSelection:
    def test_closed_positions_are_not_holdings(self):
        trades = [
            Trade("AAA", date(2026, 1, 1), "buy", 10, 5.0, trade_id=1),
            Trade("AAA", date(2026, 1, 2), "sell", 10, 6.0, trade_id=2),
            Trade("BBB", date(2026, 1, 3), "buy", 10, 5.0, trade_id=3),
        ]
        assert portfolio.open_tickers(trades) == ["BBB"]
        assert portfolio.all_tickers(trades) == ["AAA", "BBB"]


class TestSummary:
    def test_long_line_names_direction_and_average(self):
        held = portfolio.net("TEST", [trade("buy", 100, 50.0, 1)], last_close=60.0)
        line = portfolio.summarise(held)
        assert line.startswith("TEST — long 100 @ 50.00 avg")
        assert "+20.0%" in line

    def test_flat_line_reports_only_realised(self):
        trades = [trade("buy", 100, 50.0, 1), trade("sell", 100, 55.0, 2)]
        line = portfolio.summarise(portfolio.net("TEST", trades))
        assert "flat" in line
        assert "+500.00" in line
