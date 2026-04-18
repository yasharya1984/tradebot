"""
Unified Execution Interface
============================
Provides a single `Broker` abstraction so that the trading algorithm never
needs to know whether it is running in simulation or live mode.

The algorithm always calls:
    broker.execute_buy(symbol, qty, price, strategy, mode)
    broker.execute_sell(symbol, qty, price, reason, pnl, mode)
    broker.execute_cancel(symbol, mode)

In SIM mode  → SimBroker logs the action to trade_data/sim/orders.json only.
In LIVE mode → LiveBroker forwards the order to the Zerodha Kite API *and*
               mirrors the action to trade_data/live/orders.json so that
               both modes share identical file structures.

This guarantees:
  - Every fix to entry/exit logic tested in sim applies identically in live.
  - trade_data/{sim,live}/ are always kept separate; no data mixing.
  - The only code that differs between sim and live is inside this module.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

import bot_orders

if TYPE_CHECKING:
    from zerodha_trader import ZerodhaTrader

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Abstract base
# ──────────────────────────────────────────────────────────

class Broker(ABC):
    """
    Abstract execution interface.

    Both SimBroker and LiveBroker implement these three methods so that the
    trading loop (simulator.paper_trading_tick) is identical for both modes.
    """

    @abstractmethod
    def execute_buy(
        self,
        symbol: str,
        qty: int,
        price: float,
        strategy: str,
        mode: str,
    ) -> str:
        """
        Execute (or simulate) a buy order.

        Args:
            symbol:   NSE symbol with .NS suffix (e.g. "RELIANCE.NS")
            qty:      Number of shares
            price:    Execution price
            strategy: Strategy name ("ma" | "rsi_macd" | "momentum")
            mode:     "sim" | "live"

        Returns:
            order_id string (UUID for sim; Kite order_id for live)
        """

    @abstractmethod
    def execute_sell(
        self,
        symbol: str,
        qty: int,
        price: float,
        reason: str,
        pnl: float,
        mode: str,
        *,
        kite_buy_id: Optional[str] = None,
    ) -> None:
        """
        Execute (or simulate) a sell order.

        Args:
            symbol:      NSE symbol with .NS suffix
            qty:         Number of shares being sold
            price:       Execution price
            reason:      Exit reason ("Trailing Stop" | "Stop Loss" | …)
            pnl:         Realised P&L in INR
            mode:        "sim" | "live"
            kite_buy_id: Optional Kite buy-leg order_id for linking records
        """

    @abstractmethod
    def execute_cancel(self, symbol: str, mode: str) -> None:
        """
        Mark the most-recent open order for *symbol* as CANCELLED.
        Live implementations should also attempt to cancel any pending
        bracket/cover orders at the broker.
        """


# ──────────────────────────────────────────────────────────
# Simulation broker (paper trading — no real money)
# ──────────────────────────────────────────────────────────

class SimBroker(Broker):
    """
    Simulation-only execution.

    Writes to trade_data/sim/orders.json.  No real orders are placed.
    """

    def execute_buy(self, symbol, qty, price, strategy, mode) -> str:
        order_id = bot_orders.log_open(symbol, qty, price, strategy, mode=mode)
        logger.info(
            f"[SIM] BUY  {symbol} ×{qty} @ ₹{price:,.2f}  "
            f"strategy={strategy}  order_id={order_id}"
        )
        return order_id

    def execute_sell(self, symbol, qty, price, reason, pnl, mode, *, kite_buy_id=None):
        bot_orders.log_close(symbol, price, reason, pnl, mode=mode)
        sign = "+" if pnl >= 0 else ""
        logger.info(
            f"[SIM] SELL {symbol} ×{qty} @ ₹{price:,.2f}  "
            f"reason={reason}  pnl=₹{sign}{pnl:,.0f}"
        )

    def execute_cancel(self, symbol, mode):
        bot_orders.log_cancel(symbol, mode=mode)
        logger.info(f"[SIM] CANCEL {symbol}")


# ──────────────────────────────────────────────────────────
# Live broker (real money via Zerodha Kite)
# ──────────────────────────────────────────────────────────

class LiveBroker(Broker):
    """
    Live execution via the Zerodha Kite Connect API.

    Every order is *also* mirrored to trade_data/live/orders.json using the
    same format as SimBroker so that the dashboard, Telegram bot, and history
    page work identically for both modes.

    If the Kite call fails, the error is logged and the local order record is
    still written with kite_buy_id=None so state stays consistent.
    """

    def __init__(self, trader: "ZerodhaTrader") -> None:
        self.trader = trader

    def _kite_symbol(self, symbol: str) -> str:
        """Strip .NS suffix for Kite API (Kite uses bare NSE symbols)."""
        return symbol.replace(".NS", "")

    def execute_buy(self, symbol, qty, price, strategy, mode) -> str:
        kite_sym  = self._kite_symbol(symbol)
        kite_id: Optional[str] = None

        try:
            kite_id = self.trader.place_buy_order(kite_sym, qty)
        except Exception as exc:
            logger.error(f"[LIVE] BUY order failed for {symbol}: {exc}")

        order_id = bot_orders.log_open(
            symbol, qty, price, strategy, mode=mode, kite_order_id=kite_id
        )
        logger.info(
            f"[LIVE] BUY  {symbol} ×{qty} @ ₹{price:,.2f}  "
            f"kite_id={kite_id}  local_id={order_id}"
        )
        return order_id

    def execute_sell(self, symbol, qty, price, reason, pnl, mode, *, kite_buy_id=None):
        kite_sym = self._kite_symbol(symbol)
        kite_sell_id: Optional[str] = None

        try:
            kite_sell_id = self.trader.place_sell_order(kite_sym, qty)
        except Exception as exc:
            logger.error(f"[LIVE] SELL order failed for {symbol}: {exc}")

        bot_orders.log_close(
            symbol, price, reason, pnl, mode=mode, kite_sell_id=kite_sell_id
        )
        sign = "+" if pnl >= 0 else ""
        logger.info(
            f"[LIVE] SELL {symbol} ×{qty} @ ₹{price:,.2f}  "
            f"reason={reason}  pnl=₹{sign}{pnl:,.0f}  kite_sell_id={kite_sell_id}"
        )

    def execute_cancel(self, symbol, mode):
        # For live mode, cancel any pending stop-loss orders for this symbol.
        # We rely on the auto-square-off at 3:20 PM for intraday MIS positions,
        # but we still log the cancel so the dashboard reflects the state.
        bot_orders.log_cancel(symbol, mode=mode)
        logger.info(f"[LIVE] CANCEL (local log) {symbol}")


# ──────────────────────────────────────────────────────────
# Factory helper
# ──────────────────────────────────────────────────────────

def build_broker(mode: str, trader: Optional["ZerodhaTrader"] = None) -> Broker:
    """
    Return the right Broker for the given mode.

    Args:
        mode:   "sim" | "live"
        trader: A connected ZerodhaTrader instance (required when mode="live")

    Raises:
        ValueError: if mode="live" but trader is None or not connected.
    """
    if mode == "live":
        if trader is None or not trader.is_connected:
            raise ValueError(
                "LiveBroker requires a connected ZerodhaTrader instance. "
                "Ensure config.ZERODHA_ACCESS_TOKEN is set and trader.connect() "
                "returned True before calling build_broker('live', trader)."
            )
        return LiveBroker(trader)
    return SimBroker()
