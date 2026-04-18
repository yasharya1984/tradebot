"""
Simulation & Backtesting Engine
================================
Two modes:
  1. Backtest  – Runs strategy over historical data; shows what would have happened
  2. Paper Trade – Runs in real-time but with virtual money (no real orders)

Compare all strategies side-by-side to find the best performer.
"""

import logging
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

from config import CAPITAL, BACKTEST_PERIOD_DAYS, EMA_PERIOD, EMA_TIMEFRAME, STOP_LOSS_PCT
from data_fetcher import DataFetcher
from portfolio import Portfolio, Position
from stock_selector import StockSelector
from strategies import STRATEGY_MAP
import trade_store
import bot_orders
from execution import Broker, SimBroker
from market_utils import is_market_open as _is_market_open
try:
    import tg_bot as _tg
except Exception:
    _tg = None

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
        # _paper_selected_multi: keyed by strategy name → strategy-specific stock list.
        # Populated lazily on the first paper_trading_tick() call.
        self._paper_selected_multi: Dict[str, List[dict]] = {}
        self._trade_mode   = "sim"   # "sim" | "live", set by initialize_paper_trading
        self._broker: Broker = SimBroker()  # default; overridden by initialize_paper_trading

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

        # Pre-compute 20-period EMA on daily close for the EMA exit filter
        signals_df = signals_df.copy()
        signals_df["EMA20"] = signals_df["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()

        result.signals_df = signals_df

        for date, row in signals_df.iterrows():
            close_price = float(row["Close"])
            open_price  = float(row.get("Open", close_price))
            signal      = row.get("Signal", "HOLD")
            ema20       = float(row["EMA20"])

            if symbol in portfolio.positions:
                pos = portfolio.positions[symbol]

                # ── Gap-Down Handling ──────────────────────────────────────
                # If the daily Open is below the current stop loss, give the
                # stock a chance to recover within that candle before exiting.
                if open_price < pos.stop_loss:
                    candle_green = close_price > open_price
                    if candle_green:
                        # Recovery: tighten SL to the candle's Low and hold
                        new_sl = float(row["Low"])
                        if new_sl > pos.entry_price * (1 - STOP_LOSS_PCT * 2):
                            pos.stop_loss = new_sl
                        pos.gap_state = "recovered"
                        portfolio._record_equity()
                        continue  # Don't exit — gap filled
                    else:
                        # Red candle after gap down: exit at open
                        portfolio.close_position(symbol, open_price, "Gap Down Exit", date)
                        portfolio._record_equity()
                        continue

                # ── Dynamic Trailing Stop ─────────────────────────────────
                trailing_triggered = pos.update_trailing_stop(close_price)

                # ── Exit Conditions (priority order) ─────────────────────
                if trailing_triggered:
                    portfolio.close_position(symbol, close_price, "Trailing Stop", date)
                elif pos.should_stop_loss(close_price):
                    portfolio.close_position(symbol, close_price, "Stop Loss", date)
                elif close_price < ema20:
                    # Price closed below 20 EMA — secondary exit filter
                    portfolio.close_position(symbol, close_price, "EMA Exit", date)
                elif signal == "SELL":
                    portfolio.close_position(symbol, close_price, "Strategy Signal", date)

            # Open new position on BUY signal
            elif signal == "BUY":
                portfolio.open_position(symbol, close_price, strategy_name, date)

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

        # Select stocks for all strategies in one batch
        selected_multi = self.selector.select_stocks_multi()
        if not any(selected_multi.values()):
            logger.error("No stocks selected by any strategy")
            return pd.DataFrame()

        rows = []

        for strategy_name in STRATEGY_MAP.keys():
            logger.info(f"\n── Strategy: {strategy_name.upper()} ──")

            # Each strategy backtests on its own candidate pool
            selected = selected_multi.get(strategy_name, [])
            if not selected:
                # Fall back to the union if this strategy produced no candidates
                seen: Dict[str, dict] = {}
                for stocks in selected_multi.values():
                    for s in stocks:
                        if s["symbol"] not in seen:
                            seen[s["symbol"]] = s
                selected = list(seen.values())
                logger.info(
                    f"  [{strategy_name}] no strategy-specific candidates; "
                    f"using union ({len(selected)} stocks)"
                )

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
        """Run one strategy on its strategy-specific candidate pool. Returns per-stock results."""
        selected_multi = self.selector.select_stocks_multi()
        selected = selected_multi.get(strategy_name, [])
        if not selected:
            # Fall back to union
            seen: Dict[str, dict] = {}
            for stocks in selected_multi.values():
                for s in stocks:
                    if s["symbol"] not in seen:
                        seen[s["symbol"]] = s
            selected = list(seen.values())
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
        broker: Optional[Broker] = None,
    ):
        """
        Set up paper trading portfolios (one per strategy).

        Loads previously saved JSON state from disk immediately (fast, no network
        calls) so the dashboard can render the last-known portfolio without waiting
        for yfinance.  Stock selection is left empty here and populated lazily on
        the first call to paper_trading_tick() so the UI is never blocked.

        Args:
            strategy_names: Strategies to initialise (default: all).
            mode:           "sim" or "live".  Controls which trade_data/ sub-dir
                            is used for persistence.
            broker:         Execution broker to use.  Pass a LiveBroker when
                            mode="live"; defaults to SimBroker when omitted.
        """
        self._trade_mode = mode
        # Wire the execution broker. If none supplied, default to SimBroker so
        # callers that don't care about live trading never need to change.
        self._broker = broker if broker is not None else SimBroker()
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

        # NOTE: refresh_selection() intentionally NOT called here.
        # Stock selection is expensive (300 yfinance calls) and would block the
        # dashboard render.  It is populated lazily on the first tick in a
        # background thread so the UI shows data immediately.
        logger.info(
            f"Paper trading [{mode}] initialized for strategies: {names} "
            "(stock selection deferred to first tick)"
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
        self._paper_portfolios     = {}
        self._paper_selected_multi = {}
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
        # Update order log via broker (works identically for sim and live)
        self._broker.execute_cancel(symbol, mode=self._trade_mode)
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
        # Promote any PENDING orders queued during market closure.
        # This is a no-op when the market is closed or there are no pending orders.
        if _is_market_open():
            bot_orders.promote_pending_orders(self._trade_mode)

        if strategy_name not in self._paper_portfolios:
            self.initialize_paper_trading([strategy_name])

        portfolio = self._paper_portfolios[strategy_name]
        StrategyClass = STRATEGY_MAP[strategy_name]
        strategy = StrategyClass()

        # Use the stock selection locked at startup — never re-scan mid-session.
        # _paper_selected_multi is keyed by strategy name; populated lazily on
        # the first tick so the dashboard can render without waiting for yfinance.
        if not self._paper_selected_multi:
            self._paper_selected_multi = self.selector.select_stocks_multi()

        # Use strategy-specific candidates; fall back to union if none found.
        selected = self._paper_selected_multi.get(strategy_name)
        if not selected:
            seen: Dict[str, dict] = {}
            for stocks in self._paper_selected_multi.values():
                for s in stocks:
                    if s["symbol"] not in seen:
                        seen[s["symbol"]] = s
            selected = list(seen.values())
            if selected:
                logger.info(
                    f"[{strategy_name}] no strategy-specific candidates; "
                    f"falling back to union ({len(selected)} stocks)"
                )

        tick_results = {}
        mode = self._trade_mode

        # IST time — used for gap-down timed-exit logic
        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        # True during the opening 15-min bar (9:15–9:30 AM)
        is_opening_window = now_ist.hour == 9 and 15 <= now_ist.minute < 30
        # True once the first 15-min bar has closed (≥ 9:30 AM)
        is_post_first_bar = (now_ist.hour == 9 and now_ist.minute >= 30) or now_ist.hour > 9

        symbols = [s["symbol"] for s in selected]

        # ── Batch-fetch historical data for ALL selected stocks ────────
        # One yf.download() call replaces N individual Ticker.history() calls.
        historical_batch = self.fetcher.get_multiple_historical_batch(
            symbols, period_days=60
        )

        # ── Batch-fetch current LTP for ALL selected stocks ───────────
        # get_current_price_batch() uses fast_info.last_price (real LTP) and
        # returns None for any symbol whose price cannot be verified as today's
        # data.  Never falls back to a stale daily-close bar.
        live_prices = self.fetcher.get_current_price_batch(symbols)

        # ── Intraday (15-min) data — only for open positions ──────────
        # Limits the expensive intraday calls to at most MAX_OPEN_POSITIONS.
        open_symbols = list(portfolio.positions.keys())
        intraday_cache: Dict[str, pd.DataFrame] = {}
        for _sym in open_symbols:
            intraday_cache[_sym] = self.fetcher.get_intraday(_sym, interval=EMA_TIMEFRAME)

        for stock_info in selected:
            symbol = stock_info["symbol"]
            df = historical_batch.get(symbol)
            if df is None or df.empty:
                continue

            current_price = live_prices.get(symbol)
            if current_price is None:
                # Price could not be verified as today's LTP — skip all trading
                # decisions for this symbol to avoid acting on stale data.
                tick_results[symbol] = {
                    "signal":      "PRICE_ERROR",
                    "price":       None,
                    "in_position": symbol in portfolio.positions,
                    "price_error": True,
                }
                continue

            signal = strategy.get_current_signal(df)

            # ── 15-min intraday data (already fetched above for open positions) ──
            intraday = intraday_cache.get(symbol, pd.DataFrame())

            # Compute 15-min 20-EMA and check if last close is below it
            ema_exit = False
            if intraday is not None and len(intraday) >= EMA_PERIOD:
                intraday = intraday.copy()
                intraday["EMA20"] = intraday["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()
                last_close_15m = float(intraday["Close"].iloc[-1])
                last_ema_15m   = float(intraday["EMA20"].iloc[-1])
                ema_exit = last_close_15m < last_ema_15m

            # Process exits — log each close to bot_orders
            if symbol in portfolio.positions:
                pos = portfolio.positions[symbol]
                reason = None

                # ── Gap-Down Timed Exit Logic ──────────────────────────────
                if current_price < pos.stop_loss and pos.gap_state == "none" and is_opening_window:
                    # Gap detected at open — start watching; do NOT exit yet
                    pos.gap_state = "watching"
                    pos.gap_down_open = current_price
                    tick_results[symbol] = {
                        "signal": "GAP_WATCH", "price": current_price, "in_position": True
                    }
                    continue

                elif pos.gap_state == "watching" and is_post_first_bar:
                    # First 15-min candle has closed — decide now
                    if intraday is not None and not intraday.empty:
                        first_candle = intraday.iloc[0]
                        candle_green    = float(first_candle["Close"]) > float(first_candle["Open"])
                        avg_vol         = float(intraday["Volume"].mean()) if len(intraday) > 1 else float(first_candle["Volume"])
                        is_high_volume  = avg_vol > 0 and float(first_candle["Volume"]) > avg_vol * 1.5

                        if candle_green and current_price > float(first_candle["Low"]):
                            # Recovery: hold and tighten SL to opening candle low
                            pos.stop_loss = max(
                                float(first_candle["Low"]),
                                pos.entry_price * (1 - STOP_LOSS_PCT * 2),
                            )
                            pos.gap_state = "recovered"
                        else:
                            # Red candle or high-volume flush: exit immediately
                            reason = "Gap Down Exit"
                    else:
                        reason = "Stop Loss"  # No intraday data — fall back

                elif pos.gap_state not in ("watching", "recovered"):
                    # ── Dynamic Trailing Stop ─────────────────────────────
                    _be_before = pos.breakeven_set
                    trailing_triggered = pos.update_trailing_stop(current_price)

                    # Notify when break-even is first set
                    if not _be_before and pos.breakeven_set and _tg:
                        try:
                            _tg.notify_breakeven(symbol, pos.entry_price)
                        except Exception:
                            pass

                    if trailing_triggered:
                        reason = "Trailing Stop"
                    elif pos.should_stop_loss(current_price):
                        reason = "Stop Loss"
                    elif ema_exit:
                        reason = "EMA Exit"
                    elif signal == "SELL":
                        reason = "Strategy Signal"
                else:
                    # gap_state == "watching" (still in opening window) or "recovered" — run normal checks
                    _be_before = pos.breakeven_set
                    trailing_triggered = pos.update_trailing_stop(current_price)
                    if not _be_before and pos.breakeven_set and _tg:
                        try:
                            _tg.notify_breakeven(symbol, pos.entry_price)
                        except Exception:
                            pass
                    if trailing_triggered:
                        reason = "Trailing Stop"
                    elif ema_exit:
                        reason = "EMA Exit"
                    elif signal == "SELL":
                        reason = "Strategy Signal"

                if reason:
                    trade = portfolio.close_position(symbol, current_price, reason)
                    if trade:
                        # Unified execution: identical path for sim and live.
                        # SimBroker logs to JSON; LiveBroker calls Kite then logs.
                        try:
                            self._broker.execute_sell(
                                symbol, trade.quantity, current_price,
                                reason, trade.pnl, mode,
                            )
                        except Exception as exc:
                            logger.error(f"execute_sell failed [{symbol}]: {exc}")
                        # Push notification for exits
                        if _tg:
                            try:
                                if reason == "Trailing Stop":
                                    _tg.notify_tsl_triggered(symbol, current_price, trade.pnl)
                                else:
                                    bare = symbol.replace(".NS", "")
                                    pnl_str = f"+₹{trade.pnl:,.0f}" if trade.pnl >= 0 else f"-₹{abs(trade.pnl):,.0f}"
                                    emoji = "✅" if trade.pnl >= 0 else "❌"
                                    _tg.send_notification(
                                        f"{emoji} *{bare}* closed\n"
                                        f"Exit: ₹{current_price:,.2f}  P&L {pnl_str}\n"
                                        f"Reason: {reason}"
                                    )
                            except Exception:
                                pass

            # Process entries — unified execute_buy handles sim and live identically
            elif signal == "BUY":
                new_pos = portfolio.open_position(symbol, current_price, strategy_name)
                if new_pos:
                    try:
                        self._broker.execute_buy(
                            symbol, new_pos.quantity, current_price,
                            strategy_name, mode,
                        )
                    except Exception as exc:
                        logger.error(f"execute_buy failed [{symbol}]: {exc}")
                    # Push notification for entry
                    if _tg:
                        try:
                            bare = symbol.replace(".NS", "")
                            _tg.send_notification(
                                f"🟢 *BUY {bare}*\n"
                                f"Entry: ₹{current_price:,.2f}  ×{new_pos.quantity}\n"
                                f"SL: ₹{new_pos.stop_loss:,.2f}  [{strategy_name.upper()}]"
                            )
                        except Exception:
                            pass

            tick_results[symbol] = {
                "signal":      signal,
                "price":       current_price,
                "in_position": symbol in portfolio.positions,
            }

        # Exclude price-error symbols — None values would break equity calculations
        # and must not be written into the prices.json disk cache.
        current_prices = {
            s: tick_results[s]["price"]
            for s in tick_results
            if tick_results[s].get("price") is not None
        }
        portfolio._record_equity(current_prices)

        # Persist portfolio state to disk so it survives app restarts
        try:
            trade_store.save_portfolio(strategy_name, portfolio.to_dict(), mode=mode)
        except Exception as exc:
            logger.warning(f"Failed to persist portfolio [{strategy_name}]: {exc}")

        # Persist live prices so the dashboard can show them before the next
        # tick completes (avoids falling back to entry prices on restart).
        try:
            trade_store.save_last_prices(current_prices, mode=mode)
        except Exception as exc:
            logger.warning(f"Failed to persist prices [{strategy_name}]: {exc}")

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
                stop_loss     = float(avg_price) * (1 - STOP_LOSS_PCT),
                target        = None,   # Dynamic TSL handles exits
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
            # Use the broker so the log entry mirrors however live orders are stored
            try:
                self._broker.execute_buy(
                    symbol_ns, int(quantity), float(avg_price),
                    strategy_name, "live",
                )
            except Exception:
                # Fallback: at minimum log via bot_orders directly
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
