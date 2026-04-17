"""
Stock Selector
==============
Selects the top-momentum stocks from a 300-stock NSE universe
(100 large cap + 100 mid cap + 100 small cap).

Selection guarantees at least TOP_N_PER_CAP picks from each tier,
then fills remaining slots with the overall highest scorers.

Momentum is calculated using:
  - Price momentum (rate of change over lookback period)
  - Volume trend (rising volume = conviction)
  - Consistency (% of positive days)
"""

import logging
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from config import (
    LARGE_CAP_SYMBOLS, MID_CAP_SYMBOLS, SMALL_CAP_SYMBOLS,
    TOP_N_STOCKS, TOP_N_PER_CAP,
    MOMENTUM_LOOKBACK, MOMENTUM_VOLUME_LOOKBACK,
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


class StockSelector:
    """Selects top momentum stocks from the 300-stock NSE universe."""

    def __init__(self, data_fetcher: DataFetcher):
        self.fetcher = data_fetcher

    def compute_momentum_score(self, df: pd.DataFrame) -> float:
        """
        Compute a composite momentum score for a stock.

        Components:
          1. Price ROC (Rate of Change) over lookback period      (40%)
          2. Short-term ROC (5-day)                               (20%)
          3. Volume trend (is volume rising?)                     (20%)
          4. Consistency (% of days with positive returns)        (20%)

        Returns:
            float: Composite momentum score (higher = stronger momentum)
        """
        if len(df) < MOMENTUM_LOOKBACK + 5:
            return -999.0

        close = df["Close"]
        volume = df["Volume"]

        # 1. Long-term price ROC
        roc_long = (close.iloc[-1] / close.iloc[-MOMENTUM_LOOKBACK] - 1) * 100

        # 2. Short-term price ROC (5-day)
        roc_short = (close.iloc[-1] / close.iloc[-5] - 1) * 100

        # 3. Volume trend: compare recent avg volume vs older avg volume
        vol_recent = volume.iloc[-MOMENTUM_VOLUME_LOOKBACK:].mean()
        vol_older = volume.iloc[-MOMENTUM_LOOKBACK:-MOMENTUM_VOLUME_LOOKBACK].mean()
        vol_trend = ((vol_recent / vol_older) - 1) * 100 if vol_older > 0 else 0

        # 4. Consistency: % of days with positive daily returns
        daily_returns = close.pct_change().dropna()
        recent_returns = daily_returns.iloc[-MOMENTUM_LOOKBACK:]
        consistency = (recent_returns > 0).sum() / len(recent_returns) * 100

        # Composite score (weighted)
        score = (
            0.40 * roc_long +
            0.20 * roc_short +
            0.20 * vol_trend +
            0.20 * consistency
        )

        return round(score, 4)

    def compute_volatility(self, df: pd.DataFrame, period: int = 20) -> float:
        """Compute annualised volatility (standard deviation of returns)."""
        if len(df) < period:
            return 999.0
        returns = df["Close"].pct_change().dropna().tail(period)
        return float(returns.std() * np.sqrt(252) * 100)

    def select_stocks(
        self,
        period_days: int = 120,
        max_volatility: float = 70.0,
    ) -> List[Dict]:
        """
        Scan all 300 NSE stocks and return the top TOP_N_STOCKS by momentum,
        guaranteeing at least TOP_N_PER_CAP from each cap tier.

        Uses get_multiple_historical_batch() (yf.download multi-threaded) instead
        of a sequential symbol-by-symbol loop — roughly 5-10× faster.

        Args:
            period_days:     Historical data window
            max_volatility:  Exclude stocks with annualised vol above this %

        Returns:
            List of dicts sorted best → worst momentum.
        """
        logger.info(
            f"Scanning {len(ALL_SYMBOLS)} stocks "
            f"({len(LARGE_CAP_SYMBOLS)} large / {len(MID_CAP_SYMBOLS)} mid / "
            f"{len(SMALL_CAP_SYMBOLS)} small cap)..."
        )

        # Single batch call replaces ~300 sequential yf.Ticker().history() calls
        all_data = self.fetcher.get_multiple_historical_batch(
            ALL_SYMBOLS, period_days=period_days + 10
        )

        by_cap: Dict[str, List[Dict]] = {
            "Large Cap": [], "Mid Cap": [], "Small Cap": []
        }
        failed: List[str] = []

        for symbol in ALL_SYMBOLS:
            try:
                df = all_data.get(symbol)
                if df is None or df.empty or len(df) < MOMENTUM_LOOKBACK + 10:
                    failed.append(symbol)
                    continue

                score = self.compute_momentum_score(df)
                vol   = self.compute_volatility(df)

                if vol > max_volatility:
                    logger.debug(f"Skipping {symbol}: vol {vol:.1f}% > {max_volatility}%")
                    continue

                current_price = float(df["Close"].iloc[-1])
                roc = (df["Close"].iloc[-1] / df["Close"].iloc[-MOMENTUM_LOOKBACK] - 1) * 100
                cap = _CAP_MAP.get(symbol, "Large Cap")

                by_cap[cap].append({
                    "symbol":         symbol,
                    "cap_category":   cap,
                    "score":          score,
                    "momentum_pct":   round(float(roc), 2),
                    "volatility_pct": round(vol, 2),
                    "current_price":  round(current_price, 2),
                    "data":           df,
                })

            except Exception as e:
                logger.warning(f"Error processing {symbol}: {e}")
                failed.append(symbol)

        if failed:
            logger.warning(f"Failed to fetch: {len(failed)} symbols")

        # Sort each tier by score
        for cap in by_cap:
            by_cap[cap].sort(key=lambda x: x["score"], reverse=True)

        # Guarantee minimum from each tier, then fill with overall best
        guaranteed: List[Dict] = []
        for cap in by_cap:
            guaranteed.extend(by_cap[cap][:TOP_N_PER_CAP])

        guaranteed_syms = {s["symbol"] for s in guaranteed}
        remaining_pool  = sorted(
            [s for cap in by_cap for s in by_cap[cap] if s["symbol"] not in guaranteed_syms],
            key=lambda x: x["score"], reverse=True,
        )

        selected = guaranteed + remaining_pool[: max(0, TOP_N_STOCKS - len(guaranteed))]
        selected.sort(key=lambda x: x["score"], reverse=True)
        selected = selected[:TOP_N_STOCKS]

        # Summary log
        cap_counts = {}
        for s in selected:
            cap_counts[s["cap_category"]] = cap_counts.get(s["cap_category"], 0) + 1
        logger.info(
            f"Selected {len(selected)} stocks — "
            + ", ".join(f"{c}: {n}" for c, n in cap_counts.items())
        )
        for s in selected:
            logger.info(
                f"  [{s['cap_category'][:2].upper()}] {s['symbol']:20s} "
                f"score={s['score']:7.2f}  mom={s['momentum_pct']:+6.2f}%  "
                f"vol={s['volatility_pct']:5.1f}%  price=₹{s['current_price']:,.2f}"
            )

        return selected

    def get_selection_summary(self, selected: List[Dict]) -> pd.DataFrame:
        """Return a clean DataFrame summary of selected stocks (no raw data column)."""
        rows = []
        for s in selected:
            rows.append({
                "Symbol":         s["symbol"].replace(".NS", ""),
                "Cap":            s.get("cap_category", "—"),
                "Score":          s["score"],
                "Momentum (%)":   s["momentum_pct"],
                "Volatility (%)": s["volatility_pct"],
                "Price (₹)":      s["current_price"],
            })
        return pd.DataFrame(rows)

    def refresh_selection(self) -> Tuple[List[Dict], pd.DataFrame]:
        """Convenience method: select stocks and return both list and summary."""
        selected = self.select_stocks()
        summary = self.get_selection_summary(selected)
        return selected, summary
