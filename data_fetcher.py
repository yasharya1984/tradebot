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

# Maximum symbols per single yf.download() call.
# yfinance uses an internal thread pool; batches beyond ~100 become unreliable.
_YF_BATCH_SIZE = 60


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

            _t0 = time.perf_counter()
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start, end=end, interval=interval)
            _elapsed = time.perf_counter() - _t0
            if _elapsed > 2.0:
                msg = f"[DEBUG] yfinance fetch took: {_elapsed:.1f} seconds (single: {symbol})"
                print(msg, flush=True)
                logger.info(msg)

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
        """Fetch historical data for multiple symbols (sequential fallback).
        Prefer get_multiple_historical_batch() for bulk requests."""
        results = {}
        for i, symbol in enumerate(symbols):
            df = self.get_historical(symbol, period_days, interval)
            if not df.empty:
                results[symbol] = df
            # Rate limit to avoid yfinance bans
            if i > 0 and i % 10 == 0:
                time.sleep(1)
        return results

    def get_multiple_historical_batch(
        self,
        symbols: List[str],
        period_days: int = 60,
        interval: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """
        Batch-fetch historical OHLCV for many symbols using yf.download().

        yf.download() uses an internal ThreadPoolExecutor so a single call for
        60 symbols is dramatically faster than 60 sequential Ticker.history()
        calls.  Symbols are processed in chunks of _YF_BATCH_SIZE to stay within
        reliable request limits.

        Each batch prints:
            [DEBUG] yfinance fetch took: X.X seconds (N symbols, Xd history)

        Returns a dict {symbol: DataFrame} for symbols that returned data.
        Falls back to individual get_historical() for any symbol that fails.
        """
        if not symbols:
            return {}

        required_cols = {"Open", "High", "Low", "Close", "Volume"}

        # ── Serve fresh items from cache ───────────────────────────────
        results: Dict[str, pd.DataFrame] = {}
        stale: List[str] = []
        for sym in symbols:
            ck = f"{sym}_{period_days}_{interval}"
            if ck in self._cache:
                age = (datetime.now() - self._cache_time[ck]).seconds
                if age < self.cache_duration:
                    results[sym] = self._cache[ck]
                    continue
            stale.append(sym)

        if not stale:
            return results

        # ── Process in batches ─────────────────────────────────────────
        end_str   = datetime.now().strftime("%Y-%m-%d")
        start_str = (datetime.now() - timedelta(days=period_days + 15)).strftime("%Y-%m-%d")

        for batch_start in range(0, len(stale), _YF_BATCH_SIZE):
            batch = stale[batch_start: batch_start + _YF_BATCH_SIZE]
            t0 = time.perf_counter()
            try:
                raw = yf.download(
                    batch,
                    start=start_str,
                    end=end_str,
                    interval=interval,
                    group_by="ticker",
                    progress=False,
                    threads=True,
                    auto_adjust=True,
                )
                elapsed = time.perf_counter() - t0
                msg = (
                    f"[DEBUG] yfinance fetch took: {elapsed:.1f} seconds "
                    f"({len(batch)} symbols, {period_days}d history)"
                )
                print(msg, flush=True)
                logger.info(msg)

                if raw.empty:
                    raise ValueError("Empty result from yf.download")

                # yf.download returns a flat DataFrame for a single ticker
                if len(batch) == 1:
                    sym = batch[0]
                    df  = raw.copy()
                    if required_cols.issubset(df.columns):
                        df = df[list(required_cols)].dropna().tail(period_days)
                        if not df.empty:
                            results[sym] = df
                            ck = f"{sym}_{period_days}_{interval}"
                            self._cache[ck]      = df
                            self._cache_time[ck] = datetime.now()
                else:
                    # MultiIndex columns: top level = symbol, second = OHLCV field
                    top_level = raw.columns.get_level_values(0).unique()
                    for sym in batch:
                        try:
                            if sym not in top_level:
                                continue
                            df = raw[sym].copy()
                            if not required_cols.issubset(df.columns):
                                continue
                            df = df[list(required_cols)].dropna().tail(period_days)
                            if df.empty:
                                continue
                            results[sym] = df
                            ck = f"{sym}_{period_days}_{interval}"
                            self._cache[ck]      = df
                            self._cache_time[ck] = datetime.now()
                        except Exception as _sym_exc:
                            logger.debug(f"Batch parse failed for {sym}: {_sym_exc}")

            except Exception as exc:
                elapsed = time.perf_counter() - t0
                msg = (
                    f"[DEBUG] yfinance fetch took: {elapsed:.1f} seconds "
                    f"(batch FAILED: {exc})"
                )
                print(msg, flush=True)
                logger.warning(msg)
                # Fall back to sequential for this batch
                for sym in batch:
                    df = self.get_historical(sym, period_days, interval)
                    if not df.empty:
                        results[sym] = df

        return results

    def get_multiple_prices_fast(self, symbols: List[str]) -> Dict[str, float]:
        """
        Batch-fetch the latest close price for many symbols using yf.download().

        Uses a 2-day window so the most recent trading day's close is always
        available.  The result is cached for cache_duration seconds.

        Prints:
            [DEBUG] yfinance fetch took: X.X seconds (N symbols, prices)

        Falls back to individual get_current_price() calls on failure.
        """
        if not symbols:
            return {}

        # ── Serve from cache ───────────────────────────────────────────
        prices: Dict[str, float] = {}
        stale: List[str] = []
        for sym in symbols:
            ck = f"{sym}_price"
            if ck in self._cache:
                age = (datetime.now() - self._cache_time[ck]).seconds
                if age < self.cache_duration:
                    prices[sym] = float(self._cache[ck])
                    continue
            stale.append(sym)

        if not stale:
            return prices

        # ── Batch download ─────────────────────────────────────────────
        for batch_start in range(0, len(stale), _YF_BATCH_SIZE):
            batch = stale[batch_start: batch_start + _YF_BATCH_SIZE]
            t0 = time.perf_counter()
            try:
                raw = yf.download(
                    batch,
                    period="3d",
                    interval="1d",
                    group_by="ticker",
                    progress=False,
                    threads=True,
                    auto_adjust=True,
                )
                elapsed = time.perf_counter() - t0
                msg = (
                    f"[DEBUG] yfinance fetch took: {elapsed:.1f} seconds "
                    f"({len(batch)} symbols, prices)"
                )
                print(msg, flush=True)
                logger.info(msg)

                if raw.empty:
                    raise ValueError("Empty result from yf.download")

                if len(batch) == 1:
                    sym = batch[0]
                    if "Close" in raw.columns:
                        val = raw["Close"].dropna()
                        if not val.empty:
                            prices[sym] = float(val.iloc[-1])
                            ck = f"{sym}_price"
                            self._cache[ck]      = prices[sym]
                            self._cache_time[ck] = datetime.now()
                else:
                    top_level = raw.columns.get_level_values(0).unique()
                    for sym in batch:
                        try:
                            if sym not in top_level:
                                continue
                            col = raw[sym]["Close"].dropna()
                            if col.empty:
                                continue
                            prices[sym] = float(col.iloc[-1])
                            ck = f"{sym}_price"
                            self._cache[ck]      = prices[sym]
                            self._cache_time[ck] = datetime.now()
                        except Exception as _sym_exc:
                            logger.debug(f"Batch price parse failed for {sym}: {_sym_exc}")

            except Exception as exc:
                elapsed = time.perf_counter() - t0
                msg = (
                    f"[DEBUG] yfinance fetch took: {elapsed:.1f} seconds "
                    f"(batch FAILED: {exc})"
                )
                print(msg, flush=True)
                logger.warning(msg)
                # Fall back to individual calls
                for sym in batch:
                    p = self.get_current_price(sym)
                    if p is not None:
                        prices[sym] = p

        return prices

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
