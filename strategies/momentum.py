"""
Momentum Strategy
=================
Buys stocks showing strong price + volume momentum.
Sells when momentum weakens or reverses.

Indicators used:
- Rate of Change (ROC) — price momentum
- Relative Strength Index (RSI) — not overbought
- Volume Rate of Change — volume momentum
- Average True Range (ATR) — for volatility-adjusted stops
"""

import pandas as pd
import numpy as np

from .base import BaseStrategy, Signal
from config import MOMENTUM_LOOKBACK


class MomentumStrategy(BaseStrategy):

    name = "Momentum"
    description = (
        "Buys stocks with strong price + volume momentum. "
        "Sells when momentum declines or RSI enters overbought."
    )

    def __init__(self, lookback: int = MOMENTUM_LOOKBACK):
        self.lookback = lookback

    def _compute_roc(self, series: pd.Series, period: int) -> pd.Series:
        """Rate of Change = (current - n periods ago) / n periods ago * 100"""
        return (series / series.shift(period) - 1) * 100

    def _compute_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low   = df["High"] - df["Low"]
        high_close = (df["High"] - df["Close"].shift()).abs()
        low_close  = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # Price momentum
        df["ROC"]       = self._compute_roc(df["Close"], self.lookback)
        df["ROC_5"]     = self._compute_roc(df["Close"], 5)    # Short-term

        # Volume momentum
        df["Vol_ROC"]   = self._compute_roc(df["Volume"], self.lookback)
        df["Vol_MA"]    = df["Volume"].rolling(20).mean()

        # RSI (momentum filter)
        df["RSI"]       = self._compute_rsi(df["Close"])

        # ATR for stop calculation
        df["ATR"]       = self._compute_atr(df)

        # Trend: 50-day SMA
        df["SMA_50"]    = df["Close"].rolling(50).mean()

        # Momentum score: combo of price ROC + volume ROC
        df["Mom_Score"] = (df["ROC"] * 0.6 + df["Vol_ROC"].clip(-100, 100) * 0.4)

        # Rolling z-score of momentum (normalise across time)
        roll_mean = df["Mom_Score"].rolling(60).mean()
        roll_std  = df["Mom_Score"].rolling(60).std()
        df["Mom_ZScore"] = (df["Mom_Score"] - roll_mean) / roll_std.replace(0, np.nan)

        df["Signal"] = "HOLD"
        df["Signal_Strength"] = 0.0

        for i in range(1, len(df)):
            curr = df.iloc[i]
            prev = df.iloc[i - 1]

            rsi      = curr["RSI"]
            zscore   = curr["Mom_ZScore"]
            roc      = curr["ROC"]
            roc_5    = curr["ROC_5"]
            vol_roc  = curr["Vol_ROC"]

            # ── BUY: Strong momentum, not overbought, rising volume ──
            if (
                zscore > 0.5           # Momentum above average
                and roc > 2.0          # Price up >2% in lookback period
                and roc_5 > 0          # Still rising short-term
                and rsi < 70           # Not extremely overbought
                and vol_roc > -10      # Volume not collapsing
                and curr["Close"] > curr["SMA_50"]  # Uptrend
            ):
                strength = min(1.0, zscore / 2.0)
                df.iloc[i, df.columns.get_loc("Signal")] = "BUY"
                df.iloc[i, df.columns.get_loc("Signal_Strength")] = round(strength, 2)

            # ── SELL: Momentum weakening or overbought ──
            elif (
                (zscore < -0.3 and prev.get("Signal") != "SELL") or
                rsi > 75 or
                (roc < 0 and roc_5 < -1.0)
            ):
                df.iloc[i, df.columns.get_loc("Signal")] = "SELL"
                df.iloc[i, df.columns.get_loc("Signal_Strength")] = 0.8

        return df

    def get_current_signal(self, df: pd.DataFrame) -> Signal:
        if not self.validate_df(df, min_rows=self.lookback + 10):
            return "HOLD"
        result = self.generate_signals(df)
        return result["Signal"].iloc[-1]

    def get_indicators(self, df: pd.DataFrame) -> dict:
        result = self.generate_signals(df)
        last = result.iloc[-1]
        return {
            "ROC (20d)":   f"{last.get('ROC', 0):.2f}%",
            "RSI":         round(last.get("RSI", 0), 2),
            "Mom Z-Score": round(last.get("Mom_ZScore", 0), 2),
            "ATR":         round(last.get("ATR", 0), 2),
            "Signal":      last["Signal"],
        }
