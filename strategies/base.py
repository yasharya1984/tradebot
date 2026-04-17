"""Base Strategy Interface."""

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd

Signal = Literal["BUY", "SELL", "HOLD"]


class BaseStrategy(ABC):
    """All strategies must implement this interface."""

    name: str = "BaseStrategy"
    description: str = ""

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add a 'Signal' column to the dataframe.

        Args:
            df: OHLCV DataFrame

        Returns:
            DataFrame with added columns including 'Signal' (BUY/SELL/HOLD)
        """
        ...

    @abstractmethod
    def get_current_signal(self, df: pd.DataFrame) -> Signal:
        """Return the signal for the latest bar."""
        ...

    def validate_df(self, df: pd.DataFrame, min_rows: int = 50) -> bool:
        """Validate that we have enough data."""
        if df is None or df.empty:
            return False
        if len(df) < min_rows:
            return False
        required = {"Open", "High", "Low", "Close", "Volume"}
        return required.issubset(df.columns)
