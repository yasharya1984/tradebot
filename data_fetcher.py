"""
Data Fetcher
============
Fetches historical and live market data.
- Historical: yfinance (free, no API key needed)
- Live (simulation): yfinance intraday
- Live (real trading): Zerodha Kite API
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class DataFetcher:
    """Unified data fetcher for NSE stocks."""

    def __init__(self, kite=None):
        """
        Args:
            kite: KiteConnect instance (optional, for live trading mode)
        """
        self.kite = kite
        self._cache: Dict[str, pd.DataFrame] = {}
        self._cache_time: Dict[str, datetime] = {}
        self.cache_duration = 300  # 5 minutes

    # ──────────────────────────────────────────
    # Historical Data (via yfinance)
    # ──────────────────────────────────────────

    def get_historical(
        self,
        symbol: str,
        period_days: int = 365,
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data for a symbol.

        Args:
            symbol:      NSE symbol e.g. "RELIANCE.NS"
            period_days: Number of days of history
            interval:    "1d", "1h", "15m" etc.

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume
        """
        cache_key = f"{symbol}_{period_days}_{interval}"

        # Return cached data if fresh
        if cache_key in self._cache:
            age = (datetime.now() - self._cache_time[cache_key]).seconds
            if age < self.cache_duration:
                return self._cache[cache_key]

        try:
            end = datetime.now()
            start = end - timedelta(days=period_days + 10)  # Extra buffer

            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start, end=end, interval=interval)

            if df.empty:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()

            # Guard against missing columns (can happen with delisted/renamed tickers)
            required = {"Open", "High", "Low", "Close", "Volume"}
            if not required.issubset(df.columns):
                logger.warning(f"Incomplete OHLCV columns for {symbol}: {list(df.columns)}")
                return pd.DataFrame()

            # Clean up
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.index = pd.to_datetime(df.index)
            df = df.dropna()
            df = df.tail(period_days)

            self._cache[cache_key] = df
            self._cache_time[cache_key] = datetime.now()
            logger.debug(f"Fetched {len(df)} rows for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch data for {symbol}: {e}")
            return pd.DataFrame()

    def get_multiple_historical(
        self,
        symbols: List[str],
        period_days: int = 365,
        interval: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """Fetch historical data for multiple symbols."""
        results = {}
        for i, symbol in enumerate(symbols):
            df = self.get_historical(symbol, period_days, interval)
            if not df.empty:
                results[symbol] = df
            # Rate limit to avoid yfinance bans
            if i > 0 and i % 10 == 0:
                time.sleep(1)
        return results

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current/last traded price for a symbol."""
        # If Kite is connected, use it for real-time price
        if self.kite:
            return self._get_kite_price(symbol)

        # Otherwise use yfinance
        try:
            ticker = yf.Ticker(symbol)
            # fast_info.last_price can be None when market is closed or data is unavailable
            info = ticker.fast_info
            price = getattr(info, "last_price", None)
            if price is not None and price > 0:
                return float(price)
            # Fall back to last close from a short history pull
            df = ticker.history(period="5d", interval="1d")
            if not df.empty and "Close" in df.columns:
                return float(df["Close"].iloc[-1])
            return None
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return None

    def get_multiple_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Get current prices for multiple symbols."""
        prices = {}
        for symbol in symbols:
            price = self.get_current_price(symbol)
            if price:
                prices[symbol] = price
        return prices

    def get_intraday(self, symbol: str, interval: str = "15m") -> pd.DataFrame:
        """Fetch intraday data for today."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d", interval=interval)
            if df.empty:
                return pd.DataFrame()
            required = {"Open", "High", "Low", "Close", "Volume"}
            if not required.issubset(df.columns):
                return pd.DataFrame()
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            return df
        except Exception as e:
            logger.error(f"Intraday fetch failed for {symbol}: {e}")
            return pd.DataFrame()

    # ──────────────────────────────────────────
    # Zerodha Kite (Live Data)
    # ──────────────────────────────────────────

    def _get_kite_price(self, symbol: str) -> Optional[float]:
        """Get real-time price via Kite API."""
        try:
            # Convert NSE symbol format (e.g., RELIANCE.NS → NSE:RELIANCE)
            nse_sym = symbol.replace(".NS", "")
            quote = self.kite.quote(f"NSE:{nse_sym}")
            return quote[f"NSE:{nse_sym}"]["last_price"]
        except Exception as e:
            logger.error(f"Kite price fetch failed for {symbol}: {e}")
            return None

    def get_kite_historical(
        self,
        symbol: str,
        from_date: datetime,
        to_date: datetime,
        interval: str = "day",
    ) -> pd.DataFrame:
        """Fetch historical data via Kite API (for live mode)."""
        if not self.kite:
            raise RuntimeError("Kite not connected")
        try:
            nse_sym = symbol.replace(".NS", "")
            # Get instrument token
            instruments = self.kite.instruments("NSE")
            instrument = next(
                (i for i in instruments if i["tradingsymbol"] == nse_sym), None
            )
            if not instrument:
                logger.error(f"Instrument not found: {nse_sym}")
                return pd.DataFrame()

            records = self.kite.historical_data(
                instrument["instrument_token"], from_date, to_date, interval
            )
            df = pd.DataFrame(records)
            df = df.rename(columns={"date": "Date"})
            df = df.set_index("Date")
            df.columns = [c.capitalize() for c in df.columns]
            return df
        except Exception as e:
            logger.error(f"Kite historical failed for {symbol}: {e}")
            return pd.DataFrame()

    def is_market_open(self) -> bool:
        """Check if NSE market is currently open (9:15 AM – 3:30 PM IST, Mon–Fri)."""
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        if now.weekday() >= 5:  # Saturday/Sunday
            return False
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close

    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()
        self._cache_time.clear()
