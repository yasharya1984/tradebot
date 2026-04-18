"""
Trading Bot — Main Entry Point
================================
Run modes:
  python main.py dashboard       → Launch Streamlit web dashboard (recommended)
  python main.py backtest        → Run strategy comparison in terminal
  python main.py screener        → Print top momentum stocks
  python main.py paper           → Run one paper-trading tick
  python main.py paper --loop    → Run paper trading all day (9:15 AM–3:30 PM IST)
  python main.py token           → Generate Zerodha access token
"""

import sys
import logging
import subprocess
import time
from datetime import datetime, timezone, timedelta

import config
from data_fetcher import DataFetcher
from ip_guard import verify_ip_compliance, log_ip_once, start_ip_heartbeat
from market_utils import is_market_open as _market_is_open_util
from stock_selector import StockSelector
from simulator import Simulator
from strategies import STRATEGY_MAP
from execution import build_broker

# ─────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, mode="a"),
    ],
)
logger = logging.getLogger("main")


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════╗
║         🇮🇳  India NSE Day Trading Bot                   ║
║         Mode: {:<10}  Capital: ₹{:>10,.0f}          ║
║         {:<50} ║
╚══════════════════════════════════════════════════════════╝
""".format(
        config.MODE.upper(),
        config.CAPITAL,
        datetime.now().strftime("%A, %d %B %Y  %H:%M IST"),
    ))


def cmd_dashboard():
    """Launch the Streamlit dashboard."""
    print_banner()
    print("🌐 Launching dashboard at http://localhost:8501 ...")
    #subprocess.run(["streamlit", "run", "dashboard.py", "--server.headless", "false"])
    subprocess.run([sys.executable, "-m", "streamlit", "run", "dashboard.py", "--server.headless", "false"])

def cmd_backtest():
    """Run full strategy comparison backtest in terminal."""
    print_banner()
    logger.info("Running full strategy comparison backtest...")

    fetcher   = DataFetcher()
    simulator = Simulator(fetcher)

    comparison = simulator.run_full_comparison(config.BACKTEST_PERIOD_DAYS)

    print("\n" + "=" * 70)
    print("BACKTEST RESULTS — Strategy Comparison")
    print("=" * 70)
    if not comparison.empty:
        print(comparison.to_string(index=False))
        best = comparison.iloc[0]
        print(f"\n🏆 Best Strategy: {best['Strategy']} with {best['Total Return (%)']:.2f}% return")
    else:
        print("No results — check if stocks have data.")


def cmd_screener():
    """Print top momentum stocks."""
    print_banner()
    logger.info("Running Nifty 100 momentum screener...")

    fetcher   = DataFetcher()
    selector  = StockSelector(fetcher)
    selected, summary = selector.refresh_selection()

    print("\n" + "=" * 70)
    print(f"TOP {len(selected)} MOMENTUM STOCKS — Nifty 100")
    print("=" * 70)
    print(summary.to_string(index=False))

    print("\n── Current Signals ──")
    for strat_name, StratClass in STRATEGY_MAP.items():
        strategy = StratClass()
        print(f"\n{strategy.name}:")
        for stock in selected:
            df = stock.get("data")
            if df is not None and not df.empty:
                sig = strategy.get_current_signal(df)
                emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(sig, "")
                print(f"  {stock['symbol']:20s} {emoji} {sig}")


IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN  = (9, 15)   # 9:15 AM IST
MARKET_CLOSE = (15, 30)  # 3:30 PM IST


def _market_is_open() -> bool:
    """Return True if NSE market is currently open (weekdays, non-holidays, 9:15–15:30 IST)."""
    return _market_is_open_util()


def _run_one_tick(simulator: "Simulator") -> None:
    for strat_name in STRATEGY_MAP:
        result = simulator.paper_trading_tick(strat_name)
        p = result["portfolio"]
        print(f"\n── {STRATEGY_MAP[strat_name].name} ──")
        print(f"  Cash: ₹{p['cash']:,.2f} | Equity: ₹{p['equity']:,.2f} | P&L: ₹{p['pnl']:+,.2f} ({p['pnl_pct']:+.2f}%)")
        for sym, info in result["signals"].items():
            emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(info["signal"], "")
            print(f"  {sym:20s} {emoji} {info['signal']:6s} @ ₹{info['price']:,.2f}")


def _build_broker_for_mode():
    """
    Build the execution broker matching config.MODE.

    - "simulation" → SimBroker (paper trading, no real orders)
    - "live"       → LiveBroker (real Zerodha orders)

    For live mode the ZerodhaTrader is connected here; if connection fails
    the function exits with a clear error rather than silently paper-trading.
    """
    mode = config.MODE.lower()

    if mode == "live":
        # ── SEBI Static-IP compliance check ───────────────────
        verify_ip_compliance()
        # Start hourly IP heartbeat for audit trail
        start_ip_heartbeat(interval_s=3600)

        from zerodha_trader import ZerodhaTrader
        if not config.ZERODHA_ACCESS_TOKEN:
            logger.error(
                "Live mode requires ZERODHA_ACCESS_TOKEN in config.py. "
                "Run: python main.py token"
            )
            sys.exit(1)

        trader = ZerodhaTrader(
            config.ZERODHA_API_KEY,
            config.ZERODHA_API_SECRET,
            config.ZERODHA_ACCESS_TOKEN,
        )
        connected = trader.connect()
        if not connected:
            logger.error(
                "Could not connect to Zerodha Kite. "
                "Check your API key and access token."
            )
            sys.exit(1)

        logger.info("Live mode: Zerodha connected. Building LiveBroker.")
        return "live", trader, build_broker("live", trader)

    # Default: simulation
    return "sim", None, build_broker("sim")


def cmd_paper():
    """
    Run trading loop (simulation or live) — one tick, or all day with --loop.

    Mode is determined by config.MODE:
      "simulation" → paper trading (SimBroker, no real orders)
      "live"       → real Zerodha orders (LiveBroker)
    """
    print_banner()

    mode_str, trader, broker = _build_broker_for_mode()
    trade_mode = "live" if mode_str == "live" else "sim"

    fetcher   = DataFetcher()
    simulator = Simulator(fetcher)
    simulator.initialize_paper_trading(mode=trade_mode, broker=broker)

    # For live mode, sync any existing Kite positions before the first tick
    if trade_mode == "live" and trader is not None:
        for strat_name in STRATEGY_MAP:
            result = simulator.sync_live_portfolio(strat_name, trader)
            if result["positions_loaded"]:
                logger.info(
                    f"Synced {result['positions_loaded']} live position(s) "
                    f"into [{strat_name}]"
                )

    loop_mode = "--loop" in sys.argv

    if not loop_mode:
        logger.info(f"Running single {trade_mode} tick...")
        _run_one_tick(simulator)
        return

    # ── Full-day loop ──────────────────────────────────────
    interval = config.DASHBOARD_REFRESH_SECONDS
    logger.info(
        f"Trading loop started [{trade_mode.upper()}] "
        f"(interval: {interval}s). Press Ctrl+C to stop."
    )
    tick = 0
    try:
        while True:
            now_ist = datetime.now(IST)
            if not _market_is_open():
                open_str  = f"{MARKET_OPEN[0]:02d}:{MARKET_OPEN[1]:02d}"
                close_str = f"{MARKET_CLOSE[0]:02d}:{MARKET_CLOSE[1]:02d}"
                print(f"\n[{now_ist.strftime('%H:%M:%S')} IST] Market closed "
                      f"(hours: {open_str}–{close_str} IST). Waiting...")
                time.sleep(60)
                continue

            # Market is open — promote any orders queued during closure
            import bot_orders as _bo
            for _m in ("sim", "live"):
                _n = _bo.promote_pending_orders(_m)
                if _n:
                    print(f"  → Promoted {_n} PENDING order(s) to OPEN [{_m}]")

            tick += 1
            print(f"\n{'='*60}")
            print(
                f" Tick #{tick}  [{trade_mode.upper()}]  |  "
                f"{now_ist.strftime('%d %b %Y  %H:%M:%S')} IST"
            )
            print(f"{'='*60}")
            _run_one_tick(simulator)
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\nStopped by user. Final {trade_mode} portfolio state:")
        _run_one_tick(simulator)


def cmd_token():
    """Generate Zerodha access token."""
    from zerodha_trader import ZerodhaTrader
    trader = ZerodhaTrader(config.ZERODHA_API_KEY, config.ZERODHA_API_SECRET)
    if not config.ZERODHA_REQUEST_TOKEN:
        url = trader.get_login_url()
        print(f"\n1. Open this login URL:\n   {url}")
        print("\n2. After login, copy the 'request_token' from the browser URL")
        print("3. Paste it in config.py → ZERODHA_REQUEST_TOKEN")
        print("4. Re-run: python main.py token")
    else:
        token = trader.generate_access_token(config.ZERODHA_REQUEST_TOKEN)
        if token:
            print(f"\n✅ Access token: {token}")
            print(f"   Paste into config.py → ZERODHA_ACCESS_TOKEN")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
COMMANDS = {
    "dashboard": cmd_dashboard,
    "backtest":  cmd_backtest,
    "screener":  cmd_screener,
    "paper":     cmd_paper,
    "token":     cmd_token,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dashboard"

    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[cmd]()
