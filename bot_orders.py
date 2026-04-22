"""
Bot Order Log
=============
Tracks every BUY / SELL action the bot takes (sim or live).

Stored in  trade_data/{mode}/orders.json  as a flat JSON list.

Order status lifecycle:
  PENDING   – Signal generated but market is closed; order queued for next open
  OPEN      – BUY placed/executed, position is currently held
  EXECUTED  – Both legs done; position fully closed with P&L
  CANCELLED – Manually force-closed by the user before normal exit
  REJECTED  – (live only) Order was rejected by the broker

  State transition:  PENDING → OPEN  (on next market open via promote_pending_orders)
                     OPEN    → EXECUTED | CANCELLED | REJECTED

Each record:
  order_id    – UUID (sim) or Kite order_id (live)
  symbol      – e.g. "RELIANCE.NS"
  side        – "BUY" | "SELL"  (entry vs exit of the position)
  quantity    – number of shares
  entry_price – price at which the BUY was executed
  exit_price  – price at which the SELL was executed (once closed)
  strategy    – strategy name
  mode        – "sim" | "live"
  status      – PENDING | OPEN | EXECUTED | CANCELLED | REJECTED
  placed_at   – ISO-8601 timestamp of the BUY signal
  opened_at   – ISO-8601 timestamp when PENDING → OPEN (None for same-day fills)
  closed_at   – ISO-8601 timestamp of the SELL (once done)
  exit_reason – "Stop Loss" | "Take Profit" | "Trailing Stop" |
                "Strategy Signal" | "Manual Cancel" | …
  pnl         – realised P&L in INR (once closed)
  kite_buy_id – Kite order_id for the buy leg  (live only)
  kite_sell_id– Kite order_id for the sell leg (live only)
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from market_utils import is_market_open

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent / "trade_data"


# ─────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────

def _orders_path(mode: str) -> Path:
    d = _BASE_DIR / mode
    d.mkdir(parents=True, exist_ok=True)
    return d / "orders.json"


def _load_raw(mode: str) -> List[dict]:
    path = _orders_path(mode)
    if not path.exists():
        return []
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception as exc:
        logger.error(f"bot_orders: load failed [{mode}]: {exc}")
        return []


def _save_raw(mode: str, orders: List[dict]) -> None:
    path = _orders_path(mode)
    try:
        with open(path, "w") as fh:
            json.dump(orders, fh, indent=2, default=str)
    except Exception as exc:
        logger.error(f"bot_orders: save failed [{mode}]: {exc}")


# ─────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────

def log_open(
    symbol: str,
    quantity: int,
    price: float,
    strategy: str,
    mode: str = "sim",
    kite_order_id: Optional[str] = None,
) -> str:
    """
    Record a new position opening (BUY signal received).

    Status is set automatically based on current market hours:
    - Market OPEN  → status "OPEN"   (order fills immediately)
    - Market CLOSED → status "PENDING" (queued until next market open)

    Returns the generated order_id.
    """
    orders = _load_raw(mode)
    oid    = kite_order_id or str(uuid.uuid4())[:12]
    status = "OPEN" if is_market_open() else "PENDING"
    orders.append({
        "order_id":     oid,
        "symbol":       symbol,
        "side":         "BUY",
        "quantity":     quantity,
        "entry_price":  round(price, 2),
        "exit_price":   None,
        "strategy":     strategy,
        "mode":         mode,
        "status":       status,
        "placed_at":    datetime.now().isoformat(),
        "opened_at":    datetime.now().isoformat() if status == "OPEN" else None,
        "closed_at":    None,
        "exit_reason":  None,
        "pnl":          None,
        "kite_buy_id":  kite_order_id,
        "kite_sell_id": None,
    })
    _save_raw(mode, orders)
    logger.debug(
        f"bot_orders: logged {status} {symbol} ×{quantity} @ ₹{price:.2f} [{mode}]"
    )
    return oid


def log_close(
    symbol: str,
    exit_price: float,
    exit_reason: str,
    pnl: float,
    mode: str = "sim",
    kite_sell_id: Optional[str] = None,
) -> None:
    """
    Mark the most-recent OPEN (or PENDING) order for *symbol* as EXECUTED.
    PENDING orders that are closed before ever opening are still recorded with full P&L.
    """
    orders = _load_raw(mode)
    for order in reversed(orders):
        if order["symbol"] == symbol and order["status"] in ("OPEN", "PENDING"):
            order["status"]       = "EXECUTED"
            order["exit_price"]   = round(exit_price, 2)
            order["closed_at"]    = datetime.now().isoformat()
            order["exit_reason"]  = exit_reason
            order["pnl"]          = round(pnl, 2)
            if kite_sell_id:
                order["kite_sell_id"] = kite_sell_id
            break
    _save_raw(mode, orders)
    logger.debug(f"bot_orders: logged CLOSE {symbol} @ ₹{exit_price:.2f} P&L ₹{pnl:+,.0f} [{mode}]")


def log_cancel(symbol: str, mode: str = "sim") -> None:
    """Mark the most-recent OPEN or PENDING order for *symbol* as CANCELLED."""
    orders = _load_raw(mode)
    for order in reversed(orders):
        if order["symbol"] == symbol and order["status"] in ("OPEN", "PENDING"):
            order["status"]      = "CANCELLED"
            order["closed_at"]   = datetime.now().isoformat()
            order["exit_reason"] = "Manual Cancel"
            break
    _save_raw(mode, orders)
    logger.info(f"bot_orders: logged CANCEL {symbol} [{mode}]")


def log_reject(
    symbol: str,
    quantity: int,
    price: float,
    strategy: str,
    reason: str,
    mode: str = "live",
) -> str:
    """
    Record a broker-REJECTED order (live mode only).

    Called when the Kite API raises an exception on place_order — e.g. due to
    insufficient margin, price-band breach, or a compliance rejection.
    The order is written immediately with status=REJECTED so the dashboard
    and audit trail reflect the failure without showing a ghost OPEN position.

    Returns the generated order_id.
    """
    orders = _load_raw(mode)
    oid = str(uuid.uuid4())[:12]
    orders.append({
        "order_id":     oid,
        "symbol":       symbol,
        "side":         "BUY",
        "quantity":     quantity,
        "entry_price":  round(price, 2),
        "exit_price":   None,
        "strategy":     strategy,
        "mode":         mode,
        "status":       "REJECTED",
        "placed_at":    datetime.now().isoformat(),
        "opened_at":    None,
        "closed_at":    datetime.now().isoformat(),
        "exit_reason":  reason,
        "pnl":          None,
        "kite_buy_id":  None,
        "kite_sell_id": None,
    })
    _save_raw(mode, orders)
    logger.warning(
        f"bot_orders: logged REJECTED {symbol} ×{quantity} @ ₹{price:.2f} "
        f"reason={reason!r} [{mode}]"
    )
    return oid


def promote_pending_orders(mode: str = "sim") -> int:
    """
    Transition all PENDING orders to OPEN now that the market has opened.

    Should be called once at the start of the first tick after market open.
    Returns the number of orders promoted.
    """
    if not is_market_open():
        return 0
    orders  = _load_raw(mode)
    count   = 0
    now_iso = datetime.now().isoformat()
    for order in orders:
        if order.get("status") == "PENDING":
            order["status"]    = "OPEN"
            order["opened_at"] = now_iso
            count += 1
    if count:
        _save_raw(mode, orders)
        logger.info(
            f"bot_orders: promoted {count} PENDING → OPEN [{mode}] at market open"
        )
    return count


def get_all_orders(mode: str = "sim") -> List[dict]:
    """Return all recorded orders for this mode (newest first)."""
    return list(reversed(_load_raw(mode)))


def delete_all_orders(mode: str = "sim") -> int:
    """Delete all saved orders for this mode.  Returns count removed."""
    path = _orders_path(mode)
    orders = _load_raw(mode)
    count = len(orders)
    if path.exists():
        path.unlink()
    logger.info(f"bot_orders: deleted {count} order(s) [{mode}]")
    return count


# ─────────────────────────────────────────────────────────
# Helpers used by the dashboard
# ─────────────────────────────────────────────────────────

def age_str(iso_ts: Optional[str]) -> str:
    """Convert an ISO timestamp to a human-readable age string ('5m', '2h 30m', '3d')."""
    if not iso_ts:
        return "—"
    try:
        placed = datetime.fromisoformat(iso_ts)
        delta  = datetime.now() - placed
        secs   = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s"
        elif secs < 3600:
            return f"{secs // 60}m"
        elif secs < 86400:
            h = secs // 3600
            m = (secs % 3600) // 60
            return f"{h}h {m}m" if m else f"{h}h"
        else:
            d = secs // 86400
            return f"{d}d"
    except Exception:
        return "—"


_STATUS_COLOUR = {
    "PENDING":   ("#5a3e00", "#fff8e1"),   # dark-amber text, pale-yellow bg
    "OPEN":      ("#1a4f8a", "#dce8f8"),   # blue text, light blue bg
    "EXECUTED":  ("#145c2e", "#e6f9ee"),   # green text, light green bg
    "CANCELLED": ("#7a4800", "#fff3d4"),   # amber text, light amber bg
    "REJECTED":  ("#8b1a1a", "#fde8e8"),   # red text, light red bg
}


def status_style(status: str) -> str:
    fg, bg = _STATUS_COLOUR.get(status, ("#1e2a3a", "#f0f4fa"))
    return f"color:{fg};background-color:{bg};font-weight:700;padding:2px 8px;border-radius:4px"
