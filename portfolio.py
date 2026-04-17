"""
Portfolio Manager
=================
Tracks positions, cash, P&L, and enforces risk rules.
Works in both simulation and live mode.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from config import (
    CAPITAL, MAX_POSITION_PCT, MAX_OPEN_POSITIONS,
    STOP_LOSS_PCT, TRAILING_STOP_PCT,
    BREAKEVEN_TRIGGER_PCT, TSL_ACTIVATION_PCT,
    MIN_POSITION_VALUE, MIN_PNL_TO_BOOK,
)

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open position."""
    symbol:         str
    entry_price:    float
    quantity:       int
    entry_date:     datetime
    strategy:       str
    stop_loss:      float
    target:         Optional[float] = None   # Legacy; TSL handles profit exits
    trailing_stop:  Optional[float] = None
    highest_price:  Optional[float] = None   # Tracks highest high since entry

    # Dynamic exit state
    breakeven_set:  bool = False   # Has SL been moved to entry (break-even)?
    tsl_active:     bool = False   # Is the trailing stop currently active?

    # Gap handling state
    gap_state:      str = "none"         # "none" | "watching" | "recovered"
    gap_down_open:  Optional[float] = None  # Open price when gap-down detected

    @property
    def invested_value(self) -> float:
        return self.entry_price * self.quantity

    def current_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.quantity

    def current_pnl_pct(self, current_price: float) -> float:
        return (current_price / self.entry_price - 1) * 100

    def should_stop_loss(self, current_price: float) -> bool:
        """Check if current SL (initial or break-even) is hit."""
        return current_price <= self.stop_loss

    def should_take_profit(self, current_price: float) -> bool:
        """Static target — kept for legacy; no longer set on new positions."""
        return self.target is not None and current_price >= self.target

    def update_trailing_stop(self, current_price: float) -> bool:
        """
        Dynamic trailing stop with two phases:
          Phase 1 (+1.5% profit): move SL to entry price (break-even).
          Phase 2 (+2.0% profit): activate TSL at highest_high × (1 - 1.5%).
        Returns True only when the TSL itself is triggered (not the SL).
        """
        profit_pct = (current_price / self.entry_price - 1)

        # Phase 1 — break-even trigger
        if profit_pct >= BREAKEVEN_TRIGGER_PCT and not self.breakeven_set:
            self.stop_loss = self.entry_price
            self.breakeven_set = True

        # Phase 2 — trailing stop activation
        if profit_pct >= TSL_ACTIVATION_PCT:
            self.tsl_active = True
            if self.highest_price is None or current_price > self.highest_price:
                self.highest_price = current_price
            new_tsl = self.highest_price * (1 - TRAILING_STOP_PCT)
            if self.trailing_stop is None or new_tsl > self.trailing_stop:
                self.trailing_stop = new_tsl

        # TSL trigger check (only when TSL is active)
        if self.tsl_active and self.trailing_stop is not None and current_price <= self.trailing_stop:
            return True

        return False

    def to_dict(self) -> dict:
        return {
            "symbol":        self.symbol,
            "entry_price":   self.entry_price,
            "quantity":      self.quantity,
            "entry_date":    self.entry_date.isoformat(),
            "strategy":      self.strategy,
            "stop_loss":     self.stop_loss,
            "target":        self.target,
            "trailing_stop": self.trailing_stop,
            "highest_price": self.highest_price,
            "breakeven_set": self.breakeven_set,
            "tsl_active":    self.tsl_active,
            "gap_state":     self.gap_state,
            "gap_down_open": self.gap_down_open,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(
            symbol         = d["symbol"],
            entry_price    = d["entry_price"],
            quantity       = d["quantity"],
            entry_date     = datetime.fromisoformat(d["entry_date"]),
            strategy       = d["strategy"],
            stop_loss      = d["stop_loss"],
            target         = d.get("target"),
            trailing_stop  = d.get("trailing_stop"),
            highest_price  = d.get("highest_price"),
            breakeven_set  = d.get("breakeven_set", False),
            tsl_active     = d.get("tsl_active", False),
            gap_state      = d.get("gap_state", "none"),
            gap_down_open  = d.get("gap_down_open"),
        )


@dataclass
class Trade:
    """A completed trade record."""
    symbol:         str
    entry_date:     datetime
    exit_date:      datetime
    entry_price:    float
    exit_price:     float
    quantity:       int
    strategy:       str
    exit_reason:    str
    pnl:            float
    pnl_pct:        float
    holding_days:   int

    def to_dict(self) -> dict:
        return {
            "symbol":       self.symbol,
            "entry_date":   self.entry_date.isoformat(),
            "exit_date":    self.exit_date.isoformat(),
            "entry_price":  self.entry_price,
            "exit_price":   self.exit_price,
            "quantity":     self.quantity,
            "strategy":     self.strategy,
            "exit_reason":  self.exit_reason,
            "pnl":          self.pnl,
            "pnl_pct":      self.pnl_pct,
            "holding_days": self.holding_days,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        return cls(
            symbol       = d["symbol"],
            entry_date   = datetime.fromisoformat(d["entry_date"]),
            exit_date    = datetime.fromisoformat(d["exit_date"]),
            entry_price  = d["entry_price"],
            exit_price   = d["exit_price"],
            quantity     = d["quantity"],
            strategy     = d["strategy"],
            exit_reason  = d["exit_reason"],
            pnl          = d["pnl"],
            pnl_pct      = d["pnl_pct"],
            holding_days = d["holding_days"],
        )


class Portfolio:
    """Manages the full portfolio: cash, positions, and trade history."""

    def __init__(self, initial_capital: float = CAPITAL):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}   # symbol → Position
        self.trade_history: List[Trade] = []
        self.equity_curve: List[dict] = []          # For P&L chart
        self._record_equity()

    # ──────────────────────────────────────────
    # Entry / Exit
    # ──────────────────────────────────────────

    def can_open_position(self, symbol: str, entry_price: float) -> bool:
        """Check if we can open a new position."""
        if symbol in self.positions:
            logger.debug(f"Already in position for {symbol}")
            return False
        if len(self.positions) >= MAX_OPEN_POSITIONS:
            logger.debug(f"Max positions ({MAX_OPEN_POSITIONS}) reached")
            return False
        # Must be able to afford at least MIN_POSITION_VALUE worth of shares
        min_qty = math.ceil(MIN_POSITION_VALUE / entry_price)
        min_cost = min_qty * entry_price
        if min_cost > self.cash * 0.95:
            logger.debug(
                f"Insufficient cash for {symbol}: need ₹{min_cost:,.0f} "
                f"(min position), have ₹{self.cash:,.0f}"
            )
            return False
        return True

    def calculate_quantity(self, entry_price: float) -> int:
        """
        Calculate shares to buy based on position sizing.
        Enforces a minimum position value of MIN_POSITION_VALUE (₹5,000).
        """
        budget = min(
            self.initial_capital * MAX_POSITION_PCT,
            self.cash * 0.95,
        )
        quantity = int(budget / entry_price)

        # Ensure minimum position value
        min_qty = math.ceil(MIN_POSITION_VALUE / entry_price)
        if quantity < min_qty:
            # Upgrade to minimum if we can afford it
            if min_qty * entry_price <= self.cash * 0.95:
                quantity = min_qty
            else:
                quantity = int(self.cash * 0.95 / entry_price)

        return quantity

    def open_position(
        self,
        symbol: str,
        entry_price: float,
        strategy: str,
        entry_date: Optional[datetime] = None,
    ) -> Optional[Position]:
        """Open a new position."""
        if not self.can_open_position(symbol, entry_price):
            return None

        quantity = self.calculate_quantity(entry_price)
        if quantity == 0:
            logger.warning(f"Quantity 0 for {symbol} at ₹{entry_price:.2f}")
            return None

        cost = entry_price * quantity
        if cost > self.cash:
            quantity = int(self.cash * 0.95 / entry_price)
            cost = entry_price * quantity

        stop_loss = entry_price * (1 - STOP_LOSS_PCT)

        pos = Position(
            symbol        = symbol,
            entry_price   = entry_price,
            quantity      = quantity,
            entry_date    = entry_date or datetime.now(),
            strategy      = strategy,
            stop_loss     = stop_loss,
            target        = None,        # No static target — TSL handles profit exits
            highest_price = entry_price,
        )

        self.cash -= cost
        self.positions[symbol] = pos

        logger.info(
            f"OPENED {symbol}: {quantity} shares @ ₹{entry_price:.2f} | "
            f"Initial SL=₹{stop_loss:.2f} | TSL activates at +2% | "
            f"Cash remaining=₹{self.cash:,.0f}"
        )
        return pos

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        reason: str = "Signal",
        exit_date: Optional[datetime] = None,
    ) -> Optional[Trade]:
        """
        Close an existing position.

        Min P&L guard: for non-risk-management exits (Strategy Signal, EMA Exit),
        skip the close if |P&L| < MIN_PNL_TO_BOOK to avoid tiny transactions.
        Risk management exits (Stop Loss, Trailing Stop, Gap Down Exit, etc.)
        always execute regardless of P&L size.
        """
        if symbol not in self.positions:
            logger.warning(f"No position found for {symbol}")
            return None

        pos = self.positions[symbol]
        pnl_preview = (exit_price - pos.entry_price) * pos.quantity

        # Apply minimum P&L threshold only to discretionary exits
        _RISK_REASONS = {"Stop Loss", "Trailing Stop", "Gap Down Exit", "Manual Cancel", "End of Backtest"}
        if reason not in _RISK_REASONS and abs(pnl_preview) < MIN_PNL_TO_BOOK:
            logger.debug(
                f"Skipping {reason} exit for {symbol}: "
                f"P&L ₹{pnl_preview:.0f} below ₹{MIN_PNL_TO_BOOK} threshold"
            )
            return None

        pos = self.positions.pop(symbol)
        proceeds = exit_price * pos.quantity
        self.cash += proceeds

        pnl = (exit_price - pos.entry_price) * pos.quantity
        pnl_pct = (exit_price / pos.entry_price - 1) * 100
        exit_dt  = exit_date or datetime.now()
        holding  = (exit_dt - pos.entry_date).days

        trade = Trade(
            symbol       = symbol,
            entry_date   = pos.entry_date,
            exit_date    = exit_dt,
            entry_price  = pos.entry_price,
            exit_price   = exit_price,
            quantity     = pos.quantity,
            strategy     = pos.strategy,
            exit_reason  = reason,
            pnl          = round(pnl, 2),
            pnl_pct      = round(pnl_pct, 2),
            holding_days = holding,
        )
        self.trade_history.append(trade)

        emoji = "✅" if pnl >= 0 else "❌"
        logger.info(
            f"{emoji} CLOSED {symbol}: @ ₹{exit_price:.2f} | "
            f"P&L=₹{pnl:+,.0f} ({pnl_pct:+.2f}%) | "
            f"Reason={reason} | Cash=₹{self.cash:,.0f}"
        )
        return trade

    # ──────────────────────────────────────────
    # Portfolio Metrics
    # ──────────────────────────────────────────

    def total_equity(self, prices: Optional[Dict[str, float]] = None) -> float:
        """Total portfolio value = cash + open positions value."""
        equity = self.cash
        if prices:
            for sym, pos in self.positions.items():
                price = prices.get(sym, pos.entry_price)
                equity += price * pos.quantity
        else:
            # Use entry prices as estimate
            for pos in self.positions.values():
                equity += pos.entry_price * pos.quantity
        return equity

    def total_pnl(self, prices: Optional[Dict[str, float]] = None) -> float:
        return self.total_equity(prices) - self.initial_capital

    def total_pnl_pct(self, prices: Optional[Dict[str, float]] = None) -> float:
        return (self.total_equity(prices) / self.initial_capital - 1) * 100

    def _record_equity(self, prices: Optional[Dict[str, float]] = None):
        """Record current equity for the equity curve."""
        self.equity_curve.append({
            "timestamp": datetime.now(),
            "equity":    self.total_equity(prices),
            "cash":      self.cash,
            "positions": len(self.positions),
        })

    def get_statistics(self) -> dict:
        """Compute overall trading statistics."""
        trades = self.trade_history
        if not trades:
            return {"total_trades": 0}

        winning = [t for t in trades if t.pnl > 0]
        losing  = [t for t in trades if t.pnl <= 0]

        win_rate   = len(winning) / len(trades) * 100
        avg_win    = sum(t.pnl for t in winning) / max(len(winning), 1)
        avg_loss   = sum(t.pnl for t in losing)  / max(len(losing),  1)
        total_pnl  = sum(t.pnl for t in trades)
        profit_factor = abs(sum(t.pnl for t in winning) / sum(t.pnl for t in losing)) if losing else float("inf")

        # Max drawdown from equity curve
        if self.equity_curve:
            equities = [e["equity"] for e in self.equity_curve]
            peak     = equities[0]
            max_dd   = 0.0
            for eq in equities:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100
                if dd > max_dd:
                    max_dd = dd
        else:
            max_dd = 0.0

        return {
            "total_trades":    len(trades),
            "winning_trades":  len(winning),
            "losing_trades":   len(losing),
            "win_rate_pct":    round(win_rate, 2),
            "total_pnl_inr":   round(total_pnl, 2),
            "avg_win_inr":     round(avg_win, 2),
            "avg_loss_inr":    round(avg_loss, 2),
            "profit_factor":   round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_holding_days": round(sum(t.holding_days for t in trades) / len(trades), 1),
        }

    def get_trade_history_df(self) -> pd.DataFrame:
        """Return trade history as DataFrame."""
        if not self.trade_history:
            return pd.DataFrame()
        rows = []
        for t in self.trade_history:
            rows.append({
                "Symbol":       t.symbol.replace(".NS", ""),
                "Entry Date":   t.entry_date.strftime("%Y-%m-%d"),
                "Exit Date":    t.exit_date.strftime("%Y-%m-%d"),
                "Entry ₹":      t.entry_price,
                "Exit ₹":       t.exit_price,
                "Qty":          t.quantity,
                "P&L ₹":        t.pnl,
                "P&L %":        t.pnl_pct,
                "Strategy":     t.strategy,
                "Exit Reason":  t.exit_reason,
                "Holding Days": t.holding_days,
            })
        return pd.DataFrame(rows)

    def get_open_positions_df(self, prices: Optional[Dict[str, float]] = None) -> pd.DataFrame:
        """Return open positions as DataFrame."""
        if not self.positions:
            return pd.DataFrame()
        rows = []
        for sym, pos in self.positions.items():
            price = prices.get(sym, pos.entry_price) if prices else pos.entry_price
            rows.append({
                "Symbol":     sym.replace(".NS", ""),
                "Strategy":   pos.strategy,
                "Entry ₹":    pos.entry_price,
                "Current ₹":  round(price, 2),
                "Qty":        pos.quantity,
                "P&L ₹":      round(pos.current_pnl(price), 2),
                "P&L %":      round(pos.current_pnl_pct(price), 2),
                "Stop Loss ₹": round(pos.stop_loss, 2),
                "Target ₹":   round(pos.target, 2),
                "Entry Date":  pos.entry_date.strftime("%Y-%m-%d"),
            })
        return pd.DataFrame(rows)

    # ──────────────────────────────────────────
    # Serialisation (for disk persistence)
    # ──────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize portfolio to a JSON-safe dict."""
        equity_curve_serialized = []
        for e in self.equity_curve:
            entry = dict(e)
            ts = entry.get("timestamp")
            if isinstance(ts, datetime):
                entry["timestamp"] = ts.isoformat()
            equity_curve_serialized.append(entry)

        return {
            "initial_capital": self.initial_capital,
            "cash":            self.cash,
            "positions":       {sym: pos.to_dict() for sym, pos in self.positions.items()},
            "trade_history":   [t.to_dict() for t in self.trade_history],
            "equity_curve":    equity_curve_serialized,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        """Reconstruct a Portfolio from a serialized dict."""
        p = cls.__new__(cls)
        p.initial_capital = d["initial_capital"]
        p.cash            = d["cash"]
        p.positions       = {
            sym: Position.from_dict(pos_d)
            for sym, pos_d in d.get("positions", {}).items()
        }
        p.trade_history   = [
            Trade.from_dict(t) for t in d.get("trade_history", [])
        ]
        p.equity_curve    = []
        for entry in d.get("equity_curve", []):
            e = dict(entry)
            ts = e.get("timestamp")
            if isinstance(ts, str):
                try:
                    e["timestamp"] = datetime.fromisoformat(ts)
                except ValueError:
                    e["timestamp"] = datetime.now()
            p.equity_curve.append(e)
        return p
