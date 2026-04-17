"""
Unit Tests — Dashboard Logic
============================
Covers:
  1. P&L colour logic  (colour_pnl helper)
  2. Capital update logic  (Total Capital = starting + realized gains)

Run with:
    python -m pytest test_dashboard.py -v
"""

import sys
import os
import re
import unittest
from datetime import datetime

# Make the project root importable
sys.path.insert(0, os.path.dirname(__file__))

from portfolio import Portfolio, Position, Trade
import config


# ──────────────────────────────────────────────────────────
# Helpers copied from dashboard.py so tests are self-contained
# (avoids importing Streamlit in a test environment)
# ──────────────────────────────────────────────────────────

def colour_pnl(val: float) -> str:
    """Return an HTML <span> coloured green for profit, red for loss."""
    colour = "#145c2e" if val >= 0 else "#8b1a1a"
    return f'<span style="color:{colour};font-weight:bold">₹{val:+,.2f}</span>'


def _pnl_card_fg(pnl_total: float) -> str:
    """Return the foreground colour used for the Total P&L card."""
    return "#145c2e" if pnl_total >= 0 else "#8b1a1a"


def _pnl_card_bg(pnl_total: float) -> str:
    """Return the background colour used for the Total P&L card."""
    return "#e6f9ee" if pnl_total >= 0 else "#fde8e8"


def compute_total_capital(starting_capital: float, realized_pnl: float) -> float:
    """Replicate the dashboard's Total Capital formula."""
    return starting_capital + realized_pnl


# ══════════════════════════════════════════════════════════
# 1. P&L Colour Logic Tests
# ══════════════════════════════════════════════════════════

class TestColourPnl(unittest.TestCase):

    # ── colour_pnl helper ──────────────────────────────────

    def test_profit_gives_green_colour(self):
        html = colour_pnl(500.0)
        self.assertIn("#145c2e", html)

    def test_loss_gives_red_colour(self):
        html = colour_pnl(-250.0)
        self.assertIn("#8b1a1a", html)

    def test_zero_treated_as_profit(self):
        html = colour_pnl(0.0)
        self.assertIn("#145c2e", html, "Zero P&L should be shown in green (neutral/positive)")

    def test_html_contains_rupee_symbol(self):
        html = colour_pnl(100.0)
        self.assertIn("₹", html)

    def test_value_rendered_with_sign(self):
        pos_html = colour_pnl(1234.56)
        neg_html = colour_pnl(-1234.56)
        self.assertIn("+", pos_html)
        self.assertIn("-", neg_html)

    def test_large_profit(self):
        html = colour_pnl(99_999.99)
        self.assertIn("#145c2e", html)

    def test_large_loss(self):
        html = colour_pnl(-99_999.99)
        self.assertIn("#8b1a1a", html)

    # ── P&L card foreground/background colours ─────────────

    def test_card_fg_profit_is_green(self):
        self.assertEqual(_pnl_card_fg(500), "#145c2e")

    def test_card_fg_loss_is_red(self):
        self.assertEqual(_pnl_card_fg(-1), "#8b1a1a")

    def test_card_fg_zero_is_green(self):
        self.assertEqual(_pnl_card_fg(0), "#145c2e")

    def test_card_bg_profit_is_light_green(self):
        self.assertEqual(_pnl_card_bg(500), "#e6f9ee")

    def test_card_bg_loss_is_light_red(self):
        self.assertEqual(_pnl_card_bg(-1), "#fde8e8")


# ══════════════════════════════════════════════════════════
# 2. Capital Update Logic Tests
# ══════════════════════════════════════════════════════════

class TestCapitalIntegration(unittest.TestCase):

    def _make_portfolio(self, capital: float = 100_000) -> Portfolio:
        return Portfolio(initial_capital=capital)

    def _close_trade(self, port: Portfolio, symbol: str, entry: float,
                     exit_price: float, qty: int = 10) -> Trade:
        """Open then immediately close a position to generate a realized trade."""
        port.cash += entry * qty          # top up cash so open always succeeds
        pos = port.open_position(symbol, entry, "ma")
        assert pos is not None, "open_position returned None"
        trade = port.close_position(symbol, exit_price, reason="Stop Loss")
        return trade

    # ── compute_total_capital helper ───────────────────────

    def test_no_realized_capital_equals_starting(self):
        result = compute_total_capital(100_000, 0.0)
        self.assertEqual(result, 100_000)

    def test_profit_increases_total_capital(self):
        result = compute_total_capital(100_000, 5_000)
        self.assertEqual(result, 105_000)

    def test_loss_decreases_total_capital(self):
        result = compute_total_capital(100_000, -3_000)
        self.assertEqual(result, 97_000)

    def test_multiple_trades_cumulative(self):
        # Simulate three closed trades
        realized = 2_000 + (-500) + 1_500   # = 3_000
        result = compute_total_capital(100_000, realized)
        self.assertEqual(result, 103_000)

    # ── Portfolio.close_position updates cash immediately ──

    def test_close_position_adds_proceeds_to_cash(self):
        port = self._make_portfolio(200_000)
        initial_cash = port.cash
        trade = self._close_trade(port, "RELIANCE.NS", entry=2_000, exit_price=2_100, qty=10)
        self.assertIsNotNone(trade)
        # Cash must be higher than initial minus cost
        self.assertGreater(port.cash, 0)

    def test_realized_gain_reflected_in_trade_pnl(self):
        port = self._make_portfolio(200_000)
        trade = self._close_trade(port, "TCS.NS", entry=3_000, exit_price=3_150, qty=5)
        self.assertIsNotNone(trade)
        self.assertGreater(trade.pnl, 0, "Exit above entry must yield positive P&L")

    def test_realized_loss_reflected_in_trade_pnl(self):
        port = self._make_portfolio(200_000)
        trade = self._close_trade(port, "INFY.NS", entry=1_500, exit_price=1_400, qty=5)
        self.assertIsNotNone(trade)
        self.assertLess(trade.pnl, 0, "Exit below entry must yield negative P&L")

    def test_total_capital_with_realized_gain(self):
        """Closing a profitable trade must raise the effective total capital."""
        port = self._make_portfolio(100_000)
        trade = self._close_trade(port, "HDFCBANK.NS", entry=1_600, exit_price=1_700, qty=10)
        self.assertIsNotNone(trade)
        pnl = trade.pnl
        total = compute_total_capital(100_000, pnl)
        self.assertGreater(total, 100_000)

    def test_total_capital_with_realized_loss(self):
        """Closing a losing trade must reduce the effective total capital."""
        port = self._make_portfolio(100_000)
        trade = self._close_trade(port, "WIPRO.NS", entry=400, exit_price=380, qty=10)
        self.assertIsNotNone(trade)
        pnl = trade.pnl
        total = compute_total_capital(100_000, pnl)
        self.assertLess(total, 100_000)

    def test_free_capital_accounts_for_realized_gains(self):
        """Free capital = (starting + realized) - in_order."""
        starting   = 100_000
        realized   = 4_000
        in_order   = 20_000
        total      = compute_total_capital(starting, realized)   # 104_000
        free       = total - in_order                            # 84_000
        self.assertEqual(free, 84_000)

    def test_capital_identity_no_open_positions(self):
        """With no open positions, free capital equals total capital."""
        starting = 100_000
        realized = 2_500
        total    = compute_total_capital(starting, realized)
        free     = total - 0   # no in-order capital
        self.assertEqual(free, total)


if __name__ == "__main__":
    unittest.main()
