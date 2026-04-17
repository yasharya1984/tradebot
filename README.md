# Yash's Trading Bot

An automated NSE (National Stock Exchange) day-trading bot with a Streamlit dashboard. Supports paper (simulation) trading and live order execution via Zerodha Kite Connect. Built for personal use on a local machine.

---

## Features

- **Paper Trading** — Simulate trades with virtual capital. All strategies run simultaneously; positions and P&L are tracked in real time.
- **Live Trading** — Place real orders via Zerodha Kite Connect with a configurable capital cap.
- **3 Trading Strategies** running in parallel:
  - `Moving Average Crossover` — Short/long MA crossover signals
  - `RSI + MACD` — Oversold/overbought RSI combined with MACD trend confirmation
  - `Momentum` — Price + volume momentum ranking
- **Stock Screener** — Ranks stocks across a 300-symbol NSE universe (100 Large, 100 Mid, 100 Small cap) by momentum score.
- **Backtester** — Runs any strategy over historical data and compares performance across strategies.
- **Risk Management** — Stop-loss, profit target, and trailing stop per trade; max position size and max open positions globally.
- **Streamlit Dashboard** — Live metrics, bot order log, portfolio status, screener results, backtest runner, and settings — all in one page.

---

## Quick Start

### 1. Install dependencies

```bash
pip install streamlit yfinance pandas ta
# For live trading only:
pip install kiteconnect
```

### 2. Configure

Edit `config.py`:

```python
CAPITAL = 100_000          # Starting capital in INR
MODE    = "simulation"     # "simulation" or "live"
```

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

## Dashboard Pages

| Page | Description |
|---|---|
| **Trading** | Start/pause/restart the bot; live order table; portfolio status with per-strategy breakdown |
| **Overview** | Equity curve, cumulative P&L, win rate summary across strategies |
| **History** | Filterable trade history with export |
| **Screener** | Live momentum ranking of NSE stocks with buy/sell signals |
| **Backtest** | Run strategies over a custom date range; compare total return, Sharpe, drawdown |
| **Settings** | Adjust all risk and strategy parameters without restarting |

The sidebar shows market open/close countdown and bot running time. Mode (Simulator / Live) is a sub-menu under Trading.

---

## Configuration Reference (`config.py`)

### Capital & Risk

| Setting | Default | Description |
|---|---|---|
| `CAPITAL` | ₹1,00,000 | Starting virtual capital |
| `MAX_POSITION_PCT` | 20% | Max capital in any single stock |
| `MAX_OPEN_POSITIONS` | 10 | Max concurrent positions |
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
2. Add your credentials to `config.py`:
   ```python
   ZERODHA_API_KEY    = "your_api_key"
   ZERODHA_API_SECRET = "your_api_secret"
   ```
3. Each morning before market open, generate a fresh access token:
   ```bash
   python main.py token
   ```
   Follow the login URL printed, copy the `request_token` from the redirect URL, paste it into `config.py`, and re-run the command.
4. Set `MODE = "live"` in `config.py` or toggle Live in the dashboard sidebar.

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

© 2024 Yash Arya. Personal use only.
