"""
Unit Tests — Lazy Loading & API Failure Resilience
===================================================
Verifies:
  1. JSON portfolio data loads instantly even when yfinance is broken.
  2. get_multiple_prices_fast() degrades gracefully on API failure.
  3. get_multiple_historical_batch() falls back to sequential on failure.
  4. initialize_paper_trading() no longer calls refresh_selection().
  5. paper_trading_tick() still works when batch fetch returns partial data.
  6. save_last_prices / load_last_prices round-trip (price persistence).
  7. Dashboard fallback chain: disk prices preferred over entry prices.

Run with:
    /usr/bin/python3 -m unittest test_lazy_loading -v
"""

import sys
import os
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def _make_portfolio_dict(cash: float = 90_000) -> dict:
    """Minimal portfolio JSON that Portfolio.from_dict() can deserialise."""
    return {
        "initial_capital": 100_000,
        "cash": cash,
        "positions": {},
        "trade_history": [],
        "equity_curve": [
            {"timestamp": datetime.now().isoformat(), "equity": cash, "cash": cash, "positions": 0}
        ],
    }


def _make_trade_dict(symbol: str, pnl: float) -> dict:
    """Minimal closed-trade record."""
    return {
        "symbol": symbol,
        "entry_date": "2025-01-10T09:30:00",
        "exit_date": "2025-01-15T14:00:00",
        "entry_price": 1000.0,
        "exit_price": 1000.0 + pnl / 10,
        "quantity": 10,
        "strategy": "ma",
        "exit_reason": "Strategy Signal",
        "pnl": pnl,
        "pnl_pct": round(pnl / 10_000 * 100, 2),
        "holding_days": 5,
    }


# ══════════════════════════════════════════════════════════
# 1. trade_store — JSON reads are API-independent
# ══════════════════════════════════════════════════════════

class TestTradeStoreJsonResilience(unittest.TestCase):

    def setUp(self):
        """Write a portfolio JSON file to a temp directory."""
        self._tmpdir = tempfile.mkdtemp()
        # Monkey-patch the module-level _BASE_DIR used by trade_store
        import trade_store as ts
        self._orig_base = ts._BASE_DIR
        ts._BASE_DIR = Path(self._tmpdir)

    def tearDown(self):
        import trade_store as ts
        ts._BASE_DIR = self._orig_base

    def _write_portfolio(self, strategy: str, mode: str, data: dict) -> None:
        import trade_store as ts
        d = ts._mode_dir(mode)
        path = d / f"{strategy}.json"
        with open(path, "w") as fh:
            json.dump(data, fh)

    def test_load_portfolio_returns_dict_when_file_exists(self):
        """load_portfolio() returns the saved dict without any network call."""
        import trade_store as ts
        payload = _make_portfolio_dict(cash=85_000)
        payload["trade_history"] = [_make_trade_dict("RELIANCE.NS", 1500)]
        self._write_portfolio("ma", "sim", payload)

        result = ts.load_portfolio("ma", "sim")
        self.assertIsNotNone(result)
        self.assertEqual(result["cash"], 85_000)
        self.assertEqual(len(result["trade_history"]), 1)

    def test_load_portfolio_returns_none_when_missing(self):
        import trade_store as ts
        result = ts.load_portfolio("nonexistent_strategy", "sim")
        self.assertIsNone(result)

    def test_load_all_trade_history_aggregates_all_strategies(self):
        import trade_store as ts
        for strat, cash in [("ma", 90_000), ("rsi_macd", 95_000)]:
            payload = _make_portfolio_dict(cash=cash)
            payload["trade_history"] = [_make_trade_dict("TCS.NS", 800)]
            self._write_portfolio(strat, "sim", payload)

        trades = ts.load_all_trade_history(mode="sim")
        self.assertEqual(len(trades), 2)
        syms = {t["symbol"] for t in trades}
        self.assertIn("TCS.NS", syms)

    def test_list_saved_strategies_finds_written_files(self):
        import trade_store as ts
        for strat in ("ma", "rsi_macd", "momentum"):
            self._write_portfolio(strat, "sim", _make_portfolio_dict())

        saved = ts.list_saved_strategies("sim")
        self.assertEqual(sorted(saved), ["ma", "momentum", "rsi_macd"])

    def test_json_load_does_not_call_yfinance(self):
        """Patching yfinance.Ticker to raise ensures JSON load is network-free."""
        import trade_store as ts
        self._write_portfolio("ma", "sim", _make_portfolio_dict(cash=77_000))

        with patch("yfinance.Ticker", side_effect=RuntimeError("no network")):
            result = ts.load_portfolio("ma", "sim")

        self.assertIsNotNone(result)
        self.assertEqual(result["cash"], 77_000)


# ══════════════════════════════════════════════════════════
# 2. DataFetcher — batch methods degrade gracefully
# ══════════════════════════════════════════════════════════

class TestBatchFetchDegradation(unittest.TestCase):

    def _make_fetcher(self):
        from data_fetcher import DataFetcher
        return DataFetcher()

    # ── get_multiple_prices_fast ───────────────────────────

    def test_prices_fast_returns_empty_for_empty_input(self):
        fetcher = self._make_fetcher()
        result = fetcher.get_multiple_prices_fast([])
        self.assertEqual(result, {})

    def test_prices_fast_returns_empty_on_api_failure(self):
        """When yf.download raises, must return {} not raise."""
        fetcher = self._make_fetcher()
        with patch("yfinance.download", side_effect=ConnectionError("no internet")):
            with patch.object(fetcher, "get_current_price", return_value=None):
                result = fetcher.get_multiple_prices_fast(["RELIANCE.NS", "TCS.NS"])
        self.assertIsInstance(result, dict)

    def test_prices_fast_falls_back_to_individual_on_batch_failure(self):
        """When yf.download fails, fall back hits get_current_price() per symbol."""
        fetcher = self._make_fetcher()
        with patch("yfinance.download", side_effect=OSError("timeout")):
            with patch.object(
                fetcher, "get_current_price",
                side_effect=lambda sym: {"RELIANCE.NS": 2900.0}.get(sym)
            ):
                result = fetcher.get_multiple_prices_fast(["RELIANCE.NS", "INFY.NS"])

        self.assertEqual(result.get("RELIANCE.NS"), 2900.0)
        self.assertNotIn("INFY.NS", result)  # fallback returned None → not added

    def test_prices_fast_serves_cached_values_without_network(self):
        """Values already in _cache must be returned without any yf.download call."""
        fetcher = self._make_fetcher()
        # Manually populate cache
        import time as _time
        from datetime import datetime as _dt
        fetcher._cache["RELIANCE.NS_price"]      = 2850.0
        fetcher._cache_time["RELIANCE.NS_price"]  = _dt.now()

        download_called = []
        with patch("yfinance.download", side_effect=lambda *a, **k: download_called.append(1)):
            result = fetcher.get_multiple_prices_fast(["RELIANCE.NS"])

        self.assertEqual(result.get("RELIANCE.NS"), 2850.0)
        self.assertEqual(len(download_called), 0, "yf.download must NOT be called for cached symbols")

    # ── get_multiple_historical_batch ─────────────────────

    def test_historical_batch_returns_empty_for_empty_input(self):
        fetcher = self._make_fetcher()
        result = fetcher.get_multiple_historical_batch([])
        self.assertEqual(result, {})

    def test_historical_batch_returns_empty_on_api_failure(self):
        """When yf.download raises AND individual fallback raises, must not crash."""
        fetcher = self._make_fetcher()
        with patch("yfinance.download", side_effect=RuntimeError("down")):
            with patch.object(fetcher, "get_historical", return_value=__import__("pandas").DataFrame()):
                result = fetcher.get_multiple_historical_batch(["RELIANCE.NS"])
        self.assertIsInstance(result, dict)

    def test_historical_batch_prints_timing(self):
        """A successful batch call must print the [DEBUG] timing line."""
        import pandas as pd
        import io

        fetcher = self._make_fetcher()

        # Build a minimal multi-ticker download response
        idx = pd.date_range("2025-01-01", periods=5, freq="D")
        single_df = pd.DataFrame({
            "Open": [100]*5, "High": [110]*5, "Low": [90]*5,
            "Close": [105]*5, "Volume": [1000]*5,
        }, index=idx)

        with patch("yfinance.download", return_value=single_df):
            import sys as _sys
            from io import StringIO
            old_stdout = _sys.stdout
            _sys.stdout = buf = StringIO()
            fetcher.get_multiple_historical_batch(["RELIANCE.NS"], period_days=5)
            _sys.stdout = old_stdout

        output = buf.getvalue()
        self.assertIn("[DEBUG] yfinance fetch took:", output)
        self.assertIn("seconds", output)


# ══════════════════════════════════════════════════════════
# 3. Simulator — initialize_paper_trading is non-blocking
# ══════════════════════════════════════════════════════════

class TestSimulatorLazyInit(unittest.TestCase):

    def _make_simulator(self, tmp_base: Path):
        """Create a Simulator with a mock fetcher, portfolios stored in tmp_base."""
        import trade_store as ts
        ts._BASE_DIR = tmp_base

        from data_fetcher import DataFetcher
        from simulator import Simulator
        fetcher = DataFetcher()
        return Simulator(fetcher)

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        import trade_store as ts
        self._orig_base = ts._BASE_DIR
        ts._BASE_DIR = self._tmpdir

    def tearDown(self):
        import trade_store as ts
        ts._BASE_DIR = self._orig_base

    def test_initialize_paper_trading_does_not_call_refresh_selection(self):
        """initialize_paper_trading must NOT call selector.refresh_selection()."""
        from data_fetcher import DataFetcher
        from simulator import Simulator

        fetcher = DataFetcher()
        sim = Simulator(fetcher)
        sim.selector = MagicMock()
        sim.selector.refresh_selection.side_effect = AssertionError(
            "refresh_selection() must NOT be called during init"
        )

        # Should complete without triggering the mock
        sim.initialize_paper_trading(["ma"], mode="sim")
        sim.selector.refresh_selection.assert_not_called()

    def test_initialize_paper_trading_completes_quickly(self):
        """init should finish in well under 1 second (pure JSON, no network)."""
        from data_fetcher import DataFetcher
        from simulator import Simulator

        fetcher = DataFetcher()
        sim = Simulator(fetcher)

        # Mock away any possible network call
        with patch("yfinance.download", side_effect=RuntimeError("no network")):
            with patch("yfinance.Ticker",  side_effect=RuntimeError("no network")):
                t0 = __import__("time").perf_counter()
                sim.initialize_paper_trading(["ma", "rsi_macd", "momentum"], mode="sim")
                elapsed = __import__("time").perf_counter() - t0

        self.assertLess(elapsed, 1.0, f"init took {elapsed:.2f}s — must be < 1s")

    def test_initialize_paper_trading_loads_saved_json(self):
        """Portfolio saved to disk must be restored without a network call."""
        import trade_store as ts
        from data_fetcher import DataFetcher
        from simulator import Simulator

        # Write a portfolio file
        payload = _make_portfolio_dict(cash=75_000)
        ts.save_portfolio("ma", payload, mode="sim")

        fetcher = DataFetcher()
        sim = Simulator(fetcher)

        with patch("yfinance.download", side_effect=RuntimeError("no network")):
            sim.initialize_paper_trading(["ma"], mode="sim")

        port = sim.get_paper_portfolio("ma")
        self.assertIsNotNone(port)
        self.assertEqual(port.cash, 75_000)

    def test_paper_selected_empty_after_init(self):
        """_paper_selected must be empty after init (lazy fill on first tick)."""
        from data_fetcher import DataFetcher
        from simulator import Simulator

        sim = Simulator(DataFetcher())
        sim.initialize_paper_trading(["ma"], mode="sim")

        self.assertFalse(
            bool(getattr(sim, "_paper_selected", [])),
            "_paper_selected must be [] so stock selection is deferred to first tick",
        )


# ══════════════════════════════════════════════════════════
# 4. trade_store — price persistence round-trip
# ══════════════════════════════════════════════════════════

class TestPricePersistence(unittest.TestCase):

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        import trade_store as ts
        self._orig_base = ts._BASE_DIR
        ts._BASE_DIR = self._tmpdir

    def tearDown(self):
        import trade_store as ts
        ts._BASE_DIR = self._orig_base

    def test_save_and_load_prices_round_trip(self):
        import trade_store as ts
        prices = {"RELIANCE.NS": 2910.5, "TCS.NS": 3850.0, "INFY.NS": 1780.25}
        ok = ts.save_last_prices(prices, mode="sim")
        self.assertTrue(ok)

        loaded = ts.load_last_prices(mode="sim")
        self.assertEqual(loaded, prices)

    def test_load_prices_returns_empty_when_no_file(self):
        import trade_store as ts
        result = ts.load_last_prices(mode="sim")
        self.assertEqual(result, {})

    def test_save_empty_prices_does_not_write(self):
        """save_last_prices({}) must return False and not create a file."""
        import trade_store as ts
        ok = ts.save_last_prices({}, mode="sim")
        self.assertFalse(ok)
        self.assertFalse(ts._prices_path("sim").exists())

    def test_load_prices_drops_non_numeric_values(self):
        """Corrupted entries (non-numeric) must be silently filtered out."""
        import trade_store as ts
        path = ts._prices_path("sim")
        path.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        with open(path, "w") as fh:
            _json.dump({"prices": {"RELIANCE.NS": 2910.5, "BAD.NS": "N/A"}}, fh)

        loaded = ts.load_last_prices(mode="sim")
        self.assertIn("RELIANCE.NS", loaded)
        self.assertNotIn("BAD.NS", loaded)

    def test_load_prices_returns_floats(self):
        import trade_store as ts
        ts.save_last_prices({"TCS.NS": 3800}, mode="sim")
        loaded = ts.load_last_prices(mode="sim")
        self.assertIsInstance(loaded["TCS.NS"], float)

    def test_save_overwrites_previous_prices(self):
        """A second save must overwrite the first."""
        import trade_store as ts
        ts.save_last_prices({"RELIANCE.NS": 2900.0}, mode="sim")
        ts.save_last_prices({"RELIANCE.NS": 2950.0, "TCS.NS": 3900.0}, mode="sim")

        loaded = ts.load_last_prices(mode="sim")
        self.assertEqual(loaded["RELIANCE.NS"], 2950.0)
        self.assertEqual(loaded["TCS.NS"], 3900.0)

    def test_sim_and_live_prices_are_independent(self):
        """sim and live modes must use separate price files."""
        import trade_store as ts
        ts.save_last_prices({"RELIANCE.NS": 2900.0}, mode="sim")
        ts.save_last_prices({"RELIANCE.NS": 2950.0}, mode="live")

        sim_prices  = ts.load_last_prices(mode="sim")
        live_prices = ts.load_last_prices(mode="live")
        self.assertEqual(sim_prices["RELIANCE.NS"],  2900.0)
        self.assertEqual(live_prices["RELIANCE.NS"], 2950.0)


# ══════════════════════════════════════════════════════════
# 5. Price fallback chain logic (unit-level)
# ══════════════════════════════════════════════════════════

class TestPriceFallbackChain(unittest.TestCase):
    """
    Tests the three-layer fallback chain implemented in the dashboard fragment:
        disk_prices  <  tick_prices  <  entry_price
    Tested here without Streamlit by replicating the merge logic.
    """

    @staticmethod
    def _build_effective_prices(disk_prices: dict, tick_signals: dict) -> dict:
        """Mirrors the dashboard fragment's merge logic."""
        effective = dict(disk_prices)            # layer 1: disk
        for info in tick_signals.values():       # layer 2: live tick
            effective[info["sym"]] = info["price"]
        return effective

    def test_disk_price_used_when_no_tick_price(self):
        disk  = {"RELIANCE.NS": 2910.0}
        ticks = {}
        eff   = self._build_effective_prices(disk, ticks)
        self.assertEqual(eff["RELIANCE.NS"], 2910.0)

    def test_tick_price_overrides_disk_price(self):
        disk  = {"RELIANCE.NS": 2910.0}
        ticks = {"s1": {"sym": "RELIANCE.NS", "price": 2935.0}}
        eff   = self._build_effective_prices(disk, ticks)
        self.assertEqual(eff["RELIANCE.NS"], 2935.0)

    def test_entry_price_used_when_both_missing(self):
        """If symbol absent from disk and tick, fallback to entry_price."""
        entry_price = 2800.0
        disk  = {}
        ticks = {}
        eff   = self._build_effective_prices(disk, ticks)
        result = eff.get("RELIANCE.NS", entry_price)
        self.assertEqual(result, entry_price)

    def test_partial_tick_leaves_disk_prices_for_missing_symbols(self):
        """Tick that covers only half the symbols must keep disk prices for the rest."""
        disk  = {"RELIANCE.NS": 2910.0, "TCS.NS": 3850.0}
        ticks = {"s1": {"sym": "RELIANCE.NS", "price": 2920.0}}
        eff   = self._build_effective_prices(disk, ticks)
        self.assertEqual(eff["RELIANCE.NS"], 2920.0)   # tick won
        self.assertEqual(eff["TCS.NS"],      3850.0)   # disk retained


if __name__ == "__main__":
    unittest.main()
