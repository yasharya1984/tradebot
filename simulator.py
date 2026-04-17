"""
Simulation & Backtesting Engine
================================
Two modes:
  1. Backtest  – Runs strategy over historical data; shows what would have happened
  2. Paper Trade – Runs in real-time but with virtual money (no real orders)

Compare all strategies side-by-side to find the best performer.
"""

import logging
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from config import CAPITAL, BACKTEST_PERIOD_DAYS
from data_fetcher import DataFetcher
from portfolio import Portfolio, Position
from stock_selector import StockSelector
from strategies import STRATEGY_MAP
import trade_store
import bot_orders

logger = logging.getLogger(__name__)


class BacktestResult:
    """Holds results from a single backtest run."""

    def __init__(self, strategy_name: str, symbol: str):
        self.strategy_name = strategy_name
        self.symbol        = symbol
        self.portfolio     = Portfolio(CAPITAL)
        self.signals_df: Optional[pd.DataFrame] = None

    @property
    def stats(self) -> dict:
        s = self.portfolio.get_statistics()
        s["final_equity"] = round(self.portfolio.total_equity(), 2)
        s["total_return_pct"] = round(self.portfolio.total_pnl_pct(), 2)
        return s


class Simulator:
    """Backtesting and paper trading engine."""

    def __init__(self, data_fetcher: DataFetcher):
        self.fetcher       = data_fetcher
        self.selector      = StockSelector(data_fetcher)
        self._paper_portfolios: Dict[str, Portfolio] = {}
        self._paper_selected:   List[dict] = []
        self._trade_mode   = "sim"   # "sim" | "live", set by initialize_paper_trading

    # ──────────────────────────────────────────────────────
    # BACKTEST: Single Strategy × Single Symbol
    # ──────────────────────────────────────────────────────

    def backtest_single(
        self,
        strategy_name: str,
        symbol: str,
        df: pd.DataFrame,
    ) -> BacktestResult:
        """
        Backtest one strategy on one stock over its full historical data.

        Returns:
            BacktestResult with portfolio and signals DataFrame
        """
        result = BacktestResult(strategy_name, symbol)
        portfolio = result.portfolio

        StrategyClass = STRATEGY_MAP.get(strategy_name)
        if not StrategyClass:
            logger.error(f"Unknown strategy: {strategy_name}")
            return result

        strategy = StrategyClass()
        signals_df = strategy.generate_signals(df)
        result.signals_df = signals_df

        for i, (date, row) in enumerate(signals_df.iterrows()):
            price = float(row["Close"])
            signal = row.get("Signal", "HOLD")

            # Update trailing stops for open positions
            if symbol in portfolio.positions:
                pos = portfolio.positions[symbol]
                trailing_triggered = pos.update_trailing_stop(price)

                # Check exit conditions
                if trailing_triggered:
                    portfolio.close_position(symbol, price, "Trailing Stop", date)
                elif pos.should_stop_loss(price):
                    portfolio.close_position(symbol, price, "Stop Loss", date)
                elif pos.should_take_profit(price):
                    portfolio.close_position(symbol, price, "Take Profit", date)
                elif signal == "SELL":
                    portfolio.close_position(symbol, price, "Strategy Signal", date)

            # Open new position on BUY signal
            elif signal == "BUY" and symbol not in portfolio.positions:
                portfolio.open_position(symbol, price, strategy_name, date)

            portfolio._record_equity()

        # Close any remaining position at end of backtest
        if symbol in portfolio.positions:
            last_price = float(signals_df["Close"].iloc[-1])
            portfolio.close_position(symbol, last_price, "End of Backtest")

        return result

    # ──────────────────────────────────────────────────────
    # BACKTEST: All Strategies × Top Stocks (Comparison)
    # ──────────────────────────────────────────────────────

    def run_full_comparison(
        self,
        period_days: int = BACKTEST_PERIOD_DAYS,
    ) -> pd.DataFrame:
        """
        Run all three strategies across top momentum stocks.
        Returns a comparison DataFrame showing performance of each strategy.

        Args:
            period_days: Historical period to backtest

        Returns:
            DataFrame comparing strategies by total return, win rate, etc.
        """
        logger.info("=" * 60)
        logger.info("Starting Full Strategy Comparison Backtest")
        logger.info("=" * 60)

        # Select top stocks
        selected, _ = self.selector.refresh_selection()
        if not selected:
            logger.error("No stocks selected")
            return pd.DataFrame()

        rows = []

        for strategy_name in STRATEGY_MAP.keys():
            logger.info(f"\n── Strategy: {strategy_name.upper()} ──")

            strategy_portfolios = []
            all_trades = []

            for stock_info in selected:
                symbol = stock_info["symbol"]
                df = stock_info.get("data")

                if df is None or df.empty:
                    df = self.fetcher.get_historical(symbol, period_days)
                if df.empty:
                    continue

                try:
                    result = self.backtest_single(strategy_name, symbol, df)
                    stats = result.stats
                    stats["symbol"] = symbol
                    strategy_portfolios.append(stats)
                    all_trades.extend(result.portfolio.trade_history)
                except Exception as e:
                    logger.error(f"Backtest failed for {symbol}/{strategy_name}: {e}")

            if not strategy_portfolios:
                continue

            # Aggregate: treat as portfolio of all stocks
            total_pnl = sum(t.pnl for t in all_trades)
            winning   = [t for t in all_trades if t.pnl > 0]
            losing    = [t for t in all_trades if t.pnl <= 0]
            win_rate  = len(winning) / max(len(all_trades), 1) * 100
            avg_return = total_pnl / max(len(all_trades), 1)

            rows.append({
                "Strategy":          STRATEGY_MAP[strategy_name].name,
                "Total P&L (₹)":     round(total_pnl, 2),
                "Total Return (%)":  round(total_pnl / (CAPITAL * len(selected)) * 100, 2),
                "Total Trades":      len(all_trades),
                "Win Rate (%)":      round(win_rate, 2),
                "Avg Trade P&L (₹)": round(avg_return, 2),
                "Winning Trades":    len(winning),
                "Losing Trades":     len(losing),
            })

        comparison = pd.DataFrame(rows)
        if not comparison.empty:
            comparison = comparison.sort_values("Total Return (%)", ascending=False)

        return comparison

    def backtest_strategy_on_stocks(
        self,
        strategy_name: str,
        period_days: int = BACKTEST_PERIOD_DAYS,
    ) -> Dict[str, BacktestResult]:
        """Run one strategy on all selected stocks. Returns per-stock results."""
        selected, _ = self.selector.refresh_selection()
        results = {}

        for stock_info in selected:
            symbol = stock_info["symbol"]
            df = stock_info.get("data") or self.fetcher.get_historical(symbol, period_days)
            if df.empty:
                continue
            try:
                result = self.backtest_single(strategy_name, symbol, df)
                results[symbol] = result
            except Exception as e:
                logger.error(f"Failed {strategy_name}/{symbol}: {e}")

        return results

    # ──────────────────────────────────────────────────────
    # PAPER TRADING (real-time simulation)
    # ──────────────────────────────────────────────────────

    def initialize_paper_trading(
        self,
        strategy_names: Optional[List[str]] = None,
        mode: str = "sim",
    ):
        """
        Set up paper trading portfolios (one per strategy).
        Loads previously saved state from disk (mode-specific) if available
        so trades survive application restarts.
        """
        self._trade_mode = mode
        names = strategy_names or list(STRATEGY_MAP.keys())
        for name in names:
            saved = trade_store.load_portfolio(name, mode=mode)
            if saved:
                try:
                    self._paper_portfolios[name] = Portfolio.from_dict(saved)
                    logger.info(
                        f"Paper trading [{mode}]: loaded state for [{name}] "
                        f"({len(self._paper_portfolios[name].trade_history)} trades, "
                        f"cash=₹{self._paper_portfolios[name].cash:,.0f})"
                    )
                except Exception as exc:
                    logger.warning(f"Could not restore [{name}] state: {exc} — starting fresh")
                    self._paper_portfolios[name] = Portfolio(CAPITAL)
            else:
                self._paper_portfolios[name] = Portfolio(CAPITAL)

        # Lock in the stock selection at startup so it doesn't change mid-session
        self._paper_selected, _ = self.selector.refresh_selection()
        logger.info(
            f"Paper trading [{mode}] initialized for strategies: {names} | "
            f"Stocks locked: {[s['symbol'] for s in self._paper_selected]}"
        )

    def reset_paper_trading(
        self,
        strategy_names: Optional[List[str]] = None,
        mode: Optional[str] = None,
    ):
        """
        Forget ALL saved trades and restart with fresh capital.
        Deletes on-disk state (portfolios + order log) and clears memory.
        """
        m = mode or self._trade_mode
        pf_count = trade_store.delete_all_portfolios(mode=m)
        ord_count = bot_orders.delete_all_orders(mode=m)
        self._paper_portfolios = {}
        self._paper_selected   = []
        logger.info(
            f"Paper trading RESET [{m}]: deleted {pf_count} portfolio(s), "
            f"{ord_count} order(s)"
        )
        if strategy_names:
            self.initialize_paper_trading(strategy_names, mode=m)

    def force_close_position(
        self,
        strategy_name: str,
        symbol: str,
    ) -> bool:
        """
        Manually force-close an open position at the current market price.
        Records the close in the portfolio AND the bot order log.

        Returns True if the position was found and closed.
        """
        portfolio = self._paper_portfolios.get(strategy_name)
        if not portfolio or symbol not in portfolio.positions:
            logger.warning(f"force_close: no open position [{strategy_name}] {symbol}")
            return False

        pos = portfolio.positions[symbol]
        current_price = (
            self.fetcher.get_current_price(symbol) or pos.entry_price
        )
        trade = portfolio.close_position(symbol, current_price, "Manual Cancel")
        if not trade:
            return False

        pnl = trade.pnl
        # Update order log
        bot_orders.log_cancel(symbol, mode=self._trade_mode)
        # Persist updated portfolio
        trade_store.save_portfolio(
            strategy_name, portfolio.to_dict(), mode=self._trade_mode
        )
        logger.info(
            f"force_close: closed {symbol} @ ₹{current_price:.2f} "
            f"P&L ₹{pnl:+,.0f} [{self._trade_mode}]"
        )
        return True

    def paper_trading_tick(self, strategy_name: str) -> dict:
        """
        Process one tick of paper trading.
        Fetches latest prices, generates signals, updates paper portfolio.

        Returns dict with signals and portfolio state.
        """
        if strategy_name not in self._paper_portfolios:
            self.initialize_paper_trading([strategy_name])

        portfolio = self._paper_portfolios[strategy_name]
        StrategyClass = STRATEGY_MAP[strategy_name]
        strategy = StrategyClass()

        # Use the stock selection locked at startup — never re-scan mid-session
        selected = getattr(self, "_paper_selected", None)
        if not selected:
            self._paper_selected, _ = self.selector.refresh_selection()
            selected = self._paper_selected

        tick_results = {}
        mode = self._trade_mode

        for stock_info in selected:
            symbol = stock_info["symbol"]
            df = self.fetcher.get_historical(symbol, period_days=60)
            if df.empty:
                continue

            current_price = self.fetcher.get_current_price(symbol) or float(df["Close"].iloc[-1])
            signal = strategy.get_current_signal(df)

            # Process exits — log each close to bot_orders
            if symbol in portfolio.positions:
                pos = portfolio.positions[symbol]
                trailing_triggered = pos.update_trailing_stop(current_price)
                reason = None
                if trailing_triggered:
                    reason = "Trailing Stop"
                elif pos.should_stop_loss(current_price):
                    reason = "Stop Loss"
                elif pos.should_take_profit(current_price):
                    reason = "Take Profit"
                elif signal == "SELL":
                    reason = "Strategy Signal"

                if reason:
                    trade = portfolio.close_position(symbol, current_price, reason)
                    if trade:
                        try:
                            bot_orders.log_close(
                                symbol, current_price, reason, trade.pnl, mode=mode
                            )
                        except Exception:
                            pass

            # Process entries — log each open to bot_orders
            elif signal == "BUY":
                new_pos = portfolio.open_position(symbol, current_price, strategy_name)
                if new_pos:
                    try:
                        bot_orders.log_open(
                            symbol, new_pos.quantity, current_price,
                            strategy_name, mode=mode,
                        )
                    except Exception:
                        pass

            tick_results[symbol] = {
                "signal":        signal,
                "price":         current_price,
                "in_position":   symbol in portfolio.positions,
            }

        current_prices = {s: tick_results[s]["price"] for s in tick_results}
        portfolio._record_equity(current_prices)

        # Persist state to disk so it survives app restarts
        try:
            trade_store.save_portfolio(strategy_name, portfolio.to_dict(), mode=mode)
        except Exception as exc:
            logger.warning(f"Failed to persist portfolio [{strategy_name}]: {exc}")

        return {
            "strategy":     strategy_name,
            "timestamp":    datetime.now().isoformat(),
            "signals":      tick_results,
            "portfolio":    {
                "cash":      round(portfolio.cash, 2),
                "equity":    round(portfolio.total_equity(current_prices), 2),
                "pnl":       round(portfolio.total_pnl(current_prices), 2),
                "pnl_pct":   round(portfolio.total_pnl_pct(current_prices), 2),
                "positions": len(portfolio.positions),
            },
        }

    def get_paper_portfolio(self, strategy_name: str) -> Optional[Portfolio]:
        return self._paper_portfolios.get(strategy_name)

    def get_all_paper_portfolios(self) -> Dict[str, Portfolio]:
        return self._paper_portfolios

    def sync_live_portfolio(self, strategy_name: str, trader) -> dict:
        """
        Sync the in-memory portfolio for `strategy_name` with actual Kite
        positions so the bot knows what's already open.

        Only positions that the bot doesn't already track are imported.
        The portfolio cash is adjusted to respect CAPITAL as the trading cap
        (excess Kite balance beyond CAPITAL is ignored).

        Args:
            strategy_name: Which strategy portfolio to sync into.
            trader: A connected ZerodhaTrader instance.

        Returns:
            dict with keys: positions_loaded, positions_skipped, error
        """
        result = {"positions_loaded": 0, "positions_skipped": 0, "error": None}

        if strategy_name not in self._paper_portfolios:
            self.initialize_paper_trading([strategy_name])

        portfolio = self._paper_portfolios[strategy_name]

        try:
            kite_positions = trader.get_positions()   # list of dicts from Kite
        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"sync_live_portfolio: failed to fetch positions: {exc}")
            return result

        for pos in kite_positions:
            symbol_kite = pos.get("tradingsymbol", "")
            symbol_ns   = f"{symbol_kite}.NS"
            quantity    = pos.get("quantity", 0)
            avg_price   = pos.get("average_price", 0.0)

            if quantity <= 0 or avg_price <= 0:
                continue  # flat or short — skip

            if symbol_ns in portfolio.positions:
                result["positions_skipped"] += 1
                continue  # already tracked

            # Reconstruct a Position from Kite data
            new_pos = Position(
                symbol        = symbol_ns,
                entry_price   = float(avg_price),
                quantity      = int(quantity),
                entry_date    = datetime.now(),
                strategy      = strategy_name,
                stop_loss     = float(avg_price) * (1 - 0.02),
                target        = float(avg_price) * (1 + 0.04),
                highest_price = float(avg_price),
            )
            portfolio.positions[symbol_ns] = new_pos
            portfolio.cash -= float(avg_price) * int(quantity)
            result["positions_loaded"] += 1
            # Log as OPEN order so it shows in bot orders panel
            try:
                bot_orders.log_open(
                    symbol_ns, int(quantity), float(avg_price),
                    strategy_name, mode="live",
                    kite_order_id=pos.get("order_id"),
                )
            except Exception:
                pass
            logger.info(
                f"sync_live_portfolio: imported {symbol_kite} "
                f"×{quantity} @ ₹{avg_price:.2f} into [{strategy_name}]"
            )

        return result

    # ──────────────────────────────────────────────────────
    # EQUITY CURVE
    # ──────────────────────────────────────────────────────

    def get_equity_curve_df(self, strategy_name: str) -> pd.DataFrame:
        """Return equity curve as DataFrame for charting."""
        portfolio = self._paper_portfolios.get(strategy_name)
        if not portfolio or not portfolio.equity_curve:
            return pd.DataFrame()
        df = pd.DataFrame(portfolio.equity_curve)
        df["return_pct"] = (df["equity"] / CAPITAL - 1) * 100
        return df
