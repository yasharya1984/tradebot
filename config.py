"""
Trading Bot Configuration
=========================
All sensitive values are read from environment variables first, then fall
back to the hardcoded defaults below. In production (Docker / Lightsail)
set everything via docker-compose.yml → .env file; never commit secrets.

Priority: environment variable > value below.
"""

import os

# ─────────────────────────────────────────────
# CAPITAL & RISK SETTINGS
# ─────────────────────────────────────────────
CAPITAL = 100_000            # Starting capital in INR
MAX_POSITION_PCT = 0.20      # Max 20% of capital in any single stock
MAX_OPEN_POSITIONS = 10       # Max concurrent open positions
STOP_LOSS_PCT = 0.02         # 2% initial stop-loss per trade
TARGET_PCT = 0.04            # Legacy — static target no longer used; TSL handles exits
TRAILING_STOP_PCT = 0.015    # 1.5% trailing stop distance from Highest High

# ─────────────────────────────────────────────
# DYNAMIC EXIT STRATEGY (Equity Module)
# ─────────────────────────────────────────────
BREAKEVEN_TRIGGER_PCT = 0.015   # Move SL to entry once profit >= 1.5%
TSL_ACTIVATION_PCT    = 0.02    # Activate trailing stop once profit >= 2%
EMA_PERIOD            = 20      # EMA period for secondary exit filter
EMA_TIMEFRAME         = "15m"   # Candle timeframe for EMA exit filter

# ─────────────────────────────────────────────
# POSITION SIZING GUARDS
# ─────────────────────────────────────────────
MIN_POSITION_VALUE = 5_000   # Minimum ₹5,000 invested per symbol
MIN_PNL_TO_BOOK    = 200     # Minimum ₹200 P&L to execute a signal exit

# ─────────────────────────────────────────────
# ZERODHA KITE API CREDENTIALS
# Get yours at: https://developers.kite.trade/
# Cost: ₹500/month (Connect plan as of 2025)
# Price data is sourced from yfinance (free) —
#   the Kite API is used ONLY for:
#     • place_order / cancel_order
#     • order_history / orders
#     • positions
#     • margins (account balance)
# No Historical Data or Full Quotes API calls are made.
# ─────────────────────────────────────────────
ZERODHA_API_KEY      = os.environ.get("ZERODHA_API_KEY",      "your_api_key_here")
ZERODHA_API_SECRET   = os.environ.get("ZERODHA_API_SECRET",   "your_api_secret_here")
ZERODHA_ACCESS_TOKEN = os.environ.get("ZERODHA_ACCESS_TOKEN", "")   # Generated daily via login flow
ZERODHA_REQUEST_TOKEN= os.environ.get("ZERODHA_REQUEST_TOKEN","")   # Paste after OAuth redirect

# ─────────────────────────────────────────────
# SEBI STATIC-IP COMPLIANCE (April 2026 mandate)
# SEBI requires algo-trading bots to run from a
# whitelisted static IP registered with your broker.
#
# Add every authorised IP below.  An empty list means
# the check is skipped (useful for simulation mode).
# Typical setup: one home static IP + one VPS IP.
#
# Example:
#   ALLOWED_IPS = ["203.0.113.10", "198.51.100.42"]
# ─────────────────────────────────────────────
# Read from env var as comma-separated string, e.g. "203.0.113.10,198.51.100.42"
_allowed_ips_env = os.environ.get("ALLOWED_IPS", "")
ALLOWED_IPS: list = [ip.strip() for ip in _allowed_ips_env.split(",") if ip.strip()]

# ─────────────────────────────────────────────
# TRADING MODE
# "simulation"  → paper trading (no real money)
# "live"        → real orders via Zerodha Kite
#
# Switching MODE here is the ONLY change needed to go from sim to live.
# The algorithm (entry/exit/TSL/P&L logic) is identical in both modes.
# The execution.py Broker adapter handles the difference:
#   SimBroker  → writes to trade_data/sim/
#   LiveBroker → calls Kite API + writes to trade_data/live/
# ─────────────────────────────────────────────
MODE = os.environ.get("TRADING_MODE", "simulation")

# ─────────────────────────────────────────────
# LIVE TRADING CAP
# Maximum real money the bot is allowed to deploy
# even if your Zerodha account has more funds.
# ─────────────────────────────────────────────
LIVE_TRADING_CAP = 50_000    # INR — change to your desired cap

# ─────────────────────────────────────────────
# STRATEGY SELECTION
# "ma"             → Moving Average Crossover
# "rsi_macd"       → RSI + MACD
# "momentum"       → Momentum
# "trend_strength" → Trend-Strength (RS + Volume + ADX)  ← NEW
# "all"            → Run all 4 in parallel & compare (simulation only)
# ─────────────────────────────────────────────
ACTIVE_STRATEGY = "all"

# ─────────────────────────────────────────────
# MOVING AVERAGE STRATEGY PARAMS
# ─────────────────────────────────────────────
MA_SHORT_PERIOD = 10        # Short-term MA period (days)
MA_LONG_PERIOD  = 30        # Long-term MA period (days)

# ─────────────────────────────────────────────
# RSI + MACD STRATEGY PARAMS
# ─────────────────────────────────────────────
RSI_PERIOD       = 14
RSI_OVERSOLD     = 35       # Buy signal below this
RSI_OVERBOUGHT   = 65       # Sell signal above this
MACD_FAST        = 12
MACD_SLOW        = 26
MACD_SIGNAL      = 9

# ─────────────────────────────────────────────
# MOMENTUM STRATEGY PARAMS
# ─────────────────────────────────────────────
MOMENTUM_LOOKBACK = 20      # Days to calculate momentum
MOMENTUM_VOLUME_LOOKBACK = 10

# ─────────────────────────────────────────────
# STOCK SELECTION (Nifty 100 universe)
# ─────────────────────────────────────────────
TOP_N_STOCKS = 60           # Total top stocks to trade per session
TOP_N_PER_CAP = 12          # Minimum stocks from each cap tier (remaining filled by best score)

# ─────────────────────────────────────────────
# RELATIVE STRENGTH (RS) SCANNER FILTERS
# Applied before momentum scoring to keep only
# institutionally-trending stocks.
# ─────────────────────────────────────────────
RS_SMA_SHORT         = 50    # Price must be above 50-day SMA
RS_SMA_LONG          = 200   # Price must be above 200-day SMA
RS_VOLUME_LOOKBACK   = 20    # Days for average volume baseline
RS_VOLUME_MULTIPLIER = 1.5   # Current volume must be >= 1.5× 20-day avg
RS_ADX_PERIOD        = 14    # ADX period (Wilder's smoothing)
RS_ADX_MIN           = 25    # Minimum ADX to qualify as a trending stock

# ─────────────────────────────────────────────
# NSE STOCK UNIVERSE — 300 stocks
# 100 Large Cap | 100 Mid Cap | 100 Small Cap
# ─────────────────────────────────────────────

LARGE_CAP_SYMBOLS = [
    # Financials
    "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS",
    "BAJFINANCE.NS", "INDUSINDBK.NS", "BAJAJFINSV.NS", "CHOLAFIN.NS", "MUTHOOTFIN.NS",
    "SBILIFE.NS", "HDFCLIFE.NS", "ICICIPRULI.NS", "FEDERALBNK.NS", "IDFCFIRSTB.NS",
    "BANDHANBNK.NS",
    # IT / Tech
    "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS",
    "MPHASIS.NS", "LTIM.NS", "COFORGE.NS", "PERSISTENT.NS",
    # Energy & Utilities
    "RELIANCE.NS", "ONGC.NS", "BPCL.NS", "IOC.NS", "HINDPETRO.NS",
    "GAIL.NS", "PETRONET.NS", "NTPC.NS", "POWERGRID.NS", "COALINDIA.NS",
    "TATAPOWER.NS",
    # Industrials & Defence
    "LT.NS", "SIEMENS.NS", "ABB.NS", "CUMMINSIND.NS", "THERMAX.NS",
    "HAL.NS", "BEL.NS", "BHEL.NS",
    # Metals & Mining
    "TATASTEEL.NS", "JSWSTEEL.NS", "SAIL.NS", "NMDC.NS", "ADANIENT.NS",
    "HINDALCO.NS", "VEDL.NS",
    # Consumer / FMCG
    "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS",
    "MARICO.NS", "TATACONSUM.NS", "UNITDSPR.NS",
    # Automobiles
    "MARUTI.NS", "TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS",
    "EICHERMOT.NS",
    # Pharma & Healthcare
    "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "LUPIN.NS",
    "TORNTPHARM.NS", "AUROPHARMA.NS", "BIOCON.NS", "ALKEM.NS", "GLAND.NS",
    "APOLLOHOSP.NS",
    # Paints & Chemicals
    "ASIANPAINT.NS", "PIDILITIND.NS", "BERGEPAINT.NS", "UPL.NS",
    # Cement
    "ULTRACEMCO.NS", "GRASIM.NS", "SHREECEM.NS", "AMBUJACEM.NS", "ACC.NS",
    # Consumer Durables / Retail
    "TITAN.NS", "HAVELLS.NS", "VOLTAS.NS", "DMART.NS", "TRENT.NS",
    "JUBLFOOD.NS",
    # Real Estate
    "DLF.NS", "GODREJPROP.NS", "OBEROIRLTY.NS",
    # Infrastructure / Logistics
    "ADANIPORTS.NS", "INDIGO.NS", "IRCTC.NS",
    # PSU / Govt
    "RECLTD.NS", "PFC.NS", "IRFC.NS",
    # New-age / Internet
    "ZOMATO.NS", "NYKAA.NS", "PAYTM.NS", "POLICYBZR.NS",
]

MID_CAP_SYMBOLS = [
    # Financials
    "ABCAPITAL.NS", "CANFINHOME.NS", "LICHSGFIN.NS", "MFSL.NS", "MANAPPURAM.NS",
    "IDBI.NS", "PNB.NS", "CANBK.NS", "INDIANB.NS", "MAHABANK.NS",
    "UNIONBANK.NS", "UJJIVANSFB.NS", "POONAWALLA.NS", "MOTILALOFS.NS",
    # IT / Tech
    "LTTS.NS", "CYIENT.NS", "KPITTECH.NS", "INDIAMART.NS", "OFSS.NS",
    "REDINGTON.NS",
    # Industrials & Capital Goods
    "BHARATFORG.NS", "ELGIEQUIP.NS", "KPIL.NS", "TIMKEN.NS", "SKFINDIA.NS",
    "GMRAIRPORT.NS", "NBCC.NS", "NCC.NS", "IRCON.NS", "HUDCO.NS",
    "SJVN.NS", "NHPC.NS", "NLCINDIA.NS",
    # Metals
    "HINDCOPPER.NS", "NATIONALUM.NS", "WELCORP.NS", "JINDALSAW.NS",
    # Automobiles / Auto Ancillaries
    "ESCORTS.NS", "ENDURANCE.NS", "EXIDEIND.NS", "SUNDRMFAST.NS",
    # Consumer / FMCG
    "BATAINDIA.NS", "RELAXO.NS", "LUXIND.NS", "JYOTHYLAB.NS", "KANSAINER.NS",
    "RAJESHEXPO.NS", "RAYMOND.NS",
    # Pharma
    "GLENMARK.NS", "LAURUSLABS.NS", "JBCHEPHARM.NS", "SYNGENE.NS",
    "IPCALAB.NS", "SPARC.NS",
    # Paints & Chemicals
    "GNFC.NS", "PCBL.NS", "DEEPAKNITRITE.NS", "CHAMBLFERT.NS",
    # Cement / Building Material
    "DALBHARAT.NS", "JKCEMENT.NS", "JKLAKSHMI.NS", "KAJARIACER.NS",
    "SUPREMEIND.NS", "APLAPOLLO.NS",
    # Consumer Durables
    "BLUESTARCO.NS", "DIXON.NS", "HONAUT.NS",
    # Real Estate
    "PRESTIGE.NS", "SOBHA.NS",
    # Energy
    "GUJGASLTD.NS", "MGL.NS", "MRPL.NS", "OIL.NS",
    # Pharma / Healthcare
    "METROPOLIS.NS", "MAXHEALTH.NS", "KIMS.NS",
    # Miscellaneous
    "ASTRAL.NS", "ATUL.NS", "AUBANK.NS", "BALKRISIND.NS",
    "CASTROLIND.NS", "CEATLTD.NS", "EMAMILTD.NS",
    "GICRE.NS", "IEX.NS", "LINDEINDIA.NS",
    "PAGEIND.NS", "POLYCAB.NS", "SOLARINDS.NS",
    "SONACOMS.NS", "SUNDARMFIN.NS", "SUNTV.NS",
    "TATACHEM.NS", "TATACOMM.NS", "TATAELXSI.NS",
    "TEAMLEASE.NS", "TRIDENT.NS", "VBL.NS",
    "ZEEL.NS", "ABFRL.NS",
]

SMALL_CAP_SYMBOLS = [
    # Pharma & Chemicals
    "AARTIDRUGS.NS", "AARTIIND.NS", "ALKYLAMINE.NS", "BALAMINES.NS",
    "CAPLIPOINT.NS", "DHANUKA.NS", "FINEORG.NS", "GALAXYSURF.NS",
    "GRANULES.NS", "HIKAL.NS", "INDOCO.NS", "NAVINFLUOR.NS",
    "SUDARSCHEM.NS", "SUMICHEM.NS", "VINATIORGA.NS", "SHILPAMED.NS",
    "MARKSANS.NS", "GHCL.NS", "BAYERCROP.NS", "PFIZER.NS",
    # IT / Tech
    "AFFLE.NS", "BSOFT.NS", "DATAPATTNS.NS", "HAPPSTMNDS.NS",
    "INTELLECT.NS", "LATENTVIEW.NS", "NAZARA.NS", "QUICKHEAL.NS",
    "ROUTE.NS", "STLTECH.NS", "TANLA.NS", "TEJASNET.NS",
    "ZENSARTECH.NS", "KFINTECH.NS",
    # Industrials & Engineering
    "BEML.NS", "CARBORUNIV.NS", "CRAFTSMAN.NS", "ELECON.NS",
    "FORCEMOT.NS", "GABRIEL.NS", "GRINDWELL.NS", "IMFA.NS",
    "JTEKTINDIA.NS", "KPRMILL.NS", "RATNAMANI.NS",
    "SURYAROSNI.NS", "TITAGARH.NS",
    # Consumer & Retail
    "AVANTIFEED.NS", "BALRAMCHIN.NS", "CAMPUS.NS", "CERA.NS",
    "DEVYANI.NS", "DOMS.NS", "HERITGFOOD.NS", "HATSUN.NS",
    "JKPAPER.NS", "KRBL.NS", "MAYURUNIQ.NS", "ORIENTELEC.NS",
    "SAREGAMA.NS", "SHOPERSTOP.NS", "SYMPHONY.NS", "TTKPRESTIG.NS",
    "VAIBHAVGBL.NS", "WONDERLA.NS",
    # Financials / NBFC
    "ANGELONE.NS", "DCBBANK.NS", "EQUITASBNK.NS", "HOMEFIRST.NS",
    "MMTC.NS", "NIACL.NS", "UCOBANK.NS", "UTIAMC.NS",
    # Energy & Infra
    "AEGISLOG.NS", "CHENNPETRO.NS", "OLECTRA.NS", "RAILTEL.NS",
    "TORNTPOWER.NS",
    # Real Estate
    "LEMONTREE.NS", "PHOENIXLTD.NS", "SANSERA.NS",
    # Automobiles
    "CIEINDIA.NS", "GREENPANEL.NS",
    # Healthcare
    "LALPATHLAB.NS", "NUVOCO.NS",
    # Defence / PSU small caps
    "CGPOWER.NS", "DELTACORP.NS", "DELHIVERY.NS",
    # Misc
    "CROMPTON.NS", "INDIGOPNTS.NS", "PRINCEPIPE.NS",
    "TATAINVEST.NS", "TRIVENI.NS",
]

# Combined universe (simulator and live trading use the same list)
NSE_UNIVERSE = LARGE_CAP_SYMBOLS + MID_CAP_SYMBOLS + SMALL_CAP_SYMBOLS

# Backwards-compatible alias
NIFTY100_SYMBOLS = NSE_UNIVERSE

# ─────────────────────────────────────────────
# BACKTEST SETTINGS
# ─────────────────────────────────────────────
BACKTEST_PERIOD_DAYS = 365   # How many days of history to backtest

# ─────────────────────────────────────────────
# DASHBOARD SETTINGS
# ─────────────────────────────────────────────
DASHBOARD_REFRESH_SECONDS = 30
LOG_FILE = "trading_bot.log"
