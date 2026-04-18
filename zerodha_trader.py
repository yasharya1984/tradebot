"""
Zerodha Kite Live Trader
=========================
Handles real order execution via the Zerodha Kite Connect API.

⚠️  IMPORTANT BEFORE USING LIVE MODE:
  1. You need a Zerodha account with algo trading enabled.
  2. Apply for Kite Connect API at https://developers.kite.trade/
  3. API plan costs approx ₹500/month (Connect plan, as of 2025).
  4. Run `python zerodha_trader.py --generate-token` first each morning
     to get a fresh access token (valid for one trading day).
  5. SEBI requires a static IP and audit trail for algo trades.
     Set ALLOWED_IPS in config.py and run ip_guard.verify_ip_compliance()
     before starting live mode.

API surface used by this bot (yfinance handles all price data):
  ✅ place_order / cancel_order  — order execution
  ✅ orders / order_history      — order status
  ✅ positions                    — intraday holdings
  ✅ margins                      — account balance
  ❌ historical_data              — NOT used (yfinance covers this)
  ❌ quote / ltp / ohlc           — NOT used (yfinance covers this)

Rate-limit strategy:
  get_live_portfolio_status() caches its result for PORTFOLIO_CACHE_TTL_S seconds
  (default 10 s) to avoid hammering the REST API on every dashboard tick.
  LTP for open positions is streamed via KiteTickerManager (WebSocket) so the
  dashboard shows real-time prices without extra REST calls.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import webbrowser
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# How long (seconds) to cache the portfolio snapshot before re-fetching
PORTFOLIO_CACHE_TTL_S = 10


# ──────────────────────────────────────────────────────────
# KiteTickerManager — WebSocket LTP streaming
# ──────────────────────────────────────────────────────────

class KiteTickerManager:
    """
    Wraps KiteTicker (Zerodha WebSocket) to stream real-time LTP for
    a set of NSE instruments.

    Usage:
        tm = KiteTickerManager(kite_instance, api_key)
        tm.subscribe({"RELIANCE": 738561, "INFY": 408065})
        ltp = tm.get_ltp("RELIANCE")   # None until first tick arrives
        tm.stop()

    Why WebSocket instead of REST polling?
        kite.ltp() / kite.quote() are paid Full-Quotes endpoints and count
        against your API quota.  KiteTicker streams LTP via a persistent
        WebSocket connection at no extra per-call cost — only the subscription
        bandwidth is consumed.
    """

    def __init__(self, kite, api_key: str):
        self._kite    = kite
        self._api_key = api_key
        self._kticker = None
        self._ltps: Dict[str, float] = {}      # symbol → last price
        self._tokens: Dict[int, str] = {}      # token → symbol
        self._lock   = threading.Lock()
        self._running = False

    def subscribe(self, symbol_token_map: Dict[str, int]) -> None:
        """
        (Re-)subscribe to a set of instruments.  Starts the WebSocket if not running.

        Args:
            symbol_token_map: {"RELIANCE": 738561, "INFY": 408065, ...}
                              Instrument tokens can be fetched via kite.ltp() once,
                              or hardcoded from the NSE instrument dump.
        """
        try:
            from kiteconnect import KiteTicker
        except ImportError:
            logger.warning("KiteTickerManager: kiteconnect not installed — WebSocket LTP disabled.")
            return

        with self._lock:
            self._tokens = {v: k for k, v in symbol_token_map.items()}

        if self._kticker is None:
            self._kticker = KiteTicker(self._api_key, self._kite.access_token)
            self._kticker.on_ticks    = self._on_ticks
            self._kticker.on_connect  = self._on_connect
            self._kticker.on_error    = self._on_error
            self._kticker.on_close    = self._on_close
            self._running = True
            t = threading.Thread(
                target=self._kticker.connect,
                kwargs={"threaded": True},
                daemon=True,
                name="kite-ticker",
            )
            t.start()
            logger.info(
                f"KiteTickerManager: WebSocket started for "
                f"{len(symbol_token_map)} instrument(s)."
            )
        else:
            # Already running — just update the subscription
            tokens = list(symbol_token_map.values())
            self._kticker.subscribe(tokens)
            self._kticker.set_mode(self._kticker.MODE_LTP, tokens)

    def _on_connect(self, ws, response):
        tokens = list(self._tokens.keys())
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_LTP, tokens)
            logger.info(f"KiteTickerManager: subscribed {len(tokens)} token(s).")

    def _on_ticks(self, ws, ticks):
        with self._lock:
            for tick in ticks:
                symbol = self._tokens.get(tick["instrument_token"])
                if symbol and "last_price" in tick:
                    self._ltps[symbol] = tick["last_price"]

    def _on_error(self, ws, code, reason):
        logger.error(f"KiteTickerManager: WebSocket error {code}: {reason}")

    def _on_close(self, ws, code, reason):
        logger.warning(f"KiteTickerManager: WebSocket closed {code}: {reason}")
        self._running = False

    def get_ltp(self, symbol: str) -> Optional[float]:
        """Return the last streamed LTP for *symbol*, or None if not yet received."""
        with self._lock:
            return self._ltps.get(symbol)

    def get_all_ltps(self) -> Dict[str, float]:
        """Return a snapshot copy of all cached LTPs."""
        with self._lock:
            return dict(self._ltps)

    def stop(self):
        """Cleanly close the WebSocket connection."""
        if self._kticker and self._running:
            try:
                self._kticker.close()
            except Exception:
                pass
            self._running = False
            logger.info("KiteTickerManager: WebSocket stopped.")


# ──────────────────────────────────────────────────────────
# ZerodhaTrader
# ──────────────────────────────────────────────────────────

class ZerodhaTrader:
    """
    Wraps Kite Connect for live order execution.
    Gracefully degrades to simulation if kiteconnect is not installed.
    """

    def __init__(self, api_key: str, api_secret: str, access_token: str = ""):
        self.api_key      = api_key
        self.api_secret   = api_secret
        self.access_token = access_token
        self.kite         = None
        self._connected   = False
        # Portfolio cache
        self._portfolio_cache: Optional[dict] = None
        self._portfolio_cache_ts: float       = 0.0
        # WebSocket LTP manager (lazy-init on first subscribe call)
        self.ticker: Optional[KiteTickerManager] = None

    # ──────────────────────────────────────────
    # Connection & token management
    # ──────────────────────────────────────────

    def connect(self) -> bool:
        """Establish connection to Kite API."""
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            logger.error(
                "kiteconnect not installed. Run: pip install kiteconnect\n"
                "Then re-run the bot."
            )
            return False

        try:
            self.kite = KiteConnect(api_key=self.api_key)

            if self.access_token:
                self.kite.set_access_token(self.access_token)
                profile = self.kite.profile()
                logger.info(f"✅ Connected to Zerodha as: {profile['user_name']}")
                self._connected = True
                return True
            else:
                logger.warning("No access token. Call generate_access_token() first.")
                return False

        except Exception as e:
            logger.error(f"Kite connection failed: {e}")
            self._connected = False
            return False

    def _handle_kite_error(self, exc: Exception, context: str) -> None:
        """
        Central error handler for Kite API exceptions.

        Detects token expiry / unauthorised errors and marks the connection as
        broken so the dashboard shows the correct "Kite Disconnected" state
        instead of repeatedly failing on every tick.
        """
        err_str = str(exc).lower()
        # kiteconnect raises TokenException for expired / invalid tokens.
        # We also match on the string in case the exception class is not importable.
        token_keywords = ("token", "unauthorised", "unauthorized", "invalid session",
                          "access token", "tokenexception")
        if any(kw in err_str for kw in token_keywords):
            logger.error(
                f"[{context}] Kite session expired or token invalid: {exc}\n"
                "  → Run `python main.py token` to generate a fresh access token "
                "and restart the bot."
            )
            self._connected = False   # prevents further API calls this session
        else:
            logger.error(f"[{context}] Kite API error: {exc}")

    def _require_connected(self, context: str) -> bool:
        """Return True if connected; log and return False otherwise."""
        if not self.is_connected:
            logger.error(f"{context}: not connected to Kite.")
            return False
        return True

    def generate_access_token(self, request_token: str) -> str:
        """
        Exchange request token for access token.
        Call this ONCE per day after logging in via the Kite login URL.
        """
        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=self.api_key)
            data = kite.generate_session(request_token, api_secret=self.api_secret)
            access_token = data["access_token"]
            logger.info(f"✅ Access token generated. Add to config.py: {access_token}")
            return access_token
        except Exception as e:
            logger.error(f"Token generation failed: {e}")
            return ""

    def get_login_url(self) -> str:
        """Return the Zerodha login URL to generate a request token."""
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={self.api_key}"

    def open_login(self):
        """Open Kite login page in browser to get request token."""
        url = self.get_login_url()
        logger.info(f"Opening login URL: {url}")
        webbrowser.open(url)

    @property
    def is_connected(self) -> bool:
        return self._connected and self.kite is not None

    # ──────────────────────────────────────────
    # WebSocket LTP streaming
    # ──────────────────────────────────────────

    def start_ltp_stream(self, symbol_token_map: Dict[str, int]) -> None:
        """
        Start (or update) the WebSocket LTP stream for the given instruments.

        Call this after placing a buy order so the dashboard gets real-time
        P&L without extra REST calls.

        Args:
            symbol_token_map: {"RELIANCE": 738561, ...}
                Kite instrument tokens.  Fetch them once via:
                    instruments = kite.instruments("NSE")
                    token = next(i["instrument_token"] for i in instruments
                                 if i["tradingsymbol"] == "RELIANCE")
        """
        if not self.is_connected:
            return
        if self.ticker is None:
            self.ticker = KiteTickerManager(self.kite, self.api_key)
        self.ticker.subscribe(symbol_token_map)

    def get_ws_ltp(self, symbol: str) -> Optional[float]:
        """
        Return the WebSocket-streamed LTP for *symbol* (bare NSE name, no .NS).
        Returns None if the stream hasn't delivered a tick yet.
        """
        if self.ticker is None:
            return None
        return self.ticker.get_ltp(symbol)

    # ──────────────────────────────────────────
    # Order Placement
    # ──────────────────────────────────────────

    def place_buy_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str = "MARKET",
        price: Optional[float] = None,
    ) -> Optional[str]:
        """
        Place a buy order on NSE.

        Args:
            symbol:     NSE symbol WITHOUT .NS (e.g. "RELIANCE")
            quantity:   Number of shares
            order_type: "MARKET" or "LIMIT"
            price:      Required if order_type="LIMIT"

        Returns:
            order_id string if successful, None if failed
        """
        if not self._require_connected("place_buy_order"):
            return None

        try:
            params = {
                "tradingsymbol":    symbol,
                "exchange":         self.kite.EXCHANGE_NSE,
                "transaction_type": self.kite.TRANSACTION_TYPE_BUY,
                "quantity":         quantity,
                "order_type":       (
                    self.kite.ORDER_TYPE_MARKET
                    if order_type == "MARKET"
                    else self.kite.ORDER_TYPE_LIMIT
                ),
                "product":          self.kite.PRODUCT_MIS,
                "validity":         self.kite.VALIDITY_DAY,
            }
            if order_type == "LIMIT" and price:
                params["price"] = price

            order_id = self.kite.place_order(variety=self.kite.VARIETY_REGULAR, **params)
            logger.info(f"✅ BUY order placed: {symbol} × {quantity} | order_id={order_id}")
            return str(order_id)

        except Exception as e:
            self._handle_kite_error(e, f"place_buy_order({symbol})")
            return None

    def place_sell_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str = "MARKET",
        price: Optional[float] = None,
    ) -> Optional[str]:
        """Place a sell order."""
        if not self._require_connected("place_sell_order"):
            return None

        try:
            params = {
                "tradingsymbol":    symbol,
                "exchange":         self.kite.EXCHANGE_NSE,
                "transaction_type": self.kite.TRANSACTION_TYPE_SELL,
                "quantity":         quantity,
                "order_type":       (
                    self.kite.ORDER_TYPE_MARKET
                    if order_type == "MARKET"
                    else self.kite.ORDER_TYPE_LIMIT
                ),
                "product":          self.kite.PRODUCT_MIS,
                "validity":         self.kite.VALIDITY_DAY,
            }
            if order_type == "LIMIT" and price:
                params["price"] = price

            order_id = self.kite.place_order(variety=self.kite.VARIETY_REGULAR, **params)
            logger.info(f"✅ SELL order placed: {symbol} × {quantity} | order_id={order_id}")
            return str(order_id)

        except Exception as e:
            self._handle_kite_error(e, f"place_sell_order({symbol})")
            return None

    def place_stop_loss_order(
        self,
        symbol: str,
        quantity: int,
        trigger_price: float,
        limit_price: Optional[float] = None,
    ) -> Optional[str]:
        """Place a stop-loss market (SL-M) or stop-loss limit (SL) order."""
        if not self._require_connected("place_stop_loss_order"):
            return None

        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                tradingsymbol=symbol,
                exchange=self.kite.EXCHANGE_NSE,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_SL if limit_price else self.kite.ORDER_TYPE_SLM,
                product=self.kite.PRODUCT_MIS,
                validity=self.kite.VALIDITY_DAY,
                trigger_price=trigger_price,
                price=limit_price or trigger_price * 0.995,
            )
            logger.info(
                f"✅ Stop-loss order placed: {symbol} trigger=₹{trigger_price:.2f} | id={order_id}"
            )
            return str(order_id)
        except Exception as e:
            self._handle_kite_error(e, f"place_stop_loss_order({symbol})")
            return None

    # ──────────────────────────────────────────
    # Order Status (via REST with rate limiting)
    # ──────────────────────────────────────────

    def get_order_status(self, order_id: str) -> Optional[str]:
        """
        Return the current status string for a single order
        (e.g. "COMPLETE", "REJECTED", "OPEN", "CANCELLED").

        Uses order_history() — a lightweight per-order endpoint that does NOT
        count as a Full-Quotes or Historical Data API call.

        Note: for real-time push updates without polling, configure Kite's
        postback/webhook URL in your app settings on developers.kite.trade
        (requires a publicly accessible server endpoint).
        """
        if not self._require_connected("get_order_status"):
            return None
        try:
            history = self.kite.order_history(order_id)
            if history:
                return history[-1].get("status")
        except Exception as e:
            self._handle_kite_error(e, f"get_order_status({order_id})")
        return None

    # ──────────────────────────────────────────
    # Account / Portfolio Info
    # ──────────────────────────────────────────

    def get_account_balance(self) -> dict:
        """Fetch available cash balance from Zerodha account."""
        if not self._require_connected("get_account_balance"):
            return {}
        try:
            margins = self.kite.margins("equity")
            return {
                "available_cash": margins["available"]["live_balance"],
                "used_margin":    margins["utilised"]["debits"],
                "net":            margins["net"],
            }
        except Exception as e:
            self._handle_kite_error(e, "get_account_balance")
            return {}

    def get_positions(self) -> list:
        """Fetch current open intraday positions from Zerodha."""
        if not self._require_connected("get_positions"):
            return []
        try:
            pos = self.kite.positions()
            return pos.get("day", [])
        except Exception as e:
            self._handle_kite_error(e, "get_positions")
            return []

    def get_orders(self) -> list:
        """Fetch today's orders."""
        if not self._require_connected("get_orders"):
            return []
        try:
            return self.kite.orders()
        except Exception as e:
            self._handle_kite_error(e, "get_orders")
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a single pending order by its Kite order_id."""
        if not self._require_connected("cancel_order"):
            return False
        try:
            self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id)
            logger.info(f"cancel_order: cancelled {order_id}")
            return True
        except Exception as exc:
            self._handle_kite_error(exc, f"cancel_order({order_id})")
            return False

    def cancel_all_orders(self):
        """Cancel all pending orders (use at end of day)."""
        if not self._require_connected("cancel_all_orders"):
            return
        try:
            orders = self.kite.orders()
            for order in orders:
                if order["status"] in ["OPEN", "TRIGGER PENDING"]:
                    self.cancel_order(order["order_id"])
        except Exception as e:
            self._handle_kite_error(e, "cancel_all_orders")

    def get_live_portfolio_status(self) -> dict:
        """
        Fetch real-time account status from Kite:
          - available cash / margin
          - open intraday positions with live P&L
            (uses WebSocket LTP if available, otherwise falls back to REST field)
          - today's order list

        Result is cached for PORTFOLIO_CACHE_TTL_S seconds to avoid hammering
        the REST API on every dashboard tick.

        Returns a dict safe to display in the dashboard; never raises.
        """
        # ── TTL cache ──────────────────────────────────────────
        now = time.monotonic()
        if (
            self._portfolio_cache is not None
            and (now - self._portfolio_cache_ts) < PORTFOLIO_CACHE_TTL_S
        ):
            return self._portfolio_cache

        result: dict = {
            "connected":      self._connected,
            "available_cash": None,
            "used_margin":    None,
            "net_balance":    None,
            "positions":      [],
            "orders":         [],
            "error":          None,
        }

        if not self.is_connected:
            result["error"] = "Not connected to Kite"
            return result

        # ── Margins ────────────────────────────────────────────
        try:
            margins = self.kite.margins("equity")
            result["available_cash"] = margins["available"]["live_balance"]
            result["used_margin"]    = margins["utilised"]["debits"]
            result["net_balance"]    = margins["net"]
        except Exception as exc:
            self._handle_kite_error(exc, "get_live_portfolio_status/margins")
            result["error"] = str(exc)

        # ── Positions (use WebSocket LTP when available) ───────
        try:
            pos_data = self.kite.positions()
            ws_ltps  = self.ticker.get_all_ltps() if self.ticker else {}
            for p in pos_data.get("day", []):
                if p.get("quantity", 0) != 0:
                    sym = p["tradingsymbol"]
                    # Prefer WebSocket LTP → REST last_price → avg_price fallback
                    ltp = ws_ltps.get(sym) or p.get("last_price") or p["average_price"]
                    pnl = (ltp - p["average_price"]) * p["quantity"]
                    result["positions"].append({
                        "symbol":    sym,
                        "quantity":  p["quantity"],
                        "avg_price": p["average_price"],
                        "ltp":       ltp,
                        "pnl":       round(pnl, 2),
                        "product":   p.get("product", "MIS"),
                        "ltp_src":   "ws" if sym in ws_ltps else "rest",
                    })
        except Exception as exc:
            self._handle_kite_error(exc, "get_live_portfolio_status/positions")

        # ── Orders ─────────────────────────────────────────────
        try:
            result["orders"] = self.kite.orders() or []
        except Exception as exc:
            self._handle_kite_error(exc, "get_live_portfolio_status/orders")

        # ── Cache and return ────────────────────────────────────
        self._portfolio_cache    = result
        self._portfolio_cache_ts = now
        return result

    def invalidate_cache(self) -> None:
        """Force the next get_live_portfolio_status() call to skip the cache."""
        self._portfolio_cache    = None
        self._portfolio_cache_ts = 0.0

    def square_off_all(self):
        """
        Emergency: Square off all open intraday positions.
        Call this before 3:20 PM to avoid auto-square-off penalty.
        """
        if not self._require_connected("square_off_all"):
            return
        logger.warning("⚠️  SQUARING OFF ALL POSITIONS")
        positions = self.get_positions()
        for pos in positions:
            if pos["quantity"] != 0:
                symbol = pos["tradingsymbol"]
                qty    = abs(pos["quantity"])
                if pos["quantity"] > 0:
                    self.place_sell_order(symbol, qty)
                else:
                    self.place_buy_order(symbol, qty)


# ──────────────────────────────────────────────────────────
# CLI helpers
# ──────────────────────────────────────────────────────────

def setup_zerodha_token(api_key: str, api_secret: str, request_token: str) -> str:
    """Exchange request token for access token (CLI helper)."""
    trader = ZerodhaTrader(api_key, api_secret)
    token  = trader.generate_access_token(request_token)
    if token:
        print(f"\n✅ Your access token (valid for today):\n{token}")
        print(f"\nPaste this in config.py as ZERODHA_ACCESS_TOKEN = '{token}'")
    return token


if __name__ == "__main__":
    import sys
    from config import ZERODHA_API_KEY, ZERODHA_API_SECRET, ZERODHA_REQUEST_TOKEN

    if "--generate-token" in sys.argv:
        if not ZERODHA_REQUEST_TOKEN:
            trader    = ZerodhaTrader(ZERODHA_API_KEY, ZERODHA_API_SECRET)
            login_url = trader.get_login_url()
            print(f"\n1. Open this URL in your browser:\n   {login_url}")
            print("\n2. Log in with your Zerodha credentials")
            print("3. After redirect, copy the 'request_token' from the URL")
            print("4. Paste it in config.py as ZERODHA_REQUEST_TOKEN")
            print("5. Re-run: python zerodha_trader.py --generate-token")
        else:
            setup_zerodha_token(ZERODHA_API_KEY, ZERODHA_API_SECRET, ZERODHA_REQUEST_TOKEN)
