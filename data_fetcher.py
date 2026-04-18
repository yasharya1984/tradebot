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

import concurrent.futures
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from market_utils import NSE_HOLIDAYS, is_market_open as _mu_is_market_open

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

    def get_current_price_batch(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """
        Batch-fetch the LTP (Last Traded Price) for many symbols concurrently.

        Uses fast_info.last_price — the actual intraday LTP — instead of the
        daily-close bar returned by yf.download().  This fixes the bug where
        get_multiple_prices_fast() showed yesterday's closing price even after
        today's market had opened.

        Staleness guard: if the NSE market has opened today but the only price
        available is from a previous trading day, the symbol is treated as an
        error (returns None) so the UI can display ❌ instead of stale data.

        Returns:
            {symbol: float}   – verified LTP
            {symbol: None}    – price unavailable / stale (show ❌, skip trading)

        Prints:
            [DEBUG] yfinance LTP batch: X.Xs (N symbols, N OK, N errors)
            [DEBUG] yfinance LTP slow: X.Xs (<symbol>)   – for slow individual fetches
        """
        if not symbols:
            return {}

        if self.kite:
            return {s: self._get_kite_price(s) for s in symbols}

        market_opened_today = self._market_opened_today()
        today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()

        # ── Serve from cache ───────────────────────────────────────────
        prices: Dict[str, Optional[float]] = {}
        stale: List[str] = []
        for sym in symbols:
            ck = f"{sym}_ltp"
            if ck in self._cache:
                age = (datetime.now() - self._cache_time[ck]).seconds
                if age < self.cache_duration:
                    prices[sym] = self._cache[ck]   # may be None (cached error)
                    continue
            stale.append(sym)

        if not stale:
            return prices

        results: Dict[str, Optional[float]] = {}
        errors: List[str] = []

        def _fetch_one(sym: str) -> None:
            try:
                ticker = yf.Ticker(sym)
                _t = time.perf_counter()
                info = ticker.fast_info
                price = getattr(info, "last_price", None)
                elapsed = time.perf_counter() - _t
                if elapsed > 3.0:
                    msg = f"[DEBUG] yfinance LTP slow: {elapsed:.1f}s ({sym})"
                    print(msg, flush=True)
                    logger.info(msg)

                if price is not None and float(price) > 0:
                    results[sym] = float(price)
                    return

                # fast_info gave nothing — fall back to recent history
                df = ticker.history(period="5d", interval="1d")
                if not df.empty and "Close" in df.columns:
                    last_idx = df.index[-1]
                    last_date = (
                        last_idx.date() if hasattr(last_idx, "date") else last_idx
                    )
                    if market_opened_today and last_date < today_ist:
                        # Only yesterday's bar available but market opened today → stale
                        results[sym] = None
                        errors.append(sym)
                    else:
                        results[sym] = float(df["Close"].iloc[-1])
                else:
                    results[sym] = None
                    errors.append(sym)
            except Exception as exc:
                logger.error(f"LTP fetch failed for {sym}: {exc}")
                results[sym] = None
                errors.append(sym)

        t0 = time.perf_counter()
        max_workers = min(20, len(stale))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(_fetch_one, stale))
        elapsed = time.perf_counter() - t0

        n_ok  = sum(1 for v in results.values() if v is not None)
        n_err = len(errors)
        msg = (
            f"[DEBUG] yfinance LTP batch: {elapsed:.1f}s "
            f"({len(stale)} symbols, {n_ok} OK, {n_err} errors)"
        )
        print(msg, flush=True)
        logger.info(msg)
        if errors:
            err_msg = f"[DEBUG] LTP errors (❌): {errors}"
            print(err_msg, flush=True)
            logger.warning(err_msg)

        # Cache and merge results
        for sym, p in results.items():
            ck = f"{sym}_ltp"
            self._cache[ck]      = p
            self._cache_time[ck] = datetime.now()
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

    def _market_opened_today(self) -> bool:
        """
        True if the NSE market has already opened today (even if currently closed after 15:30).
        Returns False on weekends and NSE holidays.  Used for price-staleness checks.
        """
        now   = datetime.now(ZoneInfo("Asia/Kolkata"))
        today = now.date()
        if now.weekday() >= 5 or today in NSE_HOLIDAYS:
            return False
        return now.hour > 9 or (now.hour == 9 and now.minute >= 15)

    def is_market_open(self) -> bool:
        """Check if NSE market is currently open (9:15 AM – 3:30 PM IST, Mon–Fri, non-holiday)."""
        return _mu_is_market_open()

    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()
        self._cache_time.clear()
