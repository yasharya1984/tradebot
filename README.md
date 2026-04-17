# Yash's Trading Bot

An automated NSE (National Stock Exchange) day-trading bot with a Streamlit dashboard. Supports paper (simulation) trading and live order execution via Zerodha Kite Connect. Built for personal use on a local machine.

---

## Features

- **Dual-Mode Trading** — Run Simulation and Live modes simultaneously. Switch views with a toggle in the sidebar; each mode has fully independent settings, capital, and trade history.
- **Paper Trading** — Simulate trades with virtual capital. All strategies run in parallel; positions and P&L tracked in real time. **Auto-starts on page load** if saved positions exist from a previous session.
- **Live Trading** — Place real orders via Zerodha Kite Connect with a configurable capital cap.
- **3 Trading Strategies** running in parallel:
  - `Moving Average Crossover` — Short/long MA crossover signals
  - `RSI + MACD` — Oversold/overbought RSI combined with MACD trend confirmation
  - `Momentum` — Price + volume momentum ranking
- **Dynamic Exit Strategy** — Replaces static targets with a multi-phase trailing system (see below).
- **Gap-Down Protection** — Timed exit logic that gives stocks a chance to recover before closing a position at the open.
- **Position Sizing Guards** — Minimum ₹5,000 per position; minimum ₹200 P&L before booking a discretionary exit.
- **Stock Screener** — Ranks stocks across a 300-symbol NSE universe (100 Large, 100 Mid, 100 Small cap) by momentum score.
- **Backtester** — Runs any strategy over historical data and compares performance across strategies.
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

The bot **auto-starts** immediately if saved portfolios are found from a previous session. All open positions continue to be managed without pressing ▶️ Start.

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
| **Trading** | Start/stop the bot; live order table with exit-state indicators; portfolio status with per-strategy breakdown |
| **Overview** | Equity curve, cumulative P&L, win rate summary across strategies |
| **History** | Filterable trade history with export; exit reasons colour-coded by type |
| **Screener** | Live momentum ranking of NSE 300 stocks with buy/sell signals |
| **Backtest** | Run strategies over a custom date range; compare total return, Sharpe, drawdown |
| **Settings** | Adjust all risk, dynamic exit, and strategy parameters (independent per mode) |

### Auto-start behaviour

When the dashboard loads, it checks for saved portfolio files. If found, the bot resumes automatically — existing open positions continue to be evaluated for exits on every tick. No manual ▶️ Start required after a restart.

### Exit State column (Trading page)

The **Exit State** column in the Bot Orders table shows real-time progress of the dynamic exit strategy for each open position:

| Value | Meaning |
|---|---|
| `SL only` | In initial phase — only the 2% hard stop is active |
| `BE ₹{price}` | Break-even triggered — SL has moved to entry price |
| `TSL ₹{price}` | Trailing stop is active at the shown level |
| `⏳ Gap Watch` | Opening gap detected — waiting for 9:30 AM candle close |

Executed orders are colour-coded by exit reason:
- 🟢 Green — `Trailing Stop` (profitable TSL exit)
- 🔴 Red — `Stop Loss` or `Gap Down Exit` (capital protection)
- 🟣 Purple — `EMA Exit` (trend-based filter)
- 🔵 Blue — `Strategy Signal`

### Connectivity Alert

A non-intrusive connectivity watchdog runs in the browser at all times. If the internet connection is lost it displays a fixed banner in the **bottom-right corner** of the screen:

> ⚠️ **Connectivity Issue** — Check your internet connection

The banner disappears automatically as soon as the connection is restored. The check uses two mechanisms:
1. **`navigator.onLine`** — fires instantly on `offline`/`online` browser events.
2. **Fetch probe** — every 10 seconds, sends a `HEAD` request to `google.com/generate_204` with a 4-second timeout to catch cases where the browser reports online but packets are not routing (e.g. VPN drop, captive portal).

No action is needed to enable this — it starts automatically when the dashboard loads.

### URL-based navigation

Each page and mode is reflected in the browser URL (e.g. `?page=settings&mode=live`). Refreshing the browser returns you to the same page and mode.

---

## Dynamic Exit Strategy (Equity Module)

Replaces the old static 4% target with a multi-phase trailing system:

### Phase 1 — Break-Even (at +1.5% profit)
Once the trade reaches 1.5% profit, the Stop Loss is automatically moved to the entry price. This locks in a zero-loss floor.

### Phase 2 — Trailing Stop (at +2% profit)
Once profit exceeds 2%, a Trailing Stop (TSL) activates. It follows the **Highest High** reached since entry at a **1.5% distance**, and only ever moves up — never down.

### EMA Filter
At any time, if the price closes below the **20-period EMA on the 15-minute chart**, the position is exited immediately (subject to the ₹200 minimum P&L guard for small trades).

### Gap-Down Timed Exit
If a stock opens below the Stop Loss:
1. **Do not exit at 9:15 AM.** Start watching.
2. Wait for the first **15-minute candle** to close (by 9:30 AM).
3. If the candle is **green** (recovery): hold the position and set a new SL at the opening candle's Low.
4. If the candle is **red** or the price is still below SL after 9:30 AM: exit immediately.

### Position Sizing Guards
- **Minimum ₹5,000 per symbol** — the bot will not buy a position worth less than ₹5,000.
- **Minimum ₹200 P&L to book** — discretionary exits (Strategy Signal, EMA Filter) are skipped if the absolute P&L is below ₹200, avoiding noise transactions. Risk-management exits (Stop Loss, Trailing Stop, Gap Down) always execute regardless.

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
| `LIVE_TRADING_CAP` | ₹50,000 | Max real money deployed in live mode |

### Dynamic Exit Strategy

| Setting | Default | Description |
|---|---|---|
| `STOP_LOSS_PCT` | 2% | Initial hard stop-loss from entry |
| `BREAKEVEN_TRIGGER_PCT` | 1.5% | Move SL to entry once profit ≥ this |
| `TSL_ACTIVATION_PCT` | 2% | Activate trailing stop once profit ≥ this |
| `TRAILING_STOP_PCT` | 1.5% | TSL distance from the Highest High |
| `EMA_PERIOD` | 20 | EMA period for secondary exit filter |
| `EMA_TIMEFRAME` | 15m | Candle timeframe for EMA calculation |
| `MIN_POSITION_VALUE` | ₹5,000 | Minimum position size per symbol |
| `MIN_PNL_TO_BOOK` | ₹200 | Min P&L for discretionary (signal/EMA) exits |

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
├── simulator.py          # Paper trading + backtest engine
├── zerodha_trader.py     # Zerodha Kite Connect wrapper
├── bot_orders.py         # Order log (read/write orders.json)
├── portfolio.py          # Portfolio state, dynamic exit logic
├── data_fetcher.py       # Yahoo Finance / Kite data fetcher
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
