"""
Trade Store
===========
Persistent JSON storage for bot portfolios and order logs.

Directory structure:
  trade_data/
    sim/          ← simulation mode
      {strategy}.json   – portfolio state per strategy
    live/         ← live Kite mode
      {strategy}.json

All public functions accept  mode="sim"  or  mode="live".

Usage:
    from trade_store import save_portfolio, load_portfolio, delete_all_portfolios
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent / "trade_data"


# ─────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────

def _mode_dir(mode: str) -> Path:
    """Return (and create) the correct subdirectory for the given mode."""
    d = _BASE_DIR / mode
    d.mkdir(parents=True, exist_ok=True)
    return d


def _portfolio_path(strategy_name: str, mode: str) -> Path:
    return _mode_dir(mode) / f"{strategy_name}.json"


# ─────────────────────────────────────────────────────────
# Portfolio persistence
# ─────────────────────────────────────────────────────────

def save_portfolio(strategy_name: str, portfolio_dict: dict, mode: str = "sim") -> bool:
    """Persist a portfolio dict to disk.  Returns True on success."""
    path = _portfolio_path(strategy_name, mode)
    try:
        payload = dict(portfolio_dict)
        payload["_saved_at"] = datetime.now().isoformat()
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        return True
    except Exception as exc:
        logger.error(f"trade_store: save failed [{mode}/{strategy_name}]: {exc}")
        return False


def load_portfolio(strategy_name: str, mode: str = "sim") -> Optional[dict]:
    """Load a saved portfolio dict.  Returns None if not found or corrupt."""
    path = _portfolio_path(strategy_name, mode)
    if not path.exists():
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception as exc:
        logger.error(f"trade_store: load failed [{mode}/{strategy_name}]: {exc}")
        return None


def delete_portfolio(strategy_name: str, mode: str = "sim") -> bool:
    """Delete one strategy's saved state."""
    path = _portfolio_path(strategy_name, mode)
    if path.exists():
        try:
            path.unlink()
            logger.info(f"trade_store: deleted [{mode}/{strategy_name}]")
            return True
        except Exception as exc:
            logger.error(f"trade_store: delete failed [{mode}/{strategy_name}]: {exc}")
    return False


def delete_all_portfolios(mode: str = "sim") -> int:
    """Delete every saved portfolio for the given mode.  Returns count deleted."""
    d = _mode_dir(mode)
    count = 0
    for f in d.glob("*.json"):
        if f.name == "orders.json":
            continue   # handled separately by bot_orders
        try:
            f.unlink()
            count += 1
        except Exception:
            pass
    logger.info(f"trade_store: deleted {count} portfolio file(s) [{mode}]")
    return count


def list_saved_strategies(mode: str = "sim") -> list:
    """Return list of strategy names that have a saved file on disk."""
    d = _mode_dir(mode)
    names = []
    for f in sorted(d.glob("*.json")):
        if f.name != "orders.json":
            names.append(f.stem)
    return names


def get_last_save_time(strategy_name: str, mode: str = "sim") -> Optional[datetime]:
    """Return the filesystem mtime of a strategy's save file."""
    path = _portfolio_path(strategy_name, mode)
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime)


# ─────────────────────────────────────────────────────────
# Last-known price cache (one file per mode)
# ─────────────────────────────────────────────────────────

def _prices_path(mode: str) -> Path:
    return _mode_dir(mode) / "prices.json"


def save_last_prices(prices: dict, mode: str = "sim") -> bool:
    """
    Persist the latest live prices fetched by yfinance so they survive
    an app restart and can be shown before the first new tick completes.

    prices: {symbol_ns: float}  e.g. {"RELIANCE.NS": 2910.5, ...}
    Returns True on success.
    """
    if not prices:
        return False
    path = _prices_path(mode)
    try:
        with open(path, "w") as fh:
            json.dump(
                {"prices": prices, "_saved_at": datetime.now().isoformat()},
                fh,
                indent=2,
            )
        return True
    except Exception as exc:
        logger.error(f"trade_store: save_last_prices failed [{mode}]: {exc}")
        return False


def load_last_prices(mode: str = "sim") -> dict:
    """
    Return the last prices saved by save_last_prices().
    Returns an empty dict if the file doesn't exist or is unreadable.
    """
    path = _prices_path(mode)
    if not path.exists():
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
        prices = data.get("prices", {})
        # Values must be numeric — drop anything else (corrupted entries)
        return {k: float(v) for k, v in prices.items() if isinstance(v, (int, float))}
    except Exception as exc:
        logger.error(f"trade_store: load_last_prices failed [{mode}]: {exc}")
        return {}


def load_all_trade_history(mode: str = "sim") -> list:
    """
    Return a combined list of all closed trade dicts from every saved strategy
    for the given mode.  Used by the Historical Performance tab.
    """
    all_trades = []
    for strategy_name in list_saved_strategies(mode):
        data = load_portfolio(strategy_name, mode)
        if not data:
            continue
        for t in data.get("trade_history", []):
            t.setdefault("strategy", strategy_name)
            t["_mode"] = mode
            all_trades.append(t)
    return all_trades
