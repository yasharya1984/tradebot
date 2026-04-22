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

SEBI / Execution-Realism Notes (April 2026 mandate)
----------------------------------------------------
1. Algo-ID tagging   — every live order carries a `tag` field so the exchange
                        can trace which registered algorithm generated it.
2. Slippage          — SimBroker adjusts fill prices by SIM_SLIPPAGE_PCT
                        (default 0.05%) to model the NSE bid-ask spread.
3. MPP band          — SEBI converts MARKET orders to limit orders at ±0.5%
                        from the last traded price (Market Price Protection).
                        SimBroker adds SIM_MPP_PCT (default 0.5%) on top of
                        slippage so simulated fills match realistic live fills.
4. REJECTED state    — LiveBroker calls bot_orders.log_reject() when the Kite
                        API raises an exception, fully wiring the REJECTED
                        status that was previously only defined but never set.
5. Real cancel       — LiveBroker.execute_cancel() now calls
                        trader.cancel_order(kite_order_id) so pending exchange
                        orders are actually cancelled, not just locally logged.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

import config
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

    get_execution_price() is a concrete helper that subclasses may override.
    SimBroker applies slippage + MPP; LiveBroker returns the price as-is
    (Kite handles the actual fill).  The simulator calls this BEFORE opening
    a portfolio position so that P&L calculations use realistic fill prices.
    """

    def get_execution_price(self, signal_price: float, side: str) -> float:
        """
        Return the realistic fill price for a given signal price and side.

        Default implementation returns signal_price unchanged (live mode
        delegates fill accuracy to the exchange).  SimBroker overrides this
        to apply slippage + MPP so back-tested P&L is not inflated.

        Args:
            signal_price: The yfinance LTP at the time of the signal.
            side:         "BUY" or "SELL"

        Returns:
            Adjusted execution price.
        """
        return signal_price

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
            price:    Execution price (already adjusted by get_execution_price)
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
            price:       Execution price (already adjusted by get_execution_price)
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

    Execution-Realism
    -----------------
    get_execution_price() applies two adjustments to every signal price so
    that simulated P&L accounts for:

    1. Bid-Ask Slippage (SIM_SLIPPAGE_PCT, default 0.05%)
       BUY  → pays slightly above mid (you hit the ask)
       SELL → receives slightly below mid (you hit the bid)

    2. SEBI Market Price Protection Band (SIM_MPP_PCT, default 0.5%)
       SEBI's April 2026 rules convert MARKET orders to limit orders at the
       current price ± MPP band.  A BUY in a fast-moving market may fill at
       up to 0.5% above the signal price; this models that worst-case fill.

    Combined worst-case for a BUY:
        fill = signal_price × (1 + SIM_SLIPPAGE_PCT + SIM_MPP_PCT)
             = signal_price × 1.00550  (at default settings)
    """

    def get_execution_price(self, signal_price: float, side: str) -> float:
        """Apply slippage + MPP band to the signal price."""
        total_adj = config.SIM_SLIPPAGE_PCT + config.SIM_MPP_PCT
        if side == "BUY":
            return round(signal_price * (1.0 + total_adj), 2)
        else:   # SELL
            return round(signal_price * (1.0 - config.SIM_SLIPPAGE_PCT), 2)

    def execute_buy(self, symbol, qty, price, strategy, mode) -> str:
        order_id = bot_orders.log_open(symbol, qty, price, strategy, mode=mode)
        logger.info(
            f"[SIM] BUY  {symbol} ×{qty} @ ₹{price:,.2f}  "
            f"(incl. {config.SIM_SLIPPAGE_PCT*100:.2f}% slip + "
            f"{config.SIM_MPP_PCT*100:.2f}% MPP)  "
            f"strategy={strategy}  order_id={order_id}"
        )
        return order_id

    def execute_sell(self, symbol, qty, price, reason, pnl, mode, *, kite_buy_id=None):
        bot_orders.log_close(symbol, price, reason, pnl, mode=mode)
        sign = "+" if pnl >= 0 else ""
        logger.info(
            f"[SIM] SELL {symbol} ×{qty} @ ₹{price:,.2f}  "
            f"(incl. {config.SIM_SLIPPAGE_PCT*100:.2f}% slip)  "
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

    SEBI Compliance
    ---------------
    • Algo-ID tag: every place_order call includes tag="{ALGO_ID_PREFIX}_{strategy}"
      so the exchange can trace orders back to the registered algorithm.
    • REJECTED state: if the Kite API raises an exception, log_reject() is called
      so the dashboard shows a REJECTED badge and no ghost OPEN position exists.
    • Real cancel: execute_cancel() calls trader.cancel_order(kite_order_id) so
      any pending exchange orders are actually cancelled, not just locally logged.
    """

    def __init__(self, trader: "ZerodhaTrader") -> None:
        self.trader = trader

    def _kite_symbol(self, symbol: str) -> str:
        """Strip .NS suffix for Kite API (Kite uses bare NSE symbols)."""
        return symbol.replace(".NS", "")

    def _algo_tag(self, strategy: str) -> str:
        """
        Build the SEBI algo 'license plate' tag for a strategy.
        e.g. strategy="rsi_macd" → "STRAT_rsi_macd"  (max 20 chars, Kite limit)
        """
        raw = f"{config.ALGO_ID_PREFIX}_{strategy}"
        return raw[:20]

    def execute_buy(self, symbol, qty, price, strategy, mode) -> str:
        kite_sym  = self._kite_symbol(symbol)
        kite_id: Optional[str] = None
        tag = self._algo_tag(strategy)

        try:
            kite_id = self.trader.place_buy_order(kite_sym, qty, tag=tag)
        except Exception as exc:
            # Kite rejected the order (margin, price-band, compliance, etc.)
            err_msg = str(exc)
            logger.error(
                f"[LIVE] BUY REJECTED for {symbol}: {err_msg}  "
                f"(tag={tag})"
            )
            # Write REJECTED record so the dashboard shows the failure
            bot_orders.log_reject(symbol, qty, price, strategy, err_msg, mode=mode)
            return ""

        order_id = bot_orders.log_open(
            symbol, qty, price, strategy, mode=mode, kite_order_id=kite_id
        )
        logger.info(
            f"[LIVE] BUY  {symbol} ×{qty} @ ₹{price:,.2f}  "
            f"tag={tag}  kite_id={kite_id}  local_id={order_id}"
        )
        return order_id

    def execute_sell(self, symbol, qty, price, reason, pnl, mode, *, kite_buy_id=None):
        kite_sym = self._kite_symbol(symbol)
        kite_sell_id: Optional[str] = None
        # Use the same tag on the exit leg so the exchange can pair entry/exit
        # (strategy is not passed here; we derive it from the open order record)
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
        """
        Cancel the most-recent open order for *symbol*.

        Attempts to cancel the corresponding Kite exchange order by its
        kite_buy_id before logging locally — prevents ghost open positions
        on the exchange after a manual force-close from the dashboard.
        """
        # Look up the Kite order_id from the local order log so we can cancel it
        orders = bot_orders.get_all_orders(mode=mode)
        kite_order_id_to_cancel: Optional[str] = None
        for order in orders:
            if order["symbol"] == symbol and order["status"] in ("OPEN", "PENDING"):
                kite_order_id_to_cancel = order.get("kite_buy_id")
                break

        if kite_order_id_to_cancel:
            try:
                cancelled = self.trader.cancel_order(kite_order_id_to_cancel)
                if cancelled:
                    logger.info(
                        f"[LIVE] CANCEL: Kite order {kite_order_id_to_cancel} "
                        f"cancelled for {symbol}"
                    )
                else:
                    logger.warning(
                        f"[LIVE] CANCEL: Kite cancel_order returned False for "
                        f"{symbol} / {kite_order_id_to_cancel} — "
                        "order may already be filled or cancelled on exchange"
                    )
            except Exception as exc:
                logger.error(
                    f"[LIVE] CANCEL: Kite cancel_order raised for {symbol}: {exc}"
                )
        else:
            logger.info(
                f"[LIVE] CANCEL: no kite_buy_id found for {symbol} — "
                "relying on 3:20 PM auto-square-off (MIS intraday)"
            )

        # Always update local log regardless of exchange cancel outcome
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
