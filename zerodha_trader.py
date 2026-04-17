"""
Zerodha Kite Live Trader
=========================
Handles real order execution via the Zerodha Kite Connect API.

⚠️  IMPORTANT BEFORE USING LIVE MODE:
  1. You need a Zerodha account with algo trading enabled
  2. Apply for Kite Connect API at https://developers.kite.trade/
  3. API Key + Secret cost approx ₹2000/month
  4. Run `python zerodha_trader.py --generate-token` first each morning
     to get a fresh access token (valid for one trading day)
  5. SEBI requires audit trail for algo trades — this bot logs all orders
"""

import logging
import os
import webbrowser
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


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
            from kiteconnect import KiteConnect
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
            return False

    def generate_access_token(self, request_token: str) -> str:
        """
        Exchange request token for access token.
        Call this ONCE per day after logging in via the Kite login URL.

        Returns:
            access_token string (save this to config.py)
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
        if not self.is_connected:
            logger.error("Not connected to Kite. Cannot place order.")
            return None

        try:
            from kiteconnect import KiteConnect
            params = {
                "tradingsymbol": symbol,
                "exchange":      self.kite.EXCHANGE_NSE,
                "transaction_type": self.kite.TRANSACTION_TYPE_BUY,
                "quantity":      quantity,
                "order_type":    (
                    self.kite.ORDER_TYPE_MARKET
                    if order_type == "MARKET"
                    else self.kite.ORDER_TYPE_LIMIT
                ),
                "product":       self.kite.PRODUCT_MIS,   # MIS = intraday, CNC = delivery
                "validity":      self.kite.VALIDITY_DAY,
            }
            if order_type == "LIMIT" and price:
                params["price"] = price

            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                **params,
            )
            logger.info(f"✅ BUY order placed: {symbol} × {quantity} | order_id={order_id}")
            return str(order_id)

        except Exception as e:
            logger.error(f"❌ BUY order failed for {symbol}: {e}")
            return None

    def place_sell_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str = "MARKET",
        price: Optional[float] = None,
    ) -> Optional[str]:
        """Place a sell order."""
        if not self.is_connected:
            logger.error("Not connected to Kite. Cannot place order.")
            return None

        try:
            params = {
                "tradingsymbol": symbol,
                "exchange":      self.kite.EXCHANGE_NSE,
                "transaction_type": self.kite.TRANSACTION_TYPE_SELL,
                "quantity":      quantity,
                "order_type":    (
                    self.kite.ORDER_TYPE_MARKET
                    if order_type == "MARKET"
                    else self.kite.ORDER_TYPE_LIMIT
                ),
                "product":       self.kite.PRODUCT_MIS,
                "validity":      self.kite.VALIDITY_DAY,
            }
            if order_type == "LIMIT" and price:
                params["price"] = price

            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                **params,
            )
            logger.info(f"✅ SELL order placed: {symbol} × {quantity} | order_id={order_id}")
            return str(order_id)

        except Exception as e:
            logger.error(f"❌ SELL order failed for {symbol}: {e}")
            return None

    def place_stop_loss_order(
        self,
        symbol: str,
        quantity: int,
        trigger_price: float,
        limit_price: Optional[float] = None,
    ) -> Optional[str]:
        """Place a stop-loss order to protect a position."""
        if not self.is_connected:
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
            logger.info(f"✅ Stop-loss order placed: {symbol} trigger=₹{trigger_price:.2f} | id={order_id}")
            return str(order_id)
        except Exception as e:
            logger.error(f"❌ Stop-loss order failed for {symbol}: {e}")
            return None

    # ──────────────────────────────────────────
    # Account / Portfolio Info
    # ──────────────────────────────────────────

    def get_account_balance(self) -> dict:
        """Fetch available cash balance from Zerodha account."""
        if not self.is_connected:
            return {}
        try:
            margins = self.kite.margins("equity")
            return {
                "available_cash": margins["available"]["live_balance"],
                "used_margin":    margins["utilised"]["debits"],
                "net":            margins["net"],
            }
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
            return {}

    def get_positions(self) -> list:
        """Fetch current open positions from Zerodha."""
        if not self.is_connected:
            return []
        try:
            pos = self.kite.positions()
            return pos.get("day", [])  # Intraday positions
        except Exception as e:
            logger.error(f"Position fetch failed: {e}")
            return []

    def get_orders(self) -> list:
        """Fetch today's orders."""
        if not self.is_connected:
            return []
        try:
            return self.kite.orders()
        except Exception as e:
            logger.error(f"Order fetch failed: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a single pending order by its Kite order_id.

        Returns True if the cancel request was accepted.
        """
        if not self.is_connected:
            logger.error("cancel_order: not connected to Kite.")
            return False
        try:
            self.kite.cancel_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id,
            )
            logger.info(f"cancel_order: cancelled {order_id}")
            return True
        except Exception as exc:
            logger.error(f"cancel_order: failed for {order_id}: {exc}")
            return False

    def cancel_all_orders(self):
        """Cancel all pending orders (use at end of day)."""
        if not self.is_connected:
            return
        try:
            orders = self.kite.orders()
            for order in orders:
                if order["status"] in ["OPEN", "TRIGGER PENDING"]:
                    self.kite.cancel_order(
                        variety=self.kite.VARIETY_REGULAR,
                        order_id=order["order_id"],
                    )
                    logger.info(f"Cancelled order {order['order_id']}")
        except Exception as e:
            logger.error(f"Cancel orders failed: {e}")

    def get_live_portfolio_status(self) -> dict:
        """
        Fetch real-time account status from Kite:
          - available cash / margin
          - open intraday positions with live P&L
          - today's order list

        Returns a dict safe to display in the dashboard; never raises.
        """
        result = {
            "connected":        self._connected,
            "available_cash":   None,
            "used_margin":      None,
            "net_balance":      None,
            "positions":        [],    # list of position dicts
            "orders":           [],    # list of order dicts (today)
            "error":            None,
        }
        if not self.is_connected:
            result["error"] = "Not connected to Kite"
            return result
        try:
            margins = self.kite.margins("equity")
            result["available_cash"] = margins["available"]["live_balance"]
            result["used_margin"]    = margins["utilised"]["debits"]
            result["net_balance"]    = margins["net"]
        except Exception as exc:
            result["error"] = str(exc)

        try:
            pos_data = self.kite.positions()
            for p in pos_data.get("day", []):
                if p.get("quantity", 0) != 0:
                    result["positions"].append({
                        "symbol":     p["tradingsymbol"],
                        "quantity":   p["quantity"],
                        "avg_price":  p["average_price"],
                        "ltp":        p.get("last_price", p["average_price"]),
                        "pnl":        p.get("pnl", 0),
                        "product":    p.get("product", "MIS"),
                    })
        except Exception as exc:
            logger.error(f"get_live_portfolio_status: positions failed: {exc}")

        try:
            result["orders"] = self.kite.orders() or []
        except Exception as exc:
            logger.error(f"get_live_portfolio_status: orders failed: {exc}")

        return result

    def square_off_all(self):
        """
        Emergency: Square off all open intraday positions.
        Call this before 3:20 PM to avoid auto-square-off penalty.
        """
        if not self.is_connected:
            return
        logger.warning("⚠️  SQUARING OFF ALL POSITIONS")
        positions = self.get_positions()
        for pos in positions:
            if pos["quantity"] != 0:
                symbol = pos["tradingsymbol"]
                qty = abs(pos["quantity"])
                if pos["quantity"] > 0:
                    self.place_sell_order(symbol, qty)
                else:
                    self.place_buy_order(symbol, qty)


def setup_zerodha_token(api_key: str, api_secret: str, request_token: str) -> str:
    """
    Helper: Exchange request token for access token.
    Call this from command line after logging in to Kite.
    """
    trader = ZerodhaTrader(api_key, api_secret)
    token = trader.generate_access_token(request_token)
    if token:
        print(f"\n✅ Your access token (valid for today):\n{token}")
        print(f"\nPaste this in config.py as ZERODHA_ACCESS_TOKEN = '{token}'")
    return token


if __name__ == "__main__":
    import sys
    from config import ZERODHA_API_KEY, ZERODHA_API_SECRET, ZERODHA_REQUEST_TOKEN

    if "--generate-token" in sys.argv:
        if not ZERODHA_REQUEST_TOKEN:
            trader = ZerodhaTrader(ZERODHA_API_KEY, ZERODHA_API_SECRET)
            login_url = trader.get_login_url()
            print(f"\n1. Open this URL in your browser:\n   {login_url}")
            print("\n2. Log in with your Zerodha credentials")
            print("3. After redirect, copy the 'request_token' from the URL")
            print("4. Paste it in config.py as ZERODHA_REQUEST_TOKEN")
            print("5. Re-run: python zerodha_trader.py --generate-token")
        else:
            setup_zerodha_token(ZERODHA_API_KEY, ZERODHA_API_SECRET, ZERODHA_REQUEST_TOKEN)
