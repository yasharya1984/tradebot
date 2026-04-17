# Yash's Trading Bot

An automated NSE (National Stock Exchange) day-trading bot with a Streamlit dashboard. Supports paper (simulation) trading and live order execution via Zerodha Kite Connect. Built for personal use on a local machine.

---

## Features

- **Dual-Mode Trading** — Run Simulation and Live modes simultaneously. Switch views with a toggle in the sidebar; each mode has fully independent settings, capital, and trade history.
- **Paper Trading** — Simulate trades with virtual capital. All strategies run in parallel; positions and P&L tracked in real time.
- **Live Trading** — Place real orders via Zerodha Kite Connect with a configurable capital cap.
- **3 Trading Strategies** running in parallel:
  - `Moving Average Crossover` — Short/long MA crossover signals
  - `RSI + MACD` — Oversold/overbought RSI combined with MACD trend confirmation
  - `Momentum` — Price + volume momentum ranking
- **Stock Screener** — Ranks stocks across a 300-symbol NSE universe (100 Large, 100 Mid, 100 Small cap) by momentum score.
- **Backtester** — Runs any strategy over historical data and compares performance across strategies.
- **Risk Management** — Stop-loss, profit target, and trailing stop per trade; max position size and max open positions globally.
- **Streamlit Dashboard** — Live metrics, bot order log, portfolio status, screener results, backtest runner, and settings — all in one place.

---

## Quick Start

### 1. Install dependencies

```bash
pip install streamlit yfinance pandas ta plotly
# For live trading only:
pip install kiteconnect
```

### 2. Configure

Edit `config.py` to set your starting capital and risk parameters. No need to set `MODE` — the dashboard toggle handles that at runtime.

### 3. Launch the dashboard

```bash
python main.py dashboard
# Opens at http://localhost:8501
```

---

## Running from the Command Line

| Command | Description |
|---|---|
| `python main.py dashboard` | Launch the Streamlit web dashboard (recommended) |
| `python main.py paper` | Run a single paper-trading tick in the terminal |
| `python main.py paper --loop` | Run paper trading all day (9:15 AM–3:30 PM IST) |
| `python main.py backtest` | Compare all strategies over historical data |
| `python main.py screener` | Print top momentum stocks with current signals |
| `python main.py token` | Generate a Zerodha access token for live trading |

---

## Dashboard

### Navigation

The sidebar is always visible (fixed, no collapse). The top of the sidebar has a **📊 Sim / ⚡ Live** toggle that switches the entire dashboard between modes. Below it are the six navigation items:

| Page | Description |
|---|---|
| **Trading** | Start/stop the bot; live order table; portfolio status with per-strategy breakdown |
| **Overview** | Equity curve, cumulative P&L, win rate summary across strategies |
| **History** | Filterable trade history with export |
| **Screener** | Live momentum ranking of NSE 300 stocks with buy/sell signals |
| **Backtest** | Run strategies over a custom date range; compare total return, Sharpe, drawdown |
| **Settings** | Adjust all risk and strategy parameters (independent per mode) |

### URL-based navigation

Each page and mode is reflected in the browser URL (e.g. `?page=settings&mode=live`). Refreshing the browser returns you to the same page and mode. You can also open two browser tabs — one with `?mode=sim` and one with `?mode=live` — to monitor both modes side by side.

### Live clock

The sidebar and hero banner both show a live NSE market status (OPEN / PRE-OPEN / CLOSED), countdown to open/close, and current IST time with seconds — all updating every second client-side.

---

## Simulation vs Live Mode

Both modes run independently in parallel. Switching the toggle only changes which mode you are *viewing* — it does not stop or start either bot.

| | Simulation (📊) | Live (⚡) |
|---|---|---|
| Orders | Virtual (no real money) | Real orders via Zerodha Kite |
| Theme | Blue | Gold / Amber |
| Settings | Independent | Independent |
| Capital | Configurable virtual amount | Capped by `LIVE_TRADING_CAP` |
| History | Stored in `trade_data/sim/` | Stored in `trade_data/live/` |

---

## Configuration Reference (`config.py`)

### Capital & Risk

| Setting | Default | Description |
|---|---|---|
| `CAPITAL` | ₹1,00,000 | Starting virtual capital |
| `MAX_POSITION_PCT` | 20% | Max capital in any single stock |
| `MAX_OPEN_POSITIONS` | 10 | Max concurrent open positions |
| `STOP_LOSS_PCT` | 2% | Stop-loss per trade |
| `TARGET_PCT` | 4% | Profit target per trade |
| `TRAILING_STOP_PCT` | 1.5% | Trailing stop once in profit |
| `LIVE_TRADING_CAP` | ₹50,000 | Max real money deployed in live mode |

### Strategy Parameters

| Strategy | Key Settings |
|---|---|
| Moving Average | `MA_SHORT_PERIOD` (10), `MA_LONG_PERIOD` (30) |
| RSI + MACD | `RSI_PERIOD` (14), `RSI_OVERSOLD` (35), `RSI_OVERBOUGHT` (65) |
| Momentum | `MOMENTUM_LOOKBACK` (20 days), `MOMENTUM_VOLUME_LOOKBACK` (10 days) |

### Stock Universe

| Setting | Default |
|---|---|
| `TOP_N_STOCKS` | 60 (total stocks traded per session) |
| `TOP_N_PER_CAP` | 12 (minimum from each cap tier) |

Universe: 300 NSE symbols — 100 Large Cap, 100 Mid Cap, 100 Small Cap.

---

## Live Trading Setup (Zerodha Kite Connect)

> **Always run in simulation mode first before going live.**

1. Apply for a Kite Connect API key at https://developers.kite.trade/ (approx ₹2000/month)
2. Switch to ⚡ Live mode in the sidebar and go to **Settings → Zerodha API Credentials**. Enter your API key and secret there.
3. Each morning before market open, generate a fresh access token:
   ```bash
   python main.py token
   ```
   Follow the login URL printed, copy the `request_token` from the redirect URL, paste it into `config.py`, and re-run the command.
4. Toggle to ⚡ Live in the sidebar and start the bot from the Trading page.

> SEBI requires an audit trail for algo trades. This bot logs all orders to `trade_data/live/orders.json`.

---

## Project Structure

```
trading_bot/
├── dashboard.py          # Streamlit dashboard (main UI)
├── main.py               # CLI entry point
├── config.py             # All configuration settings
├── simulator.py          # Paper trading engine
├── zerodha_trader.py     # Zerodha Kite Connect wrapper
├── bot_orders.py         # Order log (read/write orders.json)
├── portfolio.py          # Portfolio state & P&L calculations
├── data_fetcher.py       # Yahoo Finance data fetcher
├── stock_selector.py     # Momentum-based stock screener
├── trade_store.py        # Persistent trade history
├── strategies/
│   ├── base.py           # Abstract strategy interface
│   ├── moving_average.py # MA crossover strategy
│   ├── rsi_macd.py       # RSI + MACD strategy
│   └── momentum.py       # Momentum strategy
└── trade_data/
    ├── sim/orders.json   # Paper trade order log
    └── live/orders.json  # Live trade order log
```

---

## Disclaimer

This bot is for personal educational use only. Trading stocks involves significant financial risk. Always test thoroughly in simulation mode before using real capital. The author is not responsible for any financial losses.

NSE market hours: 9:15 AM – 3:30 PM IST, Monday–Friday (excluding exchange holidays).

---

© 2025 Yash Arya. Personal use only.
