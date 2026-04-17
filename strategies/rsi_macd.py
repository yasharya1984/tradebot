"""
RSI + MACD Combined Strategy
==============================
BUY  when RSI is oversold AND MACD line crosses above signal line
SELL when RSI is overbought AND MACD line crosses below signal line

Additional filters:
- Bollinger Band position for context
- Avoid buying in strong downtrend (price below 50-day SMA)
"""

import pandas as pd
import numpy as np

from .base import BaseStrategy, Signal
from config import (
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
)


class RSIMACDStrategy(BaseStrategy):

    name = "RSI + MACD"
    description = (
        "Buys when RSI is oversold and MACD shows bullish crossover. "
        "Sells when RSI is overbought and MACD shows bearish crossover."
    )

    def __init__(
        self,
        rsi_period:    int = RSI_PERIOD,
        rsi_oversold:  float = RSI_OVERSOLD,
        rsi_overbought: float = RSI_OVERBOUGHT,
        macd_fast:     int = MACD_FAST,
        macd_slow:     int = MACD_SLOW,
        macd_signal:   int = MACD_SIGNAL,
    ):
        self.rsi_period     = rsi_period
        self.rsi_oversold   = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_fast      = macd_fast
        self.macd_slow      = macd_slow
        self.macd_signal    = macd_signal

    def _compute_rsi(self, series: pd.Series) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=self.rsi_period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=self.rsi_period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _compute_macd(self, series: pd.Series):
        ema_fast   = series.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow   = series.ewm(span=self.macd_slow, adjust=False).mean()
        macd_line  = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.macd_signal, adjust=False).mean()
        histogram  = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _compute_bollinger(self, series: pd.Series, period: int = 20):
        ma    = series.rolling(period).mean()
        std   = series.rolling(period).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        return upper, ma, lower

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # RSI
        df["RSI"] = self._compute_rsi(df["Close"])

        # MACD
        df["MACD"], df["MACD_Signal"], df["MACD_Hist"] = self._compute_macd(df["Close"])

        # MACD crossover detection
        df["MACD_diff"]      = df["MACD"] - df["MACD_Signal"]
        df["MACD_diff_prev"] = df["MACD_diff"].shift(1)

        # Trend filter: 50-day SMA
        df["SMA_50"] = df["Close"].rolling(50).mean()

        # Bollinger Bands
        df["BB_upper"], df["BB_mid"], df["BB_lower"] = self._compute_bollinger(df["Close"])

        df["Signal"] = "HOLD"
        df["Signal_Strength"] = 0.0

        for i in range(1, len(df)):
            curr = df.iloc[i]
            prev = df.iloc[i - 1]

            rsi = curr["RSI"]
            macd_cross_up   = prev["MACD_diff"] <= 0 and curr["MACD_diff"] > 0
            macd_cross_down = prev["MACD_diff"] >= 0 and curr["MACD_diff"] < 0

            # ── BUY signal ──
            if macd_cross_up and rsi < self.rsi_oversold + 15:
                # Price not in a strong downtrend
                if curr["Close"] > curr["SMA_50"] * 0.95:
                    strength = 0.9 if rsi < self.rsi_oversold else 0.6
                    df.iloc[i, df.columns.get_loc("Signal")] = "BUY"
                    df.iloc[i, df.columns.get_loc("Signal_Strength")] = strength

            # ── SELL signal ──
            elif macd_cross_down and rsi > self.rsi_overbought - 10:
                df.iloc[i, df.columns.get_loc("Signal")] = "SELL"
                df.iloc[i, df.columns.get_loc("Signal_Strength")] = (
                    0.9 if rsi > self.rsi_overbought else 0.6
                )

        return df

    def get_current_signal(self, df: pd.DataFrame) -> Signal:
        if not self.validate_df(df, min_rows=self.macd_slow + 10):
            return "HOLD"
        result = self.generate_signals(df)
        return result["Signal"].iloc[-1]

    def get_indicators(self, df: pd.DataFrame) -> dict:
        result = self.generate_signals(df)
        last = result.iloc[-1]
        return {
            "RSI":         round(last.get("RSI", 0), 2),
            "MACD":        round(last.get("MACD", 0), 4),
            "MACD Signal": round(last.get("MACD_Signal", 0), 4),
            "MACD Hist":   round(last.get("MACD_Hist", 0), 4),
            "Signal":      last["Signal"],
        }
