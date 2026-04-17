"""
Moving Average Crossover Strategy
==================================
BUY  when short-term MA crosses ABOVE long-term MA (Golden Cross)
SELL when short-term MA crosses BELOW long-term MA (Death Cross)

Also uses:
- Price > 200-day MA as a trend filter (only long in uptrend)
- Volume confirmation: volume should be above average on signal day
"""

import pandas as pd
import numpy as np

from .base import BaseStrategy, Signal
from config import MA_SHORT_PERIOD, MA_LONG_PERIOD


class MovingAverageCrossover(BaseStrategy):

    name = "Moving Average Crossover"
    description = (
        "Buys on Golden Cross (short MA > long MA) and sells on Death Cross. "
        "Uses 200-day MA as trend filter and volume confirmation."
    )

    def __init__(
        self,
        short_period: int = MA_SHORT_PERIOD,
        long_period: int = MA_LONG_PERIOD,
    ):
        self.short = short_period
        self.long = long_period

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute indicators and signals for entire history."""
        df = df.copy()

        # Moving averages
        df[f"MA_{self.short}"] = df["Close"].rolling(self.short).mean()
        df[f"MA_{self.long}"] = df["Close"].rolling(self.long).mean()
        df["MA_200"] = df["Close"].rolling(min(200, len(df))).mean()

        # Volume MA for confirmation
        df["Vol_MA_20"] = df["Volume"].rolling(20).mean()

        # Crossover detection
        df["MA_diff"] = df[f"MA_{self.short}"] - df[f"MA_{self.long}"]
        df["MA_diff_prev"] = df["MA_diff"].shift(1)

        # Signal logic
        df["Signal"] = "HOLD"
        df["Signal_Strength"] = 0.0

        for i in range(1, len(df)):
            curr = df.iloc[i]
            prev = df.iloc[i - 1]

            # Golden cross: short crosses above long
            if prev["MA_diff"] <= 0 and curr["MA_diff"] > 0:
                # Trend filter: price should be above 200 MA
                if curr["Close"] > curr["MA_200"] * 0.98:
                    # Volume confirmation
                    vol_ok = curr["Volume"] > curr["Vol_MA_20"] * 0.8
                    df.iloc[i, df.columns.get_loc("Signal")] = "BUY"
                    df.iloc[i, df.columns.get_loc("Signal_Strength")] = (
                        0.8 if vol_ok else 0.5
                    )

            # Death cross: short crosses below long
            elif prev["MA_diff"] >= 0 and curr["MA_diff"] < 0:
                df.iloc[i, df.columns.get_loc("Signal")] = "SELL"
                df.iloc[i, df.columns.get_loc("Signal_Strength")] = 0.9

        return df

    def get_current_signal(self, df: pd.DataFrame) -> Signal:
        """Get signal for most recent bar."""
        if not self.validate_df(df, min_rows=self.long + 5):
            return "HOLD"
        result = self.generate_signals(df)
        return result["Signal"].iloc[-1]

    def get_indicators(self, df: pd.DataFrame) -> dict:
        """Return latest indicator values for dashboard display."""
        result = self.generate_signals(df)
        last = result.iloc[-1]
        return {
            f"MA {self.short}":  round(last.get(f"MA_{self.short}", 0), 2),
            f"MA {self.long}":   round(last.get(f"MA_{self.long}", 0), 2),
            "MA 200":            round(last.get("MA_200", 0), 2),
            "Signal":            last["Signal"],
        }
