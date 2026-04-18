"""
Trend-Strength Strategy
=======================
Signal strategy designed for stocks that already cleared the TrendStrength
scanner (RS + Volume + ADX filter), but can be applied to any candidate list.

Entry (BUY):  ADX > RS_ADX_MIN  AND  Price > SMA50  AND  MACD histogram > 0
              ↳ Confirmed strong trend + MA support + bullish MACD momentum

Exit  (SELL): Price < SMA50  OR  ADX < ADX_EXIT_THRESHOLD (20)
              ↳ Trend support broken or directional strength fading

Hold:         All other conditions (waiting for entry or protecting position).

This strategy avoids chasing — it only enters when all three confirmation
signals agree, and exits decisively when the trend structure weakens.
"""

import pandas as pd

from config import (
    RS_SMA_SHORT, RS_ADX_PERIOD, RS_ADX_MIN,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
)
from ta.trend import ADXIndicator, MACD as MACDIndicator

from .base import BaseStrategy, Signal

# ADX level at which we consider the trend has faded (exit threshold)
_ADX_EXIT_THRESHOLD = 20

# Minimum bars needed for all indicators to be reliable
_MIN_ROWS = RS_SMA_SHORT + MACD_SLOW + MACD_SIGNAL + 15  # ≈ 100


class TrendStrengthStrategy(BaseStrategy):
    """
    Trend-Strength: enters on ADX-confirmed trends above SMA50 with MACD support.
    Exits when trend structure weakens (ADX fades or price breaks SMA50).
    """

    name        = "Trend-Strength (RS+Vol+ADX)"
    description = (
        "Enters on confirmed strong trends (ADX>{adx_min} + above SMA50 + MACD bullish); "
        "exits on trend failure (ADX<{adx_exit} or price < SMA50)"
    ).format(adx_min=RS_ADX_MIN, adx_exit=_ADX_EXIT_THRESHOLD)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add a 'Signal' column (BUY / SELL / HOLD) to the full historical DataFrame.
        Used by the backtester.
        """
        if not self.validate_df(df, min_rows=_MIN_ROWS):
            df = df.copy()
            df["Signal"] = "HOLD"
            return df

        df = df.copy()

        # ── Compute indicators ────────────────────────────────────────
        df["SMA50"] = df["Close"].rolling(window=RS_SMA_SHORT).mean()

        adx_ind    = ADXIndicator(
            high=df["High"], low=df["Low"], close=df["Close"],
            window=RS_ADX_PERIOD, fillna=False,
        )
        df["ADX"] = adx_ind.adx()

        macd_ind = MACDIndicator(
            close=df["Close"],
            window_slow=MACD_SLOW,
            window_fast=MACD_FAST,
            window_sign=MACD_SIGNAL,
            fillna=False,
        )
        df["MACD_hist"] = macd_ind.macd_diff()

        # ── Generate signals bar-by-bar ───────────────────────────────
        signals     = []
        in_position = False

        for _, row in df.iterrows():
            price     = row["Close"]
            adx       = row["ADX"]
            sma50     = row["SMA50"]
            macd_hist = row["MACD_hist"]

            # Wait until all indicators have valid values
            if pd.isna(adx) or pd.isna(sma50) or pd.isna(macd_hist):
                signals.append("HOLD")
                continue

            if in_position:
                # Exit: trend weakening or price falls below SMA50
                if price < sma50 or adx < _ADX_EXIT_THRESHOLD:
                    signals.append("SELL")
                    in_position = False
                else:
                    signals.append("HOLD")
            else:
                # Entry: strong confirmed trend + above SMA50 + bullish MACD
                if adx > RS_ADX_MIN and price > sma50 and macd_hist > 0:
                    signals.append("BUY")
                    in_position = True
                else:
                    signals.append("HOLD")

        df["Signal"] = signals
        return df

    def get_current_signal(self, df: pd.DataFrame) -> Signal:
        """Return the signal for the latest bar. Used by paper trading tick."""
        if not self.validate_df(df, min_rows=_MIN_ROWS):
            return "HOLD"
        signals_df = self.generate_signals(df)
        sig = signals_df["Signal"].iloc[-1]
        return sig if sig in ("BUY", "SELL", "HOLD") else "HOLD"
