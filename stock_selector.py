"""
Stock Selector
==============
Multi-Strategy Parallel Scanner for the 300-stock NSE universe
(100 large cap + 100 mid cap + 100 small cap).

Four independent scan strategies share a single batch fetch:

  Strategy 1 — MA  (Moving Average Alignment)
       – Price > 50-day SMA  (near-term uptrend)
       – Price > 200-day SMA (long-term uptrend)
       – Broadest filter — no volume or ADX requirement
       – Ranked by how far price is above SMA200

  Strategy 2 — RSI_MACD  (Momentum Confirmation)
       – RSI(14) < RSI_OVERBOUGHT (not extended / overbought)
       – MACD histogram > 0 (bullish momentum active)
       – Ranked by MACD histogram value

  Strategy 3 — Momentum  (Price + Volume ROC)
       – Composite momentum score > 0
       – Weighted: 40% price ROC + 20% short ROC + 20% vol trend + 20% consistency
       – Ranked by score

  Strategy 4 — TrendStrength  (RS + Volume + ADX)   ← NEW
       – Price > SMA50 AND SMA200 (Relative Strength)
       – Volume >= RS_VOLUME_MULTIPLIER × 20-day average
       – ADX(14) > RS_ADX_MIN (strong confirmed directional trend)
       – Volatility cap: annualised vol <= max_volatility
       – Most selective — granular per-filter logging to diagnose low-yield scenarios
       – Ranked by ADX

Each strategy applies the same tier-guarantee system (TOP_N_PER_CAP from
each cap tier, up to TOP_N_STOCKS total).

Summary log after each full scan:
  Strategy [MA]:             23 selected  (LA: 8 | MI: 9 | SM: 6)
  Strategy [RSI_MACD]:       12 selected  (LA: 4 | MI: 5 | SM: 3)
  Strategy [MOMENTUM]:       31 selected  (LA: 11 | MI: 11 | SM: 9)
  Strategy [TREND_STRENGTH]:  0 selected
  [TrendStrength] Filter breakdown — RS failed: 45 | Volume failed: 89 | ADX<25: 120 | ...
"""

import logging
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, MACD as MACDIndicator

from config import (
    LARGE_CAP_SYMBOLS, MID_CAP_SYMBOLS, SMALL_CAP_SYMBOLS,
    TOP_N_STOCKS, TOP_N_PER_CAP,
    MOMENTUM_LOOKBACK, MOMENTUM_VOLUME_LOOKBACK,
    RS_SMA_SHORT, RS_SMA_LONG,
    RS_VOLUME_LOOKBACK, RS_VOLUME_MULTIPLIER,
    RS_ADX_PERIOD, RS_ADX_MIN,
    RSI_PERIOD, RSI_OVERBOUGHT,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
)
from data_fetcher import DataFetcher

logger = logging.getLogger(__name__)

# Map symbol → cap category (built once at import time)
_CAP_MAP: Dict[str, str] = {}
for _s in LARGE_CAP_SYMBOLS:
    _CAP_MAP[_s] = "Large Cap"
for _s in MID_CAP_SYMBOLS:
    _CAP_MAP[_s] = "Mid Cap"
for _s in SMALL_CAP_SYMBOLS:
    _CAP_MAP[_s] = "Small Cap"

ALL_SYMBOLS = LARGE_CAP_SYMBOLS + MID_CAP_SYMBOLS + SMALL_CAP_SYMBOLS

# Minimum data rows required by each scan strategy
_MIN_ROWS_MA             = RS_SMA_LONG + 10                     # ≈ 210
_MIN_ROWS_RSI_MACD       = MACD_SLOW + MACD_SIGNAL + 15         # ≈ 50
_MIN_ROWS_MOMENTUM       = MOMENTUM_LOOKBACK + 10               # ≈ 30
_MIN_ROWS_TREND_STRENGTH = RS_SMA_LONG + 20                     # ≈ 220

# Canonical strategy name list — must match STRATEGY_MAP keys in strategies/
SCAN_STRATEGY_NAMES = ["ma", "rsi_macd", "momentum", "trend_strength"]


class StockSelector:
    """Selects top trending stocks from the 300-stock NSE universe."""

    def __init__(self, data_fetcher: DataFetcher):
        self.fetcher = data_fetcher

    # ──────────────────────────────────────────────────────────────────
    # Metric helpers
    # ──────────────────────────────────────────────────────────────────

    def compute_momentum_score(self, df: pd.DataFrame) -> float:
        """
        Composite momentum score (used for within-tier ranking only).

        Components:
          1. Price ROC over MOMENTUM_LOOKBACK                  (40 %)
          2. Short-term ROC (5-day)                            (20 %)
          3. Volume trend (recent avg vs older avg)            (20 %)
          4. Consistency (% of positive return days)           (20 %)
        """
        if len(df) < MOMENTUM_LOOKBACK + 5:
            return -999.0

        close  = df["Close"]
        volume = df["Volume"]

        roc_long  = (close.iloc[-1] / close.iloc[-MOMENTUM_LOOKBACK] - 1) * 100
        roc_short = (close.iloc[-1] / close.iloc[-5] - 1) * 100

        vol_recent = volume.iloc[-MOMENTUM_VOLUME_LOOKBACK:].mean()
        vol_older  = volume.iloc[-MOMENTUM_LOOKBACK:-MOMENTUM_VOLUME_LOOKBACK].mean()
        vol_trend  = ((vol_recent / vol_older) - 1) * 100 if vol_older > 0 else 0

        daily_returns  = close.pct_change().dropna()
        recent_returns = daily_returns.iloc[-MOMENTUM_LOOKBACK:]
        consistency    = (recent_returns > 0).sum() / len(recent_returns) * 100

        score = (
            0.40 * roc_long +
            0.20 * roc_short +
            0.20 * vol_trend +
            0.20 * consistency
        )
        return round(score, 4)

    def compute_volatility(self, df: pd.DataFrame, period: int = 20) -> float:
        """Annualised volatility (std of returns × √252 × 100)."""
        if len(df) < period:
            return 999.0
        returns = df["Close"].pct_change().dropna().tail(period)
        return float(returns.std() * np.sqrt(252) * 100)

    def compute_adx(self, df: pd.DataFrame) -> float:
        """
        Latest ADX value using Wilder's 14-period smoothing.
        ADX > 25 → trending market. Returns 0.0 on insufficient data.
        """
        min_rows = RS_ADX_PERIOD * 3
        if len(df) < min_rows:
            return 0.0
        try:
            adx_ind = ADXIndicator(
                high=df["High"], low=df["Low"], close=df["Close"],
                window=RS_ADX_PERIOD, fillna=False,
            )
            val = adx_ind.adx().dropna()
            return float(val.iloc[-1]) if not val.empty else 0.0
        except Exception as exc:
            logger.debug(f"ADX computation failed: {exc}")
            return 0.0

    def _compute_rsi(self, df: pd.DataFrame) -> float:
        """Latest RSI(RSI_PERIOD). Returns 50.0 (neutral) on insufficient data."""
        if len(df) < RSI_PERIOD + 5:
            return 50.0
        try:
            rsi = RSIIndicator(close=df["Close"], window=RSI_PERIOD, fillna=False).rsi()
            val = rsi.dropna()
            return float(val.iloc[-1]) if not val.empty else 50.0
        except Exception:
            return 50.0

    def _compute_macd_histogram(self, df: pd.DataFrame) -> float:
        """Latest MACD histogram (MACD line − signal line). Returns 0.0 on insufficient data."""
        if len(df) < MACD_SLOW + MACD_SIGNAL + 5:
            return 0.0
        try:
            macd = MACDIndicator(
                close=df["Close"],
                window_slow=MACD_SLOW,
                window_fast=MACD_FAST,
                window_sign=MACD_SIGNAL,
                fillna=False,
            )
            hist = macd.macd_diff().dropna()
            return float(hist.iloc[-1]) if not hist.empty else 0.0
        except Exception:
            return 0.0

    # ──────────────────────────────────────────────────────────────────
    # Filter helpers
    # ──────────────────────────────────────────────────────────────────

    def _passes_ma_filter(self, df: pd.DataFrame) -> bool:
        """True if the latest close is above both SMA50 and SMA200."""
        if len(df) < RS_SMA_LONG:
            return False
        close   = df["Close"]
        sma50   = close.iloc[-RS_SMA_SHORT:].mean()
        sma200  = close.iloc[-RS_SMA_LONG:].mean()
        current = float(close.iloc[-1])
        return current > sma50 and current > sma200

    def _passes_volume_filter(self, df: pd.DataFrame) -> bool:
        """True if today's volume >= RS_VOLUME_MULTIPLIER × 20-day average volume."""
        if len(df) < RS_VOLUME_LOOKBACK + 1:
            return False
        current_vol = float(df["Volume"].iloc[-1])
        avg_vol_20  = float(df["Volume"].iloc[-(RS_VOLUME_LOOKBACK + 1):-1].mean())
        if avg_vol_20 <= 0:
            return False
        return current_vol >= RS_VOLUME_MULTIPLIER * avg_vol_20

    def _sma_spread(self, df: pd.DataFrame) -> float:
        """(price / SMA200 − 1) × 100 — how far above the long-term trend the stock is."""
        if len(df) < RS_SMA_LONG:
            return 0.0
        price  = float(df["Close"].iloc[-1])
        sma200 = float(df["Close"].iloc[-RS_SMA_LONG:].mean())
        return ((price / sma200) - 1) * 100 if sma200 > 0 else 0.0

    # ──────────────────────────────────────────────────────────────────
    # Tier guarantee helper
    # ──────────────────────────────────────────────────────────────────

    def _apply_tier_guarantee(
        self,
        by_cap: Dict[str, List[Dict]],
        sort_key: str,
        top_n: int = TOP_N_STOCKS,
        top_n_per_cap: int = TOP_N_PER_CAP,
    ) -> List[Dict]:
        """
        Guarantee at least top_n_per_cap from each tier, then fill remaining
        slots (up to top_n) with the globally highest sort_key stocks.
        """
        for cap in by_cap:
            by_cap[cap].sort(key=lambda x: x.get(sort_key, 0), reverse=True)

        guaranteed: List[Dict] = []
        for cap in by_cap:
            guaranteed.extend(by_cap[cap][:top_n_per_cap])

        guaranteed_syms = {s["symbol"] for s in guaranteed}
        remaining_pool  = sorted(
            [s for cap_list in by_cap.values() for s in cap_list
             if s["symbol"] not in guaranteed_syms],
            key=lambda x: x.get(sort_key, 0), reverse=True,
        )

        selected = guaranteed + remaining_pool[: max(0, top_n - len(guaranteed))]
        selected.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
        return selected[:top_n]

    # ──────────────────────────────────────────────────────────────────
    # Shared stock dict builder
    # ──────────────────────────────────────────────────────────────────

    def _build_stock_dict(
        self,
        symbol: str,
        df: pd.DataFrame,
        strategy_tag: str,
        **extra_fields,
    ) -> Dict:
        """Build the standard stock dict shared across all scan strategies."""
        close  = df["Close"]
        volume = df["Volume"]

        current_price = float(close.iloc[-1])
        sma50  = float(close.iloc[-RS_SMA_SHORT:].mean()) if len(df) >= RS_SMA_SHORT else current_price
        sma200 = float(close.iloc[-RS_SMA_LONG:].mean())  if len(df) >= RS_SMA_LONG  else current_price
        avg_vol_20  = float(volume.iloc[-(RS_VOLUME_LOOKBACK + 1):-1].mean()) if len(df) > RS_VOLUME_LOOKBACK + 1 else 0.0
        current_vol = float(volume.iloc[-1])
        vol_ratio   = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0.0
        momentum_pct = (
            round(float((close.iloc[-1] / close.iloc[-MOMENTUM_LOOKBACK] - 1) * 100), 2)
            if len(df) > MOMENTUM_LOOKBACK else 0.0
        )

        base = {
            "symbol":         symbol,
            "cap_category":   _CAP_MAP.get(symbol, "Large Cap"),
            "strategy_tag":   strategy_tag,
            "adx":            0.0,
            "score":          -999.0,
            "momentum_pct":   momentum_pct,
            "volatility_pct": round(self.compute_volatility(df), 2),
            "current_price":  round(current_price, 2),
            "sma50":          round(sma50, 2),
            "sma200":         round(sma200, 2),
            "vol_ratio":      vol_ratio,
            "data":           df,
        }
        base.update(extra_fields)
        return base

    # ──────────────────────────────────────────────────────────────────
    # Scan Strategy 1: MA (Moving Average Alignment)
    # ──────────────────────────────────────────────────────────────────

    def _scan_ma(self, all_data: Dict[str, pd.DataFrame]) -> List[Dict]:
        """
        MA scan: price above SMA50 AND SMA200.
        Sort key: sma_spread (% above SMA200 — strength of the long-term trend).
        """
        by_cap: Dict[str, List[Dict]] = {"Large Cap": [], "Mid Cap": [], "Small Cap": []}
        n_failed = 0
        n_filtered = 0

        for symbol in ALL_SYMBOLS:
            try:
                df = all_data.get(symbol)
                if df is None or df.empty or len(df) < _MIN_ROWS_MA:
                    n_failed += 1
                    continue

                if not self._passes_ma_filter(df):
                    n_filtered += 1
                    logger.debug(f"[MA] {symbol}: below SMA50/SMA200")
                    continue

                sma_spread = self._sma_spread(df)
                adx        = self.compute_adx(df)
                score      = self.compute_momentum_score(df)
                stock      = self._build_stock_dict(
                    symbol, df, "ma",
                    adx=round(adx, 2),
                    score=score,
                    sma_spread=round(sma_spread, 4),
                )
                by_cap[stock["cap_category"]].append(stock)
            except Exception as exc:
                logger.warning(f"[MA] Error processing {symbol}: {exc}")
                n_failed += 1

        n_passed = sum(len(v) for v in by_cap.values())
        logger.debug(
            f"[MA] scan: {n_passed} passed | below MA: {n_filtered} | data fail: {n_failed}"
        )
        return self._apply_tier_guarantee(by_cap, sort_key="sma_spread")

    # ──────────────────────────────────────────────────────────────────
    # Scan Strategy 2: RSI_MACD (Momentum Confirmation)
    # ──────────────────────────────────────────────────────────────────

    def _scan_rsi_macd(self, all_data: Dict[str, pd.DataFrame]) -> List[Dict]:
        """
        RSI+MACD scan: RSI < RSI_OVERBOUGHT (not extended) AND MACD histogram > 0.
        Sort key: macd_hist (bullish momentum strength).
        """
        by_cap: Dict[str, List[Dict]] = {"Large Cap": [], "Mid Cap": [], "Small Cap": []}
        n_failed = 0
        n_rsi    = 0
        n_macd   = 0

        for symbol in ALL_SYMBOLS:
            try:
                df = all_data.get(symbol)
                if df is None or df.empty or len(df) < _MIN_ROWS_RSI_MACD:
                    n_failed += 1
                    continue

                rsi = self._compute_rsi(df)
                if rsi >= RSI_OVERBOUGHT:
                    n_rsi += 1
                    logger.debug(f"[RSI_MACD] {symbol}: RSI {rsi:.1f} >= {RSI_OVERBOUGHT} (overbought)")
                    continue

                macd_hist = self._compute_macd_histogram(df)
                if macd_hist <= 0:
                    n_macd += 1
                    logger.debug(f"[RSI_MACD] {symbol}: MACD hist {macd_hist:.4f} <= 0")
                    continue

                adx   = self.compute_adx(df)
                score = self.compute_momentum_score(df)
                stock = self._build_stock_dict(
                    symbol, df, "rsi_macd",
                    adx=round(adx, 2),
                    score=score,
                    rsi=round(rsi, 2),
                    macd_hist=round(macd_hist, 6),
                )
                by_cap[stock["cap_category"]].append(stock)
            except Exception as exc:
                logger.warning(f"[RSI_MACD] Error processing {symbol}: {exc}")
                n_failed += 1

        n_passed = sum(len(v) for v in by_cap.values())
        logger.debug(
            f"[RSI_MACD] scan: {n_passed} passed | "
            f"RSI overbought: {n_rsi} | MACD <= 0: {n_macd} | data fail: {n_failed}"
        )
        return self._apply_tier_guarantee(by_cap, sort_key="macd_hist")

    # ──────────────────────────────────────────────────────────────────
    # Scan Strategy 3: Momentum (Price + Volume ROC)
    # ──────────────────────────────────────────────────────────────────

    def _scan_momentum(self, all_data: Dict[str, pd.DataFrame]) -> List[Dict]:
        """
        Momentum scan: composite score > 0.
        Sort key: score.
        """
        by_cap: Dict[str, List[Dict]] = {"Large Cap": [], "Mid Cap": [], "Small Cap": []}
        n_failed   = 0
        n_filtered = 0

        for symbol in ALL_SYMBOLS:
            try:
                df = all_data.get(symbol)
                if df is None or df.empty or len(df) < _MIN_ROWS_MOMENTUM:
                    n_failed += 1
                    continue

                score = self.compute_momentum_score(df)
                if score <= 0:
                    n_filtered += 1
                    logger.debug(f"[Momentum] {symbol}: score {score:.2f} <= 0")
                    continue

                adx   = self.compute_adx(df)
                stock = self._build_stock_dict(
                    symbol, df, "momentum",
                    adx=round(adx, 2),
                    score=score,
                )
                by_cap[stock["cap_category"]].append(stock)
            except Exception as exc:
                logger.warning(f"[Momentum] Error processing {symbol}: {exc}")
                n_failed += 1

        n_passed = sum(len(v) for v in by_cap.values())
        logger.debug(
            f"[Momentum] scan: {n_passed} passed | score <= 0: {n_filtered} | data fail: {n_failed}"
        )
        return self._apply_tier_guarantee(by_cap, sort_key="score")

    # ──────────────────────────────────────────────────────────────────
    # Scan Strategy 4: TrendStrength (RS + Volume + ADX)
    # ──────────────────────────────────────────────────────────────────

    def _scan_trend_strength(
        self,
        all_data: Dict[str, pd.DataFrame],
        max_volatility: float = 70.0,
    ) -> List[Dict]:
        """
        TrendStrength scan: RS + Volume surge + ADX > RS_ADX_MIN + volatility cap.

        Most selective strategy. Provides granular per-filter logging so you
        can diagnose exactly which filter is eliminating stocks (e.g. volume
        surge is rarely met on quiet market days).

        Sort key: adx.
        """
        by_cap: Dict[str, List[Dict]] = {"Large Cap": [], "Mid Cap": [], "Small Cap": []}
        n_failed     = 0
        n_rs         = 0    # failed RS (MA alignment) filter
        n_volume     = 0    # failed volume surge filter
        n_adx        = 0    # failed ADX threshold
        n_volatility = 0    # failed volatility cap

        for symbol in ALL_SYMBOLS:
            try:
                df = all_data.get(symbol)
                if df is None or df.empty or len(df) < _MIN_ROWS_TREND_STRENGTH:
                    n_failed += 1
                    continue

                # ── Filter 1: RS — price above both SMAs ──────────────────
                if not self._passes_ma_filter(df):
                    n_rs += 1
                    logger.debug(f"[TrendStrength] {symbol}: below SMA50/SMA200 (RS fail)")
                    continue

                # ── Filter 2: Volume surge ────────────────────────────────
                if not self._passes_volume_filter(df):
                    n_volume += 1
                    logger.debug(f"[TrendStrength] {symbol}: volume < {RS_VOLUME_MULTIPLIER}× avg (Vol fail)")
                    continue

                # ── Filter 3: ADX trend quality ───────────────────────────
                adx = self.compute_adx(df)
                if adx < RS_ADX_MIN:
                    n_adx += 1
                    logger.debug(f"[TrendStrength] {symbol}: ADX {adx:.1f} < {RS_ADX_MIN} (ADX fail)")
                    continue

                # ── Filter 4: Volatility cap ──────────────────────────────
                vol_pct = self.compute_volatility(df)
                if vol_pct > max_volatility:
                    n_volatility += 1
                    logger.debug(f"[TrendStrength] {symbol}: vol {vol_pct:.1f}% > {max_volatility}% (Volatility fail)")
                    continue

                score = self.compute_momentum_score(df)
                stock = self._build_stock_dict(
                    symbol, df, "trend_strength",
                    adx=round(adx, 2),
                    score=score,
                )
                by_cap[stock["cap_category"]].append(stock)

            except Exception as exc:
                logger.warning(f"[TrendStrength] Error processing {symbol}: {exc}")
                n_failed += 1

        # Granular breakdown always logged at INFO so it appears in the main log
        n_passed = sum(len(v) for v in by_cap.values())
        logger.info(
            f"[TrendStrength] Filter breakdown — "
            f"RS failed: {n_rs} | Volume failed: {n_volume} | "
            f"ADX<{RS_ADX_MIN}: {n_adx} | Vol>{max_volatility}%: {n_volatility} | "
            f"Data failed: {n_failed} | Passed: {n_passed}"
        )
        return self._apply_tier_guarantee(by_cap, sort_key="adx")

    # ──────────────────────────────────────────────────────────────────
    # Multi-Strategy Selection  (primary interface)
    # ──────────────────────────────────────────────────────────────────

    def select_stocks_multi(
        self,
        period_days: int = 220,
        max_volatility: float = 70.0,
    ) -> Dict[str, List[Dict]]:
        """
        Scan all 300 NSE stocks using 4 independent strategies in parallel.

        Uses a single batch fetch shared across all strategies so the
        total network overhead is identical to the original single-strategy scan.

        Returns:
            Dict keyed by strategy name → ranked list of stock dicts.
            Each dict includes a ``strategy_tag`` field with the strategy name.
            A stock can appear in multiple strategy lists (independent signals).
        """
        logger.info(
            f"Multi-strategy scan: {len(ALL_SYMBOLS)} stocks "
            f"({len(LARGE_CAP_SYMBOLS)} large / {len(MID_CAP_SYMBOLS)} mid / "
            f"{len(SMALL_CAP_SYMBOLS)} small cap) — 4 strategies running..."
        )

        # One batch fetch shared across all 4 strategies
        all_data = self.fetcher.get_multiple_historical_batch(
            ALL_SYMBOLS, period_days=period_days + 10
        )

        results: Dict[str, List[Dict]] = {
            "ma":             self._scan_ma(all_data),
            "rsi_macd":       self._scan_rsi_macd(all_data),
            "momentum":       self._scan_momentum(all_data),
            "trend_strength": self._scan_trend_strength(all_data, max_volatility=max_volatility),
        }

        # ── Strategy summary table ─────────────────────────────────────
        logger.info("─" * 60)
        logger.info("Multi-Strategy Scan Results:")
        for strat, stocks in results.items():
            cap_counts: Dict[str, int] = {}
            for s in stocks:
                cap_counts[s["cap_category"]] = cap_counts.get(s["cap_category"], 0) + 1
            cap_str = " | ".join(
                f"{c[:2].upper()}: {n}" for c, n in cap_counts.items()
            )
            logger.info(
                f"  Strategy [{strat.upper():14s}]: {len(stocks):3d} selected"
                + (f"  ({cap_str})" if cap_str else "")
            )
        logger.info("─" * 60)

        return results

    def refresh_selection_multi(
        self,
    ) -> Tuple[Dict[str, List[Dict]], Dict[str, pd.DataFrame]]:
        """
        Convenience wrapper: select stocks for all strategies.
        Returns (multi_dict, per_strategy_summary_DataFrames).
        """
        multi     = self.select_stocks_multi()
        summaries = {
            name: self.get_selection_summary(stocks)
            for name, stocks in multi.items()
        }
        return multi, summaries

    # ──────────────────────────────────────────────────────────────────
    # Backward-compatible single selection (union of all strategies)
    # ──────────────────────────────────────────────────────────────────

    def select_stocks(
        self,
        period_days: int = 220,
        max_volatility: float = 70.0,
    ) -> List[Dict]:
        """
        Backward-compatible single selection.

        Returns the union of all 4 strategy selections (unique by symbol),
        preferring the highest-ADX entry when a stock appears in multiple
        strategy lists. Used by the screener and backtester.
        """
        multi = self.select_stocks_multi(period_days, max_volatility)

        seen: Dict[str, Dict] = {}
        for strategy_stocks in multi.values():
            for stock in strategy_stocks:
                sym = stock["symbol"]
                if sym not in seen or stock.get("adx", 0) > seen[sym].get("adx", 0):
                    seen[sym] = stock

        selected = sorted(seen.values(), key=lambda x: x.get("adx", 0), reverse=True)

        cap_counts: Dict[str, int] = {}
        for s in selected:
            cap_counts[s["cap_category"]] = cap_counts.get(s["cap_category"], 0) + 1
        logger.info(
            f"Union selection: {len(selected)} unique stocks — "
            + ", ".join(f"{c}: {n}" for c, n in cap_counts.items())
        )
        for s in selected:
            logger.info(
                f"  [{s['cap_category'][:2].upper()}] {s['symbol']:20s} "
                f"adx={s.get('adx', 0):5.1f}  score={s.get('score', 0):7.2f}  "
                f"mom={s.get('momentum_pct', 0):+6.2f}%  "
                f"vol_ratio={s.get('vol_ratio', 0):.2f}×  "
                f"price=₹{s.get('current_price', 0):,.2f}  "
                f"tag={s.get('strategy_tag', '?')}"
            )
        return selected

    def get_selection_summary(self, selected: List[Dict]) -> pd.DataFrame:
        """Return a clean DataFrame summary of selected stocks (no raw data column)."""
        rows = []
        for s in selected:
            rows.append({
                "Symbol":         s["symbol"].replace(".NS", ""),
                "Cap":            s.get("cap_category", "—"),
                "Strategy":       s.get("strategy_tag", "—"),
                "ADX":            s.get("adx", "—"),
                "Score":          s.get("score", "—"),
                "Momentum (%)":   s.get("momentum_pct", "—"),
                "Volatility (%)": s.get("volatility_pct", "—"),
                "Vol Ratio (×)":  s.get("vol_ratio", "—"),
                "SMA50 (₹)":      s.get("sma50", "—"),
                "SMA200 (₹)":     s.get("sma200", "—"),
                "Price (₹)":      s.get("current_price", "—"),
            })
        return pd.DataFrame(rows)

    def refresh_selection(self) -> Tuple[List[Dict], pd.DataFrame]:
        """Convenience method: select stocks (union) and return both list and summary."""
        selected = self.select_stocks()
        summary  = self.get_selection_summary(selected)
        return selected, summary
