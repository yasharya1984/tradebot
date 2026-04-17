"""
Yash's Trading Bot — Dashboard
====================================
Streamlit-based web UI.

Run with:
    streamlit run dashboard.py

Pages (sidebar navigation):
  📡 Trading         – Live simulation / paper trading
  📊 Overview        – Capital, P&L summary
  📅 History         – Historical performance analytics
  🔍 Screener        – NSE 300 momentum screener
  📈 Backtest        – Strategy back-testing
  ⚙️  Settings       – Configure parameters & API keys
"""

import sys
import os
import logging
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

import config
import trade_store
import bot_orders as _bot_orders
from data_fetcher import DataFetcher
from stock_selector import StockSelector
from simulator import Simulator
from strategies import STRATEGY_MAP

# ──────────────────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Yash's Trading Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────
# Theme & custom CSS
# ──────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Hide top toolbar (deploy button, three-dot menu) entirely ── */
[data-testid="stDeployButton"]          { display: none !important; }
[data-testid="stToolbarActions"]        { display: none !important; }
[data-testid="stMainMenuButton"]        { display: none !important; }
[data-testid="stHeader"]                { display: none !important; }
/* Hide sidebar collapse arrow — sidebar is always visible */
[data-testid="stSidebarCollapsedControl"] { display: none !important; }

/* ════════════════════════════════════════════
   BASE — light slate-blue finance palette
   ════════════════════════════════════════════ */
[data-testid="stAppViewContainer"],
[data-testid="stAppViewContainer"] > div {
    background: #eef2f8;
    color: #1e2a3a;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f2847 0%, #1a3a6b 60%, #1e4580 100%);
    border-right: none;
}
/* stHeader is hidden entirely via the toolbar block above */

/* ── Remove Streamlit's default top padding so logo sits flush ── */
section[data-testid="stSidebar"] > div:first-child { padding-top: 0 !important; }
[data-testid="stSidebarHeader"] { display: none !important; }
[data-testid="stSidebar"] > div { padding-top: 0 !important; }
/* ── Remove top gap in main content area (header is hidden) ── */
[data-testid="stMainBlockContainer"],
.main .block-container,
[data-testid="stAppViewBlockContainer"] { padding-top: 0.75rem !important; }

/* ════════════════════════════════════════════
   SIDEBAR TEXT & WIDGETS
   (avoid blanket div rule so inline colors work)
   ════════════════════════════════════════════ */
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span { color: #dceeff !important; }
/* Override: all button text in sidebar must be white regardless of inner <p>/<span> */
[data-testid="stSidebar"] button,
[data-testid="stSidebar"] button p,
[data-testid="stSidebar"] button span { color: #ffffff !important; }
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 { color: #ffffff !important; }
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #a8ccf0 !important; }

/* ── Nav buttons — target via Streamlit's st-key-nav_* class ── */
[data-testid="stSidebar"] [class*="st-key-nav_"] {
    margin: 0 !important;
    padding: 0 !important;
}
[data-testid="stSidebar"] [class*="st-key-nav_"] > div { margin: 0 !important; padding: 0 !important; }
[data-testid="stSidebar"] [class*="st-key-nav_"] button {
    background: transparent !important;
    border: none !important;
    border-radius: 6px !important;
    text-align: left !important;
    color: #ffffff !important;
    font-size: 0.9rem !important;
    font-weight: 500 !important;
    padding: 6px 12px !important;
    width: 100% !important;
    transition: background 0.15s !important;
    box-shadow: none !important;
    margin: 0 !important;
    min-height: 0 !important;
}
[data-testid="stSidebar"] [class*="st-key-nav_"] button:hover {
    background: rgba(255,255,255,0.12) !important;
}
[data-testid="stSidebar"] [class*="st-key-nav_"] button p,
[data-testid="stSidebar"] [class*="st-key-nav_"] button span { color: #ffffff !important; }
/* ── Sidebar inputs ── */
[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background: rgba(255,255,255,0.12) !important;
    border-color: rgba(255,255,255,0.2) !important;
    color: #ffffff !important;
}
/* Number inputs in sidebar — white bg, dark readable text */
[data-testid="stSidebar"] [data-testid="stNumberInput"] input {
    background: rgba(255,255,255,0.95) !important;
    color: #0f2847 !important;
    border-color: rgba(255,255,255,0.35) !important;
    font-weight: 600 !important;
}
/* API Key / Access Token inputs — white bg, dark text */
[data-testid="stSidebar"] [data-testid="stTextInput"] input {
    background: rgba(255,255,255,0.95) !important;
    color: #0f2847 !important;
    border-color: rgba(255,255,255,0.35) !important;
}
/* All sidebar widget labels white */
[data-testid="stSidebar"] [data-testid="stWidgetLabel"],
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { color: #ffffff !important; }

/* ── Mode toggle buttons — same size, visible, gold when active ── */
[data-testid="stSidebar"] .stButton > button {
    background: rgba(255,255,255,0.13) !important;
    color: #ffffff !important;
    border: 1px solid rgba(255,255,255,0.28) !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.83rem !important;
    white-space: nowrap;
    min-height: 38px !important;
    width: 100% !important;
    transition: background 0.15s !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(255,255,255,0.22) !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: linear-gradient(135deg,#f0b429,#d4960c) !important;
    color: #0f2847 !important;
    border: none !important;
    font-weight: 700 !important;
    box-shadow: 0 2px 8px rgba(240,180,41,0.35) !important;
}

/* ════════════════════════════════════════════
   MAIN CONTENT TEXT
   ════════════════════════════════════════════ */
[data-testid="stMainBlockContainer"] p,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span { color: #1e2a3a !important; }

/* ════════════════════════════════════════════
   HEADINGS
   ════════════════════════════════════════════ */
h1, h2, h3 { color: #1a4f8a !important; letter-spacing: 0.02em; }
h4, h5, h6 { color: #2c4f7c !important; letter-spacing: 0.01em; }
h1 { font-size: 1.75rem !important; }

/* ════════════════════════════════════════════
   HERO HEADER BANNER
   ════════════════════════════════════════════ */
.hero-banner {
    background: linear-gradient(135deg, #0f2847 0%, #1a4f8a 55%, #2563b4 100%);
    border-radius: 14px;
    padding: 18px 28px 16px 28px;
    margin-bottom: 18px;
    box-shadow: 0 4px 24px rgba(15,40,71,0.22);
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
}
.hero-title {
    color: #ffffff;
    font-size: 1.65rem;
    font-weight: 800;
    letter-spacing: 0.03em;
    margin: 0;
}
.hero-sub { color: #a8ccf0; font-size: 0.82rem; margin-top: 2px; }
.hero-stats {
    display: flex; gap: 20px; flex-wrap: wrap; align-items: center;
}
.hero-stat {
    text-align: right; color: #c8ddf4;
    font-size: 0.78rem; line-height: 1.5;
}
.hero-stat strong { color: #ffffff; font-size: 1rem; display: block; }

/* ════════════════════════════════════════════
   STATUS PILLS
   ════════════════════════════════════════════ */
.pill-open {
    background: #e6f9ee; color: #145c2e;
    border: 1px solid #a3d9b7; border-radius: 20px;
    padding: 3px 12px; font-weight: 700; font-size: 0.8rem;
    display: inline-block;
}
.pill-closed {
    background: #fde8e8; color: #8b1a1a;
    border: 1px solid #f4a0a0; border-radius: 20px;
    padding: 3px 12px; font-weight: 700; font-size: 0.8rem;
    display: inline-block;
}
.pill-pre {
    background: #fff3d4; color: #7a4800;
    border: 1px solid #f4cc80; border-radius: 20px;
    padding: 3px 12px; font-weight: 700; font-size: 0.8rem;
    display: inline-block;
}
.pill-running {
    background: linear-gradient(135deg,#dce8f8,#eef4fc);
    color: #1a4f8a; border: 1px solid #a8c8e8; border-radius: 20px;
    padding: 3px 12px; font-weight: 700; font-size: 0.8rem;
    display: inline-block;
}

/* ════════════════════════════════════════════
   METRIC CARDS
   ════════════════════════════════════════════ */
[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #c0d4ec;
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: 0 2px 8px rgba(26,79,138,0.08);
    transition: box-shadow 0.2s;
}
[data-testid="stMetric"]:hover { box-shadow: 0 4px 16px rgba(26,79,138,0.14); }
[data-testid="stMetricLabel"]  { color: #4a6080 !important; font-size: 0.78rem; }
[data-testid="stMetricValue"]  { color: #1a4f8a !important; font-weight: 700; }
[data-testid="stMetricDelta"]  svg { display: none; }

/* ════════════════════════════════════════════
   MODE TOGGLE — sidebar variant
   ════════════════════════════════════════════ */
/* Sidebar toggle: white label, slightly larger pill */
[data-testid="stSidebar"] div[data-testid="stToggle"] {
    align-items: center;
    gap: 10px;
    padding: 6px 12px;
    background: rgba(255,255,255,0.08);
    border-radius: 10px;
    margin: 4px 0;
}
[data-testid="stSidebar"] div[data-testid="stToggle"] label,
[data-testid="stSidebar"] div[data-testid="stToggle"] label span,
[data-testid="stSidebar"] div[data-testid="stToggle"] p {
    color: #ffffff !important;
    font-weight: 700 !important;
    font-size: 0.88rem !important;
}
[data-testid="stSidebar"] div[data-testid="stToggle"] > div {
    transform: scale(1.15);
    transform-origin: left center;
}

/* ════════════════════════════════════════════
   TABS
   ════════════════════════════════════════════ */
[data-testid="stTabs"] [role="tablist"] {
    background: #dde6f2;
    border-bottom: 2px solid #1a4f8a;
    border-radius: 6px 6px 0 0;
    gap: 2px;
}
[data-testid="stTabs"] button[role="tab"] {
    color: #5a7090 !important; background: transparent;
    border-radius: 6px 6px 0 0; font-weight: 500;
    padding: 8px 18px; transition: color 0.2s, background 0.2s;
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: #1a4f8a !important; background: #eef2f8 !important;
    border-bottom: 2px solid #1a4f8a; font-weight: 700;
}
[data-testid="stTabs"] button[role="tab"]:hover {
    color: #1a4f8a !important; background: #e4ecf6 !important;
}

/* ════════════════════════════════════════════
   BUTTONS
   ════════════════════════════════════════════ */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #1a4f8a, #2a72c8);
    color: #ffffff !important; border: none; border-radius: 8px;
    font-weight: 700; letter-spacing: 0.03em;
    padding: 8px 20px; transition: opacity 0.2s, transform 0.1s;
    box-shadow: 0 2px 8px rgba(26,79,138,0.25);
}
.stButton > button[kind="primary"]:hover { opacity: 0.88; transform: translateY(-1px); }
.stButton > button {
    background: #dde6f2; color: #1e2a3a !important;
    border: 1px solid #b0c8e4; border-radius: 8px;
    padding: 8px 16px;
}
.stButton > button:hover { background: #ccd8ec !important; }

/* ════════════════════════════════════════════
   INPUTS / SELECTBOX
   ════════════════════════════════════════════ */
[data-baseweb="select"] > div,
[data-baseweb="input"] > div {
    background: #ffffff !important;
    border-color: #a8c0dc !important;
    color: #1e2a3a !important;
}
[data-baseweb="select"] [data-testid="stMarkdownContainer"] p { color: #1e2a3a !important; }

/* ════════════════════════════════════════════
   DATAFRAMES
   ════════════════════════════════════════════ */
[data-testid="stDataFrame"] {
    border: 1px solid #c0d4ec; border-radius: 10px;
    overflow: hidden; box-shadow: 0 2px 6px rgba(26,79,138,0.07);
}
[data-testid="stDataFrame"] [class*="cell-wrapper"],
[data-testid="stDataFrame"] [class*="cell"] {
    color: #1e2a3a; background-color: #ffffff;
}

/* ════════════════════════════════════════════
   EXPANDERS
   ════════════════════════════════════════════ */
[data-testid="stExpander"] {
    background: #f4f8fd; border: 1px solid #c0d4ec; border-radius: 10px;
    box-shadow: 0 1px 4px rgba(26,79,138,0.05);
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary span { color: #1e2a3a !important; font-weight: 600; }
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] p { color: #2c3e58 !important; }

/* ════════════════════════════════════════════
   ALERTS
   ════════════════════════════════════════════ */
[data-testid="stAlert"] { border-radius: 10px; }
[data-testid="stAlert"] p { color: inherit !important; }

/* ════════════════════════════════════════════
   DIVIDER
   ════════════════════════════════════════════ */
hr { border-color: #c0d4ec !important; }

/* ════════════════════════════════════════════
   DISCLAIMER / FOOTER BANNER
   ════════════════════════════════════════════ */
.disclaimer-banner {
    background: linear-gradient(135deg, #fff8e6, #fef0c0);
    border: 1px solid #d4960c; border-radius: 10px;
    padding: 8px 18px; margin: 6px 0;
    font-size: 0.78rem; color: #7a5000;
    text-align: center; letter-spacing: 0.04em; font-weight: 600;
}

/* ════════════════════════════════════════════
   FOOTER
   ════════════════════════════════════════════ */
.footer {
    margin-top: 48px; padding: 24px 20px;
    border-top: 2px solid #c0d4ec;
    background: linear-gradient(135deg, #f4f8fd, #eef2f8);
    border-radius: 0 0 12px 12px;
    text-align: center; color: #5a7090;
    font-size: 0.76rem; line-height: 2;
}
.footer strong { color: #2c4a7c; }
.footer .copy  { color: #3a5a8c; font-weight: 600; font-size: 0.82rem; }

/* ════════════════════════════════════════════
   TABLE HEADER SEPARATOR
   ════════════════════════════════════════════ */
.tbl-sep { border:none; border-top:2px solid #c0d4ec; margin:3px 0 6px 0; }

/* ════════════════════════════════════════════
   WIDGET LABELS & CAPTIONS (main content)
   ════════════════════════════════════════════ */
label, .stSelectbox label, .stMultiSelect label,
.stSlider label, .stNumberInput label,
.stTextInput label, .stTextArea label { color: #1e3558 !important; font-weight: 600; }
.stCaption, [data-testid="stCaptionContainer"] { color: #3a5878 !important; }
[data-testid="stWidgetLabel"]  { color: #1e3558 !important; font-weight: 600; }
[data-testid="stWidgetLabel"] p { color: #1e3558 !important; }
/* Selectbox / input text */
[data-baseweb="select"] [data-testid="stMarkdownContainer"] p { color: #1e2a3a !important; }
/* Make info/warning/error text clearly readable */
[data-testid="stAlert"] p { color: inherit !important; font-weight: 500; }

/* ════════════════════════════════════════════
   MARKET CLOCK CARD  (inside hero)
   ════════════════════════════════════════════ */
.mkt-card {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 10px; padding: 10px 16px;
    color: #ffffff; font-size: 0.82rem; line-height: 1.7;
    min-width: 160px;
}
.mkt-card .status { font-weight: 800; font-size: 1rem; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE, mode="a"),
    ],
)
logger = logging.getLogger("dashboard")

# ──────────────────────────────────────────────────────────
# Session State Initialisation
# ──────────────────────────────────────────────────────────
def init_session():
    if "fetcher" not in st.session_state:
        st.session_state.fetcher         = DataFetcher()
        st.session_state.selector        = StockSelector(st.session_state.fetcher)
        # Two independent simulators — one per mode so they never conflict
        st.session_state.simulator_sim   = Simulator(st.session_state.fetcher)
        st.session_state.simulator_live  = Simulator(st.session_state.fetcher)
        st.session_state.selected_stocks  = None
        st.session_state.backtest_results = None
        # Per-mode bot state (keyed with "_sim" / "_live" suffix)
        for _m in ("sim", "live"):
            st.session_state[f"paper_running_{_m}"]    = False
            st.session_state[f"paper_results_{_m}"]    = {}
            st.session_state[f"paper_ticks_{_m}"]      = 0
            st.session_state[f"_bg_thread_{_m}"]       = None
            st.session_state[f"_bg_last_update_{_m}"]  = None
            st.session_state[f"_tick_done_event_{_m}"] = threading.Event()
            st.session_state[f"_tick_result_{_m}"]     = {}
            st.session_state[f"_bot_start_time_{_m}"]  = None
        st.session_state._kite_trader          = None
        # _view_mode = what we're currently DISPLAYING ("sim" or "live")
        # Both modes can run simultaneously; this is just the view toggle.
        # Seed from query params so a page refresh restores the last state.
        _valid_pages = {"trading", "overview", "history", "screener", "backtest", "settings"}
        st.session_state._view_mode = st.query_params.get("mode", "sim") if st.query_params.get("mode") in ("sim", "live") else "sim"
        st.session_state._nav_page  = st.query_params.get("page", "trading") if st.query_params.get("page") in _valid_pages else "trading"
        st.session_state._strategies_to_run    = list(STRATEGY_MAP.keys())
        # Per-mode independent config — each mode has its own copy of all settings
        _default_cfg = {
            "CAPITAL":                   config.CAPITAL,
            "STOP_LOSS_PCT":             config.STOP_LOSS_PCT,
            "TARGET_PCT":                config.TARGET_PCT,
            "TRAILING_STOP_PCT":         config.TRAILING_STOP_PCT,
            "MAX_OPEN_POSITIONS":        config.MAX_OPEN_POSITIONS,
            "MAX_POSITION_PCT":          config.MAX_POSITION_PCT,
            "MA_SHORT_PERIOD":           config.MA_SHORT_PERIOD,
            "MA_LONG_PERIOD":            config.MA_LONG_PERIOD,
            "RSI_PERIOD":                config.RSI_PERIOD,
            "RSI_OVERSOLD":              config.RSI_OVERSOLD,
            "RSI_OVERBOUGHT":            config.RSI_OVERBOUGHT,
            "MOMENTUM_LOOKBACK":         config.MOMENTUM_LOOKBACK,
            "TOP_N_STOCKS":              config.TOP_N_STOCKS,
            "LIVE_TRADING_CAP":          config.LIVE_TRADING_CAP,
            "DASHBOARD_REFRESH_SECONDS": config.DASHBOARD_REFRESH_SECONDS,
        }
        st.session_state.config_sim  = dict(_default_cfg)
        st.session_state.config_live = dict(_default_cfg)

init_session()

fetcher        = st.session_state.fetcher
selector       = st.session_state.selector
simulator      = st.session_state.simulator_sim   # alias used by non-trading pages
simulator_live = st.session_state.simulator_live

# ──────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────
_IST = timezone(timedelta(hours=5, minutes=30))

def _market_status():
    """Returns (status: str, label: str, secs_until_change: int)."""
    now = datetime.now(_IST)
    wd  = now.weekday()  # 0=Mon … 6=Sun
    if wd >= 5:
        days = 7 - wd
        nxt  = (now + timedelta(days=days)).replace(hour=9, minute=15, second=0, microsecond=0)
        return "CLOSED", f"Opens Mon 9:15 AM IST", int((nxt - now).total_seconds())
    mo = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    mc = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < mo:
        diff = int((mo - now).total_seconds())
        h, r = divmod(diff, 3600); m = r // 60
        return "PRE-OPEN", f"Opens in {h}h {m}m", diff
    elif now <= mc:
        diff = int((mc - now).total_seconds())
        h, r = divmod(diff, 3600); m = r // 60
        return "OPEN", f"Closes in {h}h {m}m", diff
    else:
        nxt = (now + timedelta(days=1)).replace(hour=9, minute=15, second=0, microsecond=0)
        if nxt.weekday() >= 5:
            nxt += timedelta(days=(7 - nxt.weekday()))
        return "CLOSED", "Opens tomorrow 9:15 AM IST", int((nxt - now).total_seconds())

def _fmt_hm(secs: int) -> str:
    h, r = divmod(max(0, secs), 3600); m = r // 60
    return f"{h}h {m}m" if h else f"{m}m"

def colour_pnl(val):
    colour = "#145c2e" if val >= 0 else "#8b1a1a"
    return f'<span style="color:{colour};font-weight:bold">₹{val:+,.2f}</span>'

def signal_badge(signal):
    return {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "🟡 HOLD"}.get(signal, signal)

def _rdylgn(series):
    lo, hi = series.min(), series.max()
    styles = []
    for v in series:
        t = 0.5 if hi == lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))
        if t < 0.25:   bg, fg = "#fde8e8", "#8b1a1a"
        elif t < 0.5:  bg, fg = "#fef3e0", "#7a4000"
        elif t < 0.75: bg, fg = "#fefce0", "#6b5a00"
        else:          bg, fg = "#e6f9ee", "#145c2e"
        styles.append(f"background-color:{bg};color:{fg};font-weight:600")
    return styles


def _paper_tick_worker(
    strategies: list,
    sim,
    done_event: threading.Event,
    result_container: dict,
    kite_trader=None,
) -> None:
    """
    Background thread: one paper-trading tick for all strategies.
    Writes results into *result_container* and sets *done_event*.
    Never touches st.session_state to avoid 'missing ScriptRunContext'.
    """
    results = {}
    for strat in strategies:
        try:
            results[strat] = sim.paper_trading_tick(strat)
        except Exception as exc:
            logger.error(f"Tick error [{strat}]: {exc}")

    if kite_trader is not None:
        try:
            results["_kite_live"] = kite_trader.get_live_portfolio_status()
        except Exception as exc:
            logger.error(f"Kite live-status fetch failed: {exc}")
            results["_kite_live"] = {
                "error": str(exc), "positions": [], "orders": [], "connected": False,
            }

    result_container.clear()
    result_container.update(results)
    done_event.set()


def _apply_mode_config(mode: str) -> None:
    """Copy the stored per-mode config values onto the live config module."""
    cfg = st.session_state.get(f"config_{mode}", {})
    for _k, _v in cfg.items():
        if hasattr(config, _k):
            setattr(config, _k, _v)


def _save_mode_config(mode: str) -> None:
    """Snapshot current config module values back into the per-mode store."""
    _keys = [
        "CAPITAL", "STOP_LOSS_PCT", "TARGET_PCT", "TRAILING_STOP_PCT",
        "MAX_OPEN_POSITIONS", "MAX_POSITION_PCT", "MA_SHORT_PERIOD",
        "MA_LONG_PERIOD", "RSI_PERIOD", "RSI_OVERSOLD", "RSI_OVERBOUGHT",
        "MOMENTUM_LOOKBACK", "TOP_N_STOCKS", "LIVE_TRADING_CAP",
        "DASHBOARD_REFRESH_SECONDS",
    ]
    cfg = st.session_state.setdefault(f"config_{mode}", {})
    for _k in _keys:
        cfg[_k] = getattr(config, _k, cfg.get(_k))


def _build_results_from_disk(strategies: list, mode: str) -> dict:
    """
    Build a paper_results-compatible dict from stored portfolio files.
    Used to immediately show positions on page reload without waiting for a tick.
    Entry prices are used as proxy for current prices (updated on first tick).
    """
    _sim_inst = (
        st.session_state.simulator_live if mode == "live"
        else st.session_state.simulator_sim
    )
    results = {}
    for strat in strategies:
        _sim_inst.initialize_paper_trading([strat], mode=mode)
        port = _sim_inst.get_paper_portfolio(strat)
        if port:
            cp = {sym: pos.entry_price for sym, pos in port.positions.items()}
            results[strat] = {
                "strategy":  strat,
                "timestamp": "restored",
                "signals":   {},
                "portfolio": {
                    "cash":      round(port.cash, 2),
                    "equity":    round(port.total_equity(cp), 2),
                    "pnl":       round(port.total_pnl(cp), 2),
                    "pnl_pct":   round(port.total_pnl_pct(cp), 2),
                    "positions": len(port.positions),
                },
            }
    return results


# ── Inject Live Mode theme override (CSS after base styles wins on equal specificity) ──
if st.session_state.get("_view_mode") == "live":
    st.markdown("""
<style>
/* ════ LIVE MODE THEME OVERRIDE ════ */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1f0e00 0%, #4a2800 60%, #6b3c00 100%) !important;
}
[data-testid="stSidebar"] .nav-btn-active > .stButton > button {
    background: rgba(255,165,0,0.3) !important;
    border-left-color: #ffa500 !important;
    color: #ffffff !important;
}
/* Keep sidebar text bright on dark amber bg */
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span { color: #ffe8b0 !important; }
/* All sidebar button text white in live mode */
[data-testid="stSidebar"] button,
[data-testid="stSidebar"] button p,
[data-testid="stSidebar"] button span { color: #ffffff !important; }
[data-testid="stAppViewContainer"],
[data-testid="stAppViewContainer"] > div { background: #fdf8ef !important; }
/* Main content text stays dark on warm background */
[data-testid="stMainBlockContainer"] p,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span { color: #2a1800 !important; }
.hero-banner {
    background: linear-gradient(135deg, #2a1500 0%, #7a4800 55%, #c47f00 100%) !important;
}
[data-testid="stMetric"] {
    border-color: #d4960c !important;
    background: #fff8e6 !important;
}
[data-testid="stMetricValue"]  { color: #6b3800 !important; }
[data-testid="stMetricLabel"]  { color: #7a4a10 !important; font-weight: 700 !important; }
[data-testid="stMetricDelta"]  { color: #7a4a10 !important; }
h1, h2, h3 { color: #6b3800 !important; }
h4, h5, h6 { color: #7a4a10 !important; }
label, [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p { color: #5c3000 !important; }
[data-testid="stTabs"] [role="tablist"]            { background: #f5e8c8 !important; border-bottom-color: #c47f00 !important; }
[data-testid="stTabs"] button[role="tab"]          { color: #7a5020 !important; }
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] { color: #6b3800 !important; border-bottom-color: #c47f00 !important; background: #fdf8ef !important; font-weight: 700 !important; }
.stCaption, [data-testid="stCaptionContainer"] { color: #7a5020 !important; }
</style>
""", unsafe_allow_html=True)

_JS_CLOCK = """
<script>
(function(){
  var DAYS=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function pad(n){return String(n).padStart(2,'0');}
  function getIST(){return new Date(Date.now()+5.5*3600000);}
  function mktInfo(ist){
    var wd=ist.getUTCDay(),h=ist.getUTCHours(),m=ist.getUTCMinutes(),mins=h*60+m;
    if(wd===0||wd===6)return{s:'CLOSED',c:'#ff6060',l:'Weekend'};
    if(mins>=555&&mins<=930){var r=930-mins;return{s:'OPEN',c:'#50e880',l:'Closes in '+Math.floor(r/60)+'h '+(r%60)+'m'};}
    if(mins>=540&&mins<555){var r2=555-mins;return{s:'PRE-OPEN',c:'#f0b429',l:'Opens in '+r2+'m'};}
    if(mins<540){var r3=555-mins;return{s:'CLOSED',c:'#ff6060',l:'Opens in '+Math.floor(r3/60)+'h '+(r3%60)+'m'};}
    return{s:'CLOSED',c:'#ff6060',l:'Opens tomorrow 9:15 AM IST'};
  }
  function tick(){
    var ist=getIST(),mi=mktInfo(ist);
    var ts=DAYS[ist.getUTCDay()]+' '+pad(ist.getUTCDate())+' '+MONTHS[ist.getUTCMonth()]+' '+ist.getUTCFullYear()+' · '+pad(ist.getUTCHours())+':'+pad(ist.getUTCMinutes())+':'+pad(ist.getUTCSeconds())+' IST';
    var hm=pad(ist.getUTCHours())+':'+pad(ist.getUTCMinutes());
    ['sb-ist-time','hero-ist-time'].forEach(function(id){var e=document.getElementById(id);if(e)e.innerText=ts;});
    ['hero-ist-hm'].forEach(function(id){var e=document.getElementById(id);if(e)e.innerText=hm;});
    ['sb-mkt-status','hero-mkt-status'].forEach(function(id){var e=document.getElementById(id);if(e){e.innerText='● '+mi.s;e.style.color=mi.c;}});
    ['sb-mkt-label','hero-mkt-label'].forEach(function(id){var e=document.getElementById(id);if(e)e.innerText=mi.l;});
  }
  if(window._istTick)clearInterval(window._istTick);
  window._istTick=setInterval(tick,1000);
  tick();
})();
</script>
"""


# ──────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────
with st.sidebar:
    # ── Logo ──────────────────────────────────────────────
    st.markdown(
        "<div style='padding:12px 0 8px 0'>"
        "<div style='font-size:2rem;margin-bottom:2px'>📈</div>"
        "<div style='color:#ffffff;font-size:1.25rem;font-weight:800;letter-spacing:0.04em'>"
        "Yash's Trading Bot</div>"
        "<div style='color:#7aa8d4;font-size:0.75rem;margin-top:2px'>"
        "NSE · NSE 300 · Day Trading</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("<hr style='border-color:rgba(255,255,255,0.15);margin:4px 0 6px 0'>",
                unsafe_allow_html=True)

    # ── Global Mode Toggle — "📊 Sim ●━━ ⚡ Live" ─────────
    _is_live_now = (st.session_state.get("_view_mode", "sim") == "live")
    _tc1, _tc2, _tc3 = st.columns([4, 3, 4])
    _tc1.markdown(
        f"<div style='text-align:right;padding-top:5px;font-size:0.82rem;"
        f"font-weight:{'800' if not _is_live_now else '400'};"
        f"color:{'#ffffff' if not _is_live_now else '#6a8aaa'}'>"
        f"📊 Sim</div>",
        unsafe_allow_html=True,
    )
    with _tc2:
        _sb_live = st.toggle(
            "",
            value=_is_live_now,
            key="sidebar_mode_toggle",
            label_visibility="collapsed",
        )
    _tc3.markdown(
        f"<div style='text-align:left;padding-top:5px;font-size:0.82rem;"
        f"font-weight:{'800' if _is_live_now else '400'};"
        f"color:{'#ffd080' if _is_live_now else '#6a8aaa'}'>"
        f"⚡ Live</div>",
        unsafe_allow_html=True,
    )
    if _sb_live and st.session_state.get("_view_mode") != "live":
        st.session_state._view_mode = "live"
        st.query_params["mode"] = "live"
        st.rerun()
    elif not _sb_live and st.session_state.get("_view_mode") != "sim":
        st.session_state._view_mode = "sim"
        st.query_params["mode"] = "sim"
        st.rerun()

    # ── Live mode warning badge ────────────────────────────
    if st.session_state.get("_view_mode") == "live":
        st.markdown(
            "<div style='background:#5c2e00;border:1px solid #ffa500;"
            "border-radius:8px;padding:6px 12px;margin:4px 0 6px 0'>"
            "<span style='color:#ffd080;font-size:0.78rem;font-weight:700'>"
            "⚠️ LIVE — Real orders will be placed!</span>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<hr style='border-color:rgba(255,255,255,0.15);margin:6px 0 8px 0'>",
                unsafe_allow_html=True)

    # ── Navigation (button-per-item, no radio) ────────────
    _NAV_ITEMS = [
        ("📡 Trading",  "trading"),
        ("📊 Overview", "overview"),
        ("📅 History",  "history"),
        ("🔍 Screener", "screener"),
        ("📈 Backtest", "backtest"),
        ("⚙️ Settings", "settings"),
    ]
    _cur = st.session_state.get("_nav_page", "trading")

    # Inject active-item highlight via Streamlit's st-key-nav_* class
    st.markdown(f"""<style>
[data-testid="stSidebar"] .st-key-nav_{_cur} button {{
    background: rgba(240,180,41,0.18) !important;
    border-left: 3px solid #f0b429 !important;
    padding-left: 9px !important;
    font-weight: 700 !important;
}}
</style>""", unsafe_allow_html=True)

    for _lbl, _key in _NAV_ITEMS:
        if st.button(_lbl, key=f"nav_{_key}", use_container_width=True):
            st.session_state._nav_page = _key
            st.query_params["page"] = _key
            st.rerun()

    _page = st.session_state.get("_nav_page", "trading")

    st.markdown("<hr style='border-color:rgba(255,255,255,0.15);margin:10px 0'>",
                unsafe_allow_html=True)

    # ── Market Status + Clock (JS updates every second client-side) ──
    st.markdown(
        "<div style='background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);"
        "border-radius:10px;padding:10px 14px;margin-top:4px'>"
        "<div style='color:#90b8e0;font-size:0.7rem;font-weight:700;letter-spacing:0.08em'>NSE MARKET</div>"
        "<div id='sb-mkt-status' style='font-size:0.95rem;font-weight:800;margin:3px 0'>● ...</div>"
        "<div id='sb-mkt-label' style='color:#c8ddf4;font-size:0.75rem'>—</div>"
        "<div id='sb-ist-time' style='color:#7aa8d4;font-size:0.7rem;margin-top:4px'>—</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────
# Global view-mode — apply this mode's config on every render
# ──────────────────────────────────────────────────────────
_vm = st.session_state.get("_view_mode", "sim")
_apply_mode_config(_vm)

# ──────────────────────────────────────────────────────────
# Hero header — static parts from Python, clock via JS
# ──────────────────────────────────────────────────────────
_run_hero = "—"
_hero_start = st.session_state.get(f"_bot_start_time_{_vm}")
if _hero_start:
    _run_hero = _fmt_hm(int((datetime.now() - _hero_start).total_seconds()))
_mode_badge = "⚡ LIVE" if _vm == "live" else "📊 SIM"

st.markdown(
    f"""
    <div class="hero-banner">
      <div>
        <div class="hero-title">📈 Yash's Trading Bot</div>
        <div class="hero-sub">NSE · NSE 300 Day Trading · <span id="hero-date"></span></div>
      </div>
      <div class="hero-stats">
        <div class="mkt-card">
          <div style="color:#a8ccf0;font-size:0.7rem;font-weight:700;letter-spacing:0.08em">NSE MARKET</div>
          <div id="hero-mkt-status" style="font-size:0.95rem;font-weight:800;margin:3px 0">● ...</div>
          <div id="hero-mkt-label" style="font-size:0.75rem;color:#c8ddf4">—</div>
          <div id="hero-ist-time" style="font-size:0.7rem;color:#7aa8d4">—</div>
        </div>
        <div class="hero-stat">
          <strong id="hero-ist-hm">—</strong>IST Time
        </div>
        <div class="hero-stat">
          <strong>{'▶ ' + _run_hero if _hero_start else '⏹ Stopped'}</strong>Bot Running
        </div>
        <div class="hero-stat">
          <strong>{_mode_badge}</strong>Viewing
        </div>
      </div>
    </div>
    {_JS_CLOCK}
    """,
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════
# PAGE ROUTING
# ══════════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────
# PAGE: TRADING
# ──────────────────────────────────────────────────────────
if _page == "trading":
    is_live     = (_vm == "live")
    _trade_mode = _vm  # "sim" or "live"

    st.header("⚡ Live Trading" if is_live else "📡 Paper Trading — Live Simulation")

    # ── Restore from disk on view switch if results are empty for this mode ──
    if not st.session_state.get(f"paper_results_{_vm}"):
        _restored = _build_results_from_disk(list(STRATEGY_MAP.keys()), _trade_mode)
        if _restored:
            st.session_state[f"paper_results_{_vm}"] = _restored

    if is_live:
        st.info(
            f"💡 Trading cap: **₹{config.LIVE_TRADING_CAP:,.0f}** — change in ⚙️ Settings.  "
            "The bot will not deploy more than this regardless of account balance."
        )
        with st.expander("🔄 Sync existing Kite positions into the bot", expanded=False):
            st.caption(
                "Run this once after Start if you already have open intraday "
                "positions in Kite so the bot doesn't open duplicate trades."
            )
            sync_strat = st.selectbox(
                "Sync into strategy",
                list(STRATEGY_MAP.keys()),
                format_func=lambda x: STRATEGY_MAP[x].name,
                key="sync_strat_key",
            )
            if st.button("📥 Sync from Kite", key="kite_sync_btn"):
                kite_t = st.session_state.get("_kite_trader")
                if not kite_t or not kite_t.is_connected:
                    st.error("Not connected to Kite — press ▶️ Start first.")
                else:
                    res = simulator.sync_live_portfolio(sync_strat, kite_t)
                    if res["error"]:
                        st.error(f"Sync error: {res['error']}")
                    else:
                        st.success(
                            f"Imported {res['positions_loaded']} position(s), "
                            f"{res['positions_skipped']} already tracked."
                        )

    if not fetcher.is_market_open():
        st.warning(
            "🕐 NSE market is closed. Last available prices will be used. "
            "Market hours: Mon–Fri, 9:15 AM – 3:30 PM IST"
        )

    # ── Controls row ─────────────────────────────────────────
    _cur_sim = st.session_state.simulator_live if is_live else st.session_state.simulator_sim

    ctrl1, ctrl2, ctrl3, ctrl4, ctrl5 = st.columns([3, 1, 1, 1, 2])
    strategies_to_run = ctrl1.multiselect(
        "Strategies",
        list(STRATEGY_MAP.keys()),
        default=list(STRATEGY_MAP.keys()),
        format_func=lambda x: STRATEGY_MAP[x].name,
    )
    st.session_state._strategies_to_run = strategies_to_run
    refresh_secs = ctrl2.number_input("Refresh (s)", 10, 300, config.DASHBOARD_REFRESH_SECONDS, step=10)

    _is_running = st.session_state.get(f"paper_running_{_vm}", False)
    start_paper = ctrl3.button(
        "▶️ Start" if not _is_running else "⏸ Pause",
        type="primary",
        use_container_width=True,
    )
    restart_btn = ctrl4.button(
        "🔄 Restart",
        help="Delete all saved trades for this mode and start from scratch.",
        use_container_width=True,
    )

    # ── Start / Pause handler ─────────────────────────────────
    if start_paper:
        _new_running = not _is_running
        st.session_state[f"paper_running_{_vm}"] = _new_running
        if _new_running:
            if is_live:
                try:
                    from zerodha_trader import ZerodhaTrader as _ZT
                    _trader = _ZT(
                        config.ZERODHA_API_KEY,
                        config.ZERODHA_API_SECRET,
                        config.ZERODHA_ACCESS_TOKEN,
                    )
                    if _trader.connect():
                        st.session_state._kite_trader = _trader
                        st.success("✅ Connected to Zerodha Kite.")
                    else:
                        st.warning(
                            "⚠️ Could not connect to Kite — check API credentials in ⚙️ Settings. "
                            "Simulation signals will still run."
                        )
                except Exception as _e:
                    st.warning(f"Kite connect error: {_e}")

            _cur_sim.initialize_paper_trading(strategies_to_run, mode=_trade_mode)
            st.session_state[f"_bg_thread_{_vm}"]             = None
            st.session_state[f"_bg_last_update_{_vm}"]        = None
            st.session_state[f"_tick_done_event_{_vm}"]       = threading.Event()
            st.session_state[f"_tick_result_{_vm}"]           = {}
            st.session_state[f"_bot_start_time_{_vm}"]        = datetime.now()
            st.success(
                f"{'Live' if is_live else 'Paper'} trading started — "
                "saved state loaded where available."
            )
        else:
            st.info(f"{'Live' if is_live else 'Simulation'} paused.")

    # ── Restart handler ───────────────────────────────────────
    if restart_btn:
        _cur_sim.reset_paper_trading(strategies_to_run, mode=_trade_mode)
        st.session_state[f"paper_results_{_vm}"]       = {}
        st.session_state[f"paper_ticks_{_vm}"]         = 0
        st.session_state[f"paper_running_{_vm}"]       = False
        st.session_state[f"_bg_thread_{_vm}"]          = None
        st.session_state[f"_bg_last_update_{_vm}"]     = None
        st.session_state[f"_tick_done_event_{_vm}"]    = threading.Event()
        st.session_state[f"_tick_result_{_vm}"]        = {}
        st.session_state[f"_bot_start_time_{_vm}"]     = None
        if is_live:
            st.session_state._kite_trader = None
        st.success(
            f"✅ {'Live' if is_live else 'Simulation'} reset — all saved trades deleted. "
            "Press ▶️ Start to begin fresh."
        )

    # ══════════════════════════════════════════════════════
    # Live-refreshing fragment — handles BOTH modes in parallel
    # ══════════════════════════════════════════════════════
    @st.fragment(run_every=refresh_secs)
    def _paper_live_view():
        _frag_vm   = st.session_state.get("_view_mode", "sim")
        _frag_live = (_frag_vm == "live")
        _frag_mode = _frag_vm  # "sim" or "live"
        _strategies = st.session_state.get("_strategies_to_run", list(STRATEGY_MAP.keys()))

        # ① Advance ticks for ALL running modes (not just the one we're viewing)
        for _mode in ("sim", "live"):
            if not st.session_state.get(f"paper_running_{_mode}", False):
                continue
            _de = st.session_state[f"_tick_done_event_{_mode}"]
            _rc = st.session_state[f"_tick_result_{_mode}"]
            # Promote finished results
            if _de.is_set():
                _de.clear()
                st.session_state[f"paper_results_{_mode}"] = dict(_rc)
                st.session_state[f"paper_ticks_{_mode}"]  += 1
                st.session_state[f"_bg_last_update_{_mode}"] = datetime.now()
            # Launch next tick if thread is idle
            _th = st.session_state.get(f"_bg_thread_{_mode}")
            if _th is None or not _th.is_alive():
                _sim_inst = (
                    st.session_state.simulator_live
                    if _mode == "live"
                    else st.session_state.simulator_sim
                )
                _kite_arg = st.session_state.get("_kite_trader") if _mode == "live" else None
                _t = threading.Thread(
                    target=_paper_tick_worker,
                    args=(_strategies, _sim_inst, _de, _rc, _kite_arg),
                    daemon=True,
                )
                _t.start()
                st.session_state[f"_bg_thread_{_mode}"] = _t

        # ② Status bar for current view
        _paper_running  = st.session_state.get(f"paper_running_{_frag_vm}", False)
        _last           = st.session_state.get(f"_bg_last_update_{_frag_vm}")
        _thread         = st.session_state.get(f"_bg_thread_{_frag_vm}")
        _fetch          = _thread is not None and _thread.is_alive()
        _ts_str         = _last.strftime("%H:%M:%S") if _last else "—"
        _ticks          = st.session_state.get(f"paper_ticks_{_frag_vm}", 0)
        _paper_results  = st.session_state.get(f"paper_results_{_frag_vm}", {})

        # Show parallel-running badge if the OTHER mode is also active
        _other_mode = "live" if _frag_vm == "sim" else "sim"
        _other_running = st.session_state.get(f"paper_running_{_other_mode}", False)
        _parallel_note = (
            f" · {'⚡ Live' if _other_mode == 'live' else '📊 Sim'} also running in background"
            if _other_running else ""
        )

        if _paper_running:
            _dot = "⟳ fetching…" if _fetch else "✓ live"
            st.caption(
                f"{_dot}  ·  Last update: {_ts_str}  "
                f"·  Tick #{_ticks}  "
                f"·  Next refresh in ~{refresh_secs}s{_parallel_note}"
            )
        elif _ticks > 0 or _paper_results:
            st.caption(f"⏸ Paused  ·  Last update: {_ts_str}  ·  Tick #{_ticks}{_parallel_note}")

        if not _paper_results:
            if not _paper_running:
                st.info("▶️ Press **Start** to begin — or **🔄 Restart** for a clean slate.")
            else:
                st.info("⏳ Fetching first tick — this takes a few seconds…")
            return

        # Separate strategy results from Kite live payload
        _sim_results = {k: v for k, v in _paper_results.items() if k != "_kite_live" and v}
        _kite_data   = _paper_results.get("_kite_live", {})

        # Correct simulator for current view (for portfolio reads / force-close)
        _cur_sim_frag = (
            st.session_state.simulator_live if _frag_live else st.session_state.simulator_sim
        )

        # Global live price lookup: symbol_ns → latest price from strategy signals
        _all_live_prices: dict = {}
        for _sr_v in _sim_results.values():
            for _sym_lp, _info_lp in _sr_v.get("signals", {}).items():
                _all_live_prices[_sym_lp] = _info_lp["price"]

        # ══════════════════════════════════════════════════
        # SECTION A: Portfolio Status
        # ══════════════════════════════════════════════════
        if _frag_live:
            st.subheader("💳 Kite Live Holdings")
            if _kite_data.get("error") and not _kite_data.get("positions"):
                st.warning(f"Kite API: {_kite_data['error']}")
            else:
                _cash = _kite_data.get("available_cash")
                _used = _kite_data.get("used_margin")
                _net  = _kite_data.get("net_balance")
                _cap  = config.LIVE_TRADING_CAP
                _kc1, _kc2, _kc3, _kc4 = st.columns(4)
                _kc1.metric("Available Cash",  f"₹{_cash:,.0f}"  if _cash is not None else "—")
                _kc2.metric("Used Margin",     f"₹{_used:,.0f}"  if _used is not None else "—")
                _kc3.metric("Net Balance",     f"₹{_net:,.0f}"   if _net  is not None else "—")
                _kc4.metric("Trading Cap",     f"₹{_cap:,.0f}")

                _kite_pos = _kite_data.get("positions", [])
                if _kite_pos:
                    st.markdown("##### 📂 Live Kite Positions")
                    _kp_rows = []
                    for _p in _kite_pos:
                        _pnl_v = _p.get("pnl", 0)
                        _kp_rows.append({
                            "Stock":       _p.get("symbol", ""),
                            "Qty":         _p.get("quantity", 0),
                            "Avg Price ₹": f"₹{_p.get('avg_price', 0):,.2f}",
                            "LTP ₹":       f"₹{_p.get('ltp', 0):,.2f}",
                            "P&L ₹":       f"₹{_pnl_v:+,.0f}",
                            "Product":     _p.get("product", "MIS"),
                        })
                    _kp_df = pd.DataFrame(_kp_rows)

                    def _style_kite_pos(df):
                        styles = pd.DataFrame("", index=df.index, columns=df.columns)
                        for i, row in df.iterrows():
                            try:
                                pnl_raw = float(str(row["P&L ₹"]).replace("₹","").replace(",","").replace("+",""))
                                col_fg  = "#145c2e" if pnl_raw >= 0 else "#8b1a1a"
                                col_bg  = "#e6f9ee" if pnl_raw >= 0 else "#fde8e8"
                            except ValueError:
                                col_fg, col_bg = "#1e2a3a", "#f0f4fa"
                            for c in df.columns:
                                styles.loc[i, c] = f"background-color:{col_bg};color:#1e2a3a"
                            styles.loc[i, "P&L ₹"] = (
                                f"background-color:{col_bg};color:{col_fg};font-weight:700"
                            )
                        return styles

                    st.dataframe(
                        _kp_df.style.apply(_style_kite_pos, axis=None),
                        width="stretch",
                    )
                else:
                    st.info("No open Kite positions right now.")

            st.divider()
            with st.expander("📊 Simulation Reference Portfolio", expanded=False):
                for _strat, _res in _sim_results.items():
                    _p    = _res["portfolio"]
                    _col  = "#145c2e" if _p["pnl"] >= 0 else "#8b1a1a"
                    st.markdown(
                        f"**{STRATEGY_MAP[_strat].name}** — "
                        f"Cash ₹{_p['cash']:,.0f} · Equity ₹{_p['equity']:,.0f} · "
                        f"<span style='color:{_col}'>P&L ₹{_p['pnl']:+,.0f}</span>",
                        unsafe_allow_html=True,
                    )
        else:
            # ─── Sim: Shared-capital Portfolio Status ────────
            st.subheader("📊 Portfolio Status")

            _total_capital    = float(config.CAPITAL)
            _strat_breakdown  = []
            _total_in_order   = 0.0
            _total_unrealized = 0.0
            _total_realized   = 0.0
            _all_bot_orders_ps = _bot_orders.get_all_orders(_frag_mode)

            for _strat, _res in _sim_results.items():
                _port    = _cur_sim_frag.get_paper_portfolio(_strat)
                _in_order = 0.0
                _unreal   = 0.0
                if _port and _port.positions:
                    for _sym, _pos in _port.positions.items():
                        _in_order += _pos.entry_price * _pos.quantity
                        _lp = _all_live_prices.get(_sym, _pos.entry_price)
                        _unreal += (_lp - _pos.entry_price) * _pos.quantity
                _realized = sum(
                    o.get("pnl") or 0
                    for o in _all_bot_orders_ps
                    if o.get("strategy") == _strat and o.get("status") == "EXECUTED"
                )
                _total_in_order   += _in_order
                _total_unrealized += _unreal
                _total_realized   += _realized
                _strat_breakdown.append({
                    "strat":      _strat,
                    "name":       STRATEGY_MAP[_strat].name,
                    "in_order":   _in_order,
                    "n_open":     _res["portfolio"]["positions"],
                    "unrealized": _unreal,
                    "realized":   _realized,
                })

            _free_capital = _total_capital - _total_in_order

            _am1, _am2, _am3, _am4 = st.columns(4)
            _am1.metric("Total Capital", f"₹{_total_capital:,.0f}")
            _am2.metric(
                "In-Order Capital",
                f"₹{_total_in_order:,.0f}",
                delta=(
                    f"{_total_in_order / _total_capital * 100:.1f}% deployed"
                    if _total_capital > 0 else "0%"
                ),
            )
            _am3.metric("Free Capital", f"₹{_free_capital:,.0f}")
            _pnl_total = _total_realized + _total_unrealized
            _am4.metric(
                "Total P&L",
                f"₹{_pnl_total:+,.0f}",
                delta=f"₹{_total_realized:+,.0f} realized  ·  ₹{_total_unrealized:+,.0f} open",
            )

            st.divider()

            # Per-strategy breakdown
            _BW = [3, 2, 2, 1, 2, 2]
            _bhdrs = st.columns(_BW)
            for _bh, _bl in zip(
                _bhdrs,
                ["Strategy", "In-Order ₹", "In-Order %", "Open", "Unrealized P&L", "Realized P&L"],
            ):
                _bh.markdown(
                    f"<span style='font-weight:700;color:#1a4f8a;font-size:0.88rem'>{_bl}</span>",
                    unsafe_allow_html=True,
                )
            st.markdown("<hr class='tbl-sep'>", unsafe_allow_html=True)
            for _sd in _strat_breakdown:
                _pct = (_sd["in_order"] / _total_capital * 100) if _total_capital > 0 else 0.0
                _br  = st.columns(_BW)
                _br[0].write(_sd["name"])
                _br[1].write(f"₹{_sd['in_order']:,.0f}")
                _br[2].write(f"{_pct:.1f}%")
                _br[3].write(str(_sd["n_open"]))
                _ucol = "#145c2e" if _sd["unrealized"] >= 0 else "#8b1a1a"
                _br[4].markdown(
                    f"<span style='color:{_ucol};font-weight:700'>₹{_sd['unrealized']:+,.0f}</span>",
                    unsafe_allow_html=True,
                )
                _rcol = "#145c2e" if _sd["realized"] >= 0 else "#8b1a1a"
                _br[5].markdown(
                    f"<span style='color:{_rcol};font-weight:700'>₹{_sd['realized']:+,.0f}</span>",
                    unsafe_allow_html=True,
                )

        # ══════════════════════════════════════════════════
        # SECTION B: Bot Orders & Positions (merged)
        # ══════════════════════════════════════════════════
        st.divider()
        st.subheader("📋 Bot Orders & Positions")

        _bf1, _bf2, _bf3 = st.columns(3)
        _ord_status = _bf1.selectbox(
            "Status", ["All", "OPEN", "EXECUTED", "CANCELLED"], key="ord_status_sel",
        )
        _ord_time = _bf2.selectbox(
            "Placed within",
            ["All time", "Last 1 hour", "Last 6 hours", "Today", "Last 7 days"],
            key="ord_time_sel",
        )
        _ord_strat = _bf3.selectbox(
            "Strategy",
            ["All"] + list(STRATEGY_MAP.keys()),
            format_func=lambda x: "All Strategies" if x == "All" else STRATEGY_MAP[x].name,
            key="ord_strat_sel",
        )

        _all_orders = _bot_orders.get_all_orders(_frag_mode)
        _now = datetime.now()
        _time_cuts = {
            "Last 1 hour":  _now - timedelta(hours=1),
            "Last 6 hours": _now - timedelta(hours=6),
            "Today":        _now.replace(hour=0, minute=0, second=0, microsecond=0),
            "Last 7 days":  _now - timedelta(days=7),
            "All time":     None,
        }
        _tcut = _time_cuts.get(_ord_time)
        if _tcut:
            _all_orders = [
                o for o in _all_orders
                if o.get("placed_at") and datetime.fromisoformat(o["placed_at"]) >= _tcut
            ]
        if _ord_status != "All":
            _all_orders = [o for o in _all_orders if o.get("status") == _ord_status]
        if _ord_strat != "All":
            _all_orders = [o for o in _all_orders if o.get("strategy") == _ord_strat]

        # Portfolio positions keyed by (strat, sym_ns) for live-price enrichment
        _port_positions: dict = {}
        for _strat_k in _strategies:
            _port_k = _cur_sim_frag.get_paper_portfolio(_strat_k)
            if _port_k:
                for _sym_k, _pos_k in _port_k.positions.items():
                    _port_positions[(_strat_k, _sym_k)] = _pos_k

        if not _all_orders:
            st.info("No bot orders match the selected filters.")
        else:
            _TW = [2, 2, 1, 2, 2, 2, 2, 2, 1, 2, 2]
            _HDRS = [
                "Stock", "Strategy", "Qty", "Entry ₹", "Curr / Exit ₹",
                "P&L ₹", "Stop Loss ₹", "Target ₹", "Age", "Status", "Action",
            ]
            _hrow = st.columns(_TW)
            for _hcol, _hlbl in zip(_hrow, _HDRS):
                _hcol.markdown(
                    f"<span style='font-weight:700;color:#1a4f8a;font-size:0.85rem'>{_hlbl}</span>",
                    unsafe_allow_html=True,
                )
            st.markdown("<hr class='tbl-sep'>", unsafe_allow_html=True)

            for _o in _all_orders:
                _sym_ns    = _o.get("symbol", "")
                _sym_bare  = _sym_ns.replace(".NS", "")
                _strat_k   = _o.get("strategy", "")
                _strat_lbl = (
                    STRATEGY_MAP[_strat_k].name
                    if _strat_k in STRATEGY_MAP else (_strat_k or "—")
                )
                _qty     = _o.get("quantity", "—")
                _entry_p = _o.get("entry_price")
                _status  = _o.get("status", "—")
                _age     = _bot_orders.age_str(_o.get("placed_at"))
                _oid     = _o.get("order_id", "")

                _pos_obj = _port_positions.get((_strat_k, _sym_ns))
                if _status == "OPEN" and _pos_obj:
                    _lp     = _all_live_prices.get(_sym_ns, _pos_obj.entry_price)
                    _pnl_v  = (_lp - _pos_obj.entry_price) * _pos_obj.quantity
                    _curr_s = f"₹{_lp:,.2f}"
                    _sl_s   = f"₹{_pos_obj.stop_loss:,.2f}" if _pos_obj.stop_loss else "—"
                    _tgt_s  = f"₹{_pos_obj.target:,.2f}"   if _pos_obj.target    else "—"
                else:
                    _exit_p = _o.get("exit_price")
                    _pnl_v  = _o.get("pnl")
                    _curr_s = f"₹{_exit_p:,.2f}" if _exit_p is not None else "—"
                    _sl_s   = "—"
                    _tgt_s  = "—"

                _pnl_col = "#145c2e" if (_pnl_v is not None and _pnl_v >= 0) else "#8b1a1a"
                _pnl_md  = (
                    f"<span style='color:{_pnl_col};font-weight:700'>₹{_pnl_v:+,.0f}</span>"
                    if _pnl_v is not None else "—"
                )
                _fg_st, _bg_st = {
                    "OPEN":      ("#1a4f8a", "#dce8f8"),
                    "EXECUTED":  ("#145c2e", "#e6f9ee"),
                    "CANCELLED": ("#7a4800", "#fff3d4"),
                    "REJECTED":  ("#8b1a1a", "#fde8e8"),
                }.get(_status, ("#1e2a3a", "#f0f4fa"))
                _status_md = (
                    f"<span style='color:{_fg_st};background:{_bg_st};"
                    f"font-weight:700;padding:2px 7px;border-radius:4px;"
                    f"font-size:0.82rem'>{_status}</span>"
                )

                _row = st.columns(_TW)
                _row[0].write(_sym_bare)
                _row[1].write(_strat_lbl)
                _row[2].write(_qty)
                _row[3].write(f"₹{_entry_p:,.2f}" if _entry_p is not None else "—")
                _row[4].write(_curr_s)
                _row[5].markdown(_pnl_md, unsafe_allow_html=True)
                _row[6].write(_sl_s)
                _row[7].write(_tgt_s)
                _row[8].write(_age)
                _row[9].markdown(_status_md, unsafe_allow_html=True)

                if _status == "OPEN":
                    if _row[10].button("✕ Close", key=f"close_{_oid}_{_strat_k}", type="secondary"):
                        if _frag_mode == "sim":
                            if _cur_sim_frag.force_close_position(_strat_k, _sym_ns):
                                st.success(f"Closed {_sym_bare}")
                                st.rerun()
                            else:
                                st.error("Close failed — position may already be closed.")
                        else:
                            _kt2 = st.session_state.get("_kite_trader")
                            if _pos_obj and _kt2:
                                _sell_id = _kt2.place_sell_order(_sym_bare, _pos_obj.quantity)
                                if _sell_id:
                                    _cur_sim_frag.force_close_position(_strat_k, _sym_ns)
                                    st.success(f"SELL placed: {_sym_bare} ×{_pos_obj.quantity}")
                                    st.rerun()
                                else:
                                    st.error("Sell order failed — check logs.")
                            elif not _kt2:
                                st.error("Not connected to Kite.")
                else:
                    _row[10].write("")

        # ══════════════════════════════════════════════════
        # SECTION C: Equity Curves
        # ══════════════════════════════════════════════════
        st.divider()
        st.subheader("📈 Equity Curves")
        _fig_eq = go.Figure()
        for _strat in _strategies:
            _eq_df = _cur_sim_frag.get_equity_curve_df(_strat)
            if not _eq_df.empty:
                _fig_eq.add_scatter(
                    x=_eq_df["timestamp"], y=_eq_df["equity"],
                    name=STRATEGY_MAP[_strat].name, mode="lines",
                )
        _fig_eq.add_hline(
            y=config.CAPITAL, line_dash="dash", line_color="#888",
            annotation_text="Starting Capital",
        )
        _fig_eq.update_layout(
            title="Equity Curves (Simulation)",
            xaxis_title="Time", yaxis_title="Portfolio Value (₹)",
            legend=dict(orientation="h"),
            paper_bgcolor="#eef2f8", plot_bgcolor="#f8fbff",
            font=dict(color="#1e2a3a"),
        )
        st.plotly_chart(_fig_eq, use_container_width=True)

    _paper_live_view()


# ──────────────────────────────────────────────────────────
# PAGE: OVERVIEW
# ──────────────────────────────────────────────────────────
elif _page == "overview":
    st.header("📊 Portfolio Overview")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Starting Capital", f"₹{config.CAPITAL:,.0f}")

    _ov_results = st.session_state.get(f"paper_results_{_vm}", {})
    _ov_ticks   = st.session_state.get(f"paper_ticks_{_vm}", 0)
    if _ov_results:
        total_pnl = sum(
            r["portfolio"]["pnl"]
            for r in _ov_results.values()
            if r and "portfolio" in r
        )
        avg_equity = sum(
            r["portfolio"]["equity"]
            for r in _ov_results.values()
            if r and "portfolio" in r
        ) / max(len([r for r in _ov_results.values() if r and "portfolio" in r]), 1)
        col2.metric("Current Equity", f"₹{avg_equity:,.0f}")
        col3.metric("Total P&L", f"₹{total_pnl:+,.0f}",
                    delta=f"{total_pnl / config.CAPITAL * 100:+.2f}%")
        col4.metric("Paper Ticks", _ov_ticks)
    else:
        col2.metric("Current Equity", f"₹{config.CAPITAL:,.0f}")
        col3.metric("Total P&L", "₹0.00")
        col4.metric("Paper Ticks", "0")

    st.info(
        "💡 Start the **📡 Trading** page to see live simulation results here. "
        "Run **📈 Backtest** from the sidebar to compare strategies on historical data."
    )

    if st.session_state.backtest_results is not None:
        st.subheader("Strategy Comparison (Backtest)")
        df = st.session_state.backtest_results
        if not df.empty:
            fig = px.bar(
                df, x="Strategy", y="Total Return (%)",
                color="Total Return (%)",
                color_continuous_scale=["red", "yellow", "green"],
                text="Total Return (%)",
                title=f"Strategy Returns on ₹{config.CAPITAL:,.0f} over {config.BACKTEST_PERIOD_DAYS} days",
            )
            fig.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
            fig.update_layout(paper_bgcolor="#eef2f8", plot_bgcolor="#f8fbff", font=dict(color="#1e2a3a"))
            st.plotly_chart(fig)
            st.dataframe(df, width="stretch")


# ──────────────────────────────────────────────────────────
# PAGE: HISTORY
# ──────────────────────────────────────────────────────────
elif _page == "history":
    st.header("📅 Historical Performance")

    fcol1, fcol2, fcol3 = st.columns(3)
    _hist_mode_key = _vm  # follows the global mode toggle
    _hist_label    = "⚡ Live Trades" if _vm == "live" else "📊 Simulation Trades"
    st.caption(f"Showing: {_hist_label}")

    hist_trades = trade_store.load_all_trade_history(mode=_hist_mode_key)

    if not hist_trades:
        st.info("No historical trade data found. Run the simulation first.")
    else:
        hist_df = pd.DataFrame(hist_trades)
        hist_df["pnl"]        = pd.to_numeric(hist_df.get("pnl", 0), errors="coerce").fillna(0)
        hist_df["entry_date"] = pd.to_datetime(hist_df.get("entry_date"), errors="coerce")
        hist_df["exit_date"]  = pd.to_datetime(hist_df.get("exit_date"),  errors="coerce")
        hist_df["pnl_pct"]    = pd.to_numeric(hist_df.get("pnl_pct", 0), errors="coerce").fillna(0)
        hist_df["symbol_clean"] = hist_df.get("symbol", "").str.replace(".NS", "", regex=False)

        # ── Date range filter ────────────────────────────────
        _min_d = hist_df["exit_date"].dropna().min()
        _max_d = hist_df["exit_date"].dropna().max()
        if pd.notna(_min_d) and pd.notna(_max_d):
            _from_d = fcol1.date_input("From", value=_min_d.date(), key="hist_from")
            _to_d   = fcol2.date_input("To",   value=_max_d.date(), key="hist_to")
            hist_df = hist_df[
                (hist_df["exit_date"].dt.date >= _from_d) &
                (hist_df["exit_date"].dt.date <= _to_d)
            ]

        # ── Strategy filter ──────────────────────────────────
        _strat_choices = ["All"] + sorted(hist_df["strategy"].dropna().unique().tolist())
        _strat_filter  = fcol3.selectbox(
            "Strategy", _strat_choices,
            format_func=lambda x: "All" if x == "All" else (
                STRATEGY_MAP[x].name if x in STRATEGY_MAP else x
            ),
            key="hist_strat_filter",
        )
        if _strat_filter != "All":
            hist_df = hist_df[hist_df["strategy"] == _strat_filter]

        if hist_df.empty:
            st.warning("No trades in selected range.")
        else:
            # ── Summary metrics ──────────────────────────────
            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric("Total Trades",   len(hist_df))
            sm2.metric("Win Rate",       f"{(hist_df['pnl'] > 0).mean() * 100:.1f}%")
            sm3.metric("Total P&L",      f"₹{hist_df['pnl'].sum():+,.0f}")
            sm4.metric("Best Trade",     f"₹{hist_df['pnl'].max():+,.0f}")

            st.divider()

            # ── Daily P&L bar chart ──────────────────────────
            st.subheader("📆 Daily P&L")
            daily_pnl = (
                hist_df.groupby(hist_df["exit_date"].dt.date)["pnl"]
                .sum().reset_index()
                .rename(columns={"exit_date": "Date", "pnl": "P&L (₹)"})
            )
            daily_pnl["colour"] = daily_pnl["P&L (₹)"].apply(
                lambda v: "#50e880" if v >= 0 else "#ff6060"
            )
            fig_daily = go.Figure(go.Bar(
                x=daily_pnl["Date"], y=daily_pnl["P&L (₹)"],
                marker_color=daily_pnl["colour"],
                text=daily_pnl["P&L (₹)"].apply(lambda v: f"₹{v:+,.0f}"),
                textposition="outside",
            ))
            fig_daily.update_layout(
                title="Daily P&L", xaxis_title="Date", yaxis_title="P&L (₹)",
                paper_bgcolor="#eef2f8", plot_bgcolor="#f8fbff", font=dict(color="#1e2a3a"),
            )
            st.plotly_chart(fig_daily, use_container_width=True)

            # ── Cumulative P&L ───────────────────────────────
            daily_sorted = daily_pnl.sort_values("Date")
            daily_sorted["Cumulative P&L (₹)"] = daily_sorted["P&L (₹)"].cumsum()
            fig_cum = go.Figure(go.Scatter(
                x=daily_sorted["Date"], y=daily_sorted["Cumulative P&L (₹)"],
                mode="lines+markers", line=dict(color="#f0b429", width=2),
                marker=dict(size=6), fill="tozeroy", fillcolor="rgba(240,180,41,0.12)",
            ))
            fig_cum.add_hline(y=0, line_dash="dash", line_color="#555")
            fig_cum.update_layout(
                title="Cumulative P&L", xaxis_title="Date", yaxis_title="Cumulative P&L (₹)",
                paper_bgcolor="#eef2f8", plot_bgcolor="#f8fbff", font=dict(color="#1e2a3a"),
            )
            st.plotly_chart(fig_cum, use_container_width=True)

            st.divider()
            st.subheader("📊 Performance by Stock")
            by_stock = (
                hist_df.groupby("symbol_clean")
                .agg(
                    Trades      = ("pnl", "count"),
                    Wins        = ("pnl", lambda s: (s > 0).sum()),
                    Total_PnL   = ("pnl", "sum"),
                    Avg_PnL     = ("pnl", "mean"),
                    Best_Trade  = ("pnl", "max"),
                    Worst_Trade = ("pnl", "min"),
                )
                .reset_index()
                .rename(columns={
                    "symbol_clean": "Stock",
                    "Total_PnL":    "Total P&L (₹)",
                    "Avg_PnL":      "Avg P&L (₹)",
                    "Best_Trade":   "Best Trade (₹)",
                    "Worst_Trade":  "Worst Trade (₹)",
                })
            )
            by_stock["Win Rate %"] = (by_stock["Wins"] / by_stock["Trades"] * 100).round(1)
            by_stock = by_stock.sort_values("Total P&L (₹)", ascending=False)
            float_cols_stock = ["Total P&L (₹)", "Avg P&L (₹)", "Best Trade (₹)", "Worst Trade (₹)"]
            st.dataframe(
                by_stock.style
                .apply(_rdylgn, subset=["Total P&L (₹)"])
                .format({c: "₹{:+,.2f}" for c in float_cols_stock} | {"Win Rate %": "{:.1f}%"}),
                width="stretch", height=400,
            )

            st.divider()
            st.subheader("📊 Performance by Strategy")
            by_strat = (
                hist_df.groupby("strategy")
                .agg(Trades=("pnl","count"), Wins=("pnl", lambda s:(s>0).sum()),
                     Total_PnL=("pnl","sum"), Avg_PnL=("pnl","mean"))
                .reset_index()
                .rename(columns={"strategy":"Strategy","Total_PnL":"Total P&L (₹)","Avg_PnL":"Avg P&L (₹)"})
            )
            by_strat["Win Rate %"] = (by_strat["Wins"] / by_strat["Trades"] * 100).round(1)
            by_strat["Strategy"]   = by_strat["Strategy"].apply(
                lambda x: STRATEGY_MAP[x].name if x in STRATEGY_MAP else x
            )
            st.dataframe(
                by_strat.style
                .apply(_rdylgn, subset=["Total P&L (₹)"])
                .format({"Total P&L (₹)":"₹{:+,.2f}","Avg P&L (₹)":"₹{:+,.2f}","Win Rate %":"{:.1f}%"}),
                width="stretch",
            )

            st.divider()
            with st.expander("📋 Full Trade Log", expanded=False):
                display_cols = ["symbol_clean","strategy","entry_date","exit_date",
                                "entry_price","exit_price","quantity","pnl","pnl_pct","exit_reason"]
                display_cols = [c for c in display_cols if c in hist_df.columns]
                log_df = hist_df[display_cols].copy().rename(columns={
                    "symbol_clean":"Stock","strategy":"Strategy",
                    "entry_date":"Entry Date","exit_date":"Exit Date",
                    "entry_price":"Entry ₹","exit_price":"Exit ₹",
                    "quantity":"Qty","pnl":"P&L ₹","pnl_pct":"P&L %","exit_reason":"Exit Reason",
                })
                log_df["Strategy"]   = log_df["Strategy"].apply(
                    lambda x: STRATEGY_MAP[x].name if x in STRATEGY_MAP else x
                )
                log_df["Entry Date"] = log_df["Entry Date"].dt.strftime("%Y-%m-%d")
                log_df["Exit Date"]  = log_df["Exit Date"].dt.strftime("%Y-%m-%d")
                st.dataframe(
                    log_df.sort_values("Exit Date", ascending=False)
                    .style.apply(_rdylgn, subset=["P&L ₹"])
                    .format({"Entry ₹":"₹{:,.2f}","Exit ₹":"₹{:,.2f}",
                             "P&L ₹":"₹{:+,.2f}","P&L %":"{:+.2f}%"}),
                    width="stretch", height=500,
                )


# ──────────────────────────────────────────────────────────
# PAGE: SCREENER
# ──────────────────────────────────────────────────────────
elif _page == "screener":
    st.header("🔍 NSE 300 Momentum Screener")
    st.caption("Scans all NSE 300 stocks and ranks them by momentum score")

    col1, col2 = st.columns([1, 3])
    top_n = col1.slider("Top N stocks", 3, 20, min(config.TOP_N_STOCKS, 20))
    config.TOP_N_STOCKS = top_n

    if col2.button("🔄 Scan NSE 300", type="primary"):
        with st.spinner("Scanning 300 NSE stocks… this takes ~60 seconds"):
            selected, summary = selector.refresh_selection()
            st.session_state.selected_stocks = (selected, summary)
        st.success(f"Found top {len(selected)} momentum stocks!")

    if st.session_state.selected_stocks:
        selected, summary = st.session_state.selected_stocks
        st.subheader(f"Top {len(selected)} Momentum Stocks")

        _cap_colours = {
            "Large Cap": "background-color:#e4f4ea;color:#145c2e;font-weight:600",
            "Mid Cap":   "background-color:#e4eef8;color:#1a4f8a;font-weight:600",
            "Small Cap": "background-color:#fef4e4;color:#7a4800;font-weight:600",
        }

        def _colour_cap(series):
            return [_cap_colours.get(v, "") for v in series]

        styled = summary.style.apply(
            _rdylgn, subset=["Score", "Momentum (%)"]
        ).apply(_colour_cap, subset=["Cap"]).format({
            "Score": "{:.2f}", "Momentum (%)": "{:+.2f}%",
            "Volatility (%)": "{:.1f}%", "Price (₹)": "₹{:,.2f}",
        })
        st.dataframe(styled, width="stretch", height=500)

        fig = px.bar(
            summary, x="Symbol", y="Momentum (%)", color="Cap",
            color_discrete_map={
                "Large Cap": "#6fcf6f", "Mid Cap": "#6f9fcf", "Small Cap": "#cf9f6f",
            },
            title=f"Momentum (%) — Top {len(selected)} Stocks by Cap Tier",
        )
        fig.update_layout(paper_bgcolor="#eef2f8", plot_bgcolor="#f8fbff", font=dict(color="#1e2a3a"))
        st.plotly_chart(fig)

        st.subheader("Current Signals")
        strategy_choice = st.selectbox(
            "Strategy", list(STRATEGY_MAP.keys()),
            format_func=lambda x: STRATEGY_MAP[x].name,
            key="screener_strat",
        )
        StrategyClass = STRATEGY_MAP[strategy_choice]
        strategy_obj  = StrategyClass()
        sig_rows = []
        for stock in selected:
            df_s = stock.get("data")
            if df_s is not None and not df_s.empty:
                sig = strategy_obj.get_current_signal(df_s)
                sig_rows.append({
                    "Stock": stock["symbol"].replace(".NS",""),
                    "Signal": sig, "Price ₹": stock["current_price"],
                    "Momentum %": stock["momentum_pct"],
                })
        if sig_rows:
            st.dataframe(pd.DataFrame(sig_rows), width="stretch")
    else:
        st.info("Click **🔄 Scan NSE 300** to discover top momentum stocks.")


# ──────────────────────────────────────────────────────────
# PAGE: BACKTEST
# ──────────────────────────────────────────────────────────
elif _page == "backtest":
    st.header("📈 Strategy Backtester")
    st.caption(f"Tests strategies on {config.BACKTEST_PERIOD_DAYS} days of real NSE historical data")

    col1, col2, col3 = st.columns(3)
    period = col1.slider("Backtest period (days)", 90, 730, config.BACKTEST_PERIOD_DAYS, step=30)
    config.BACKTEST_PERIOD_DAYS = period

    bt_strategy = col2.selectbox(
        "Strategy to backtest",
        ["all"] + list(STRATEGY_MAP.keys()),
        format_func=lambda x: "All Strategies" if x == "all" else STRATEGY_MAP[x].name,
    )

    run_bt = col3.button("▶️ Run Backtest", type="primary")

    if run_bt:
        if bt_strategy == "all":
            with st.spinner("Running all strategies on top momentum stocks… (2–3 minutes)"):
                comparison = simulator.run_full_comparison(period)
                st.session_state.backtest_results = comparison
            st.success("Backtest complete!")
        else:
            with st.spinner(f"Running {STRATEGY_MAP[bt_strategy].name}…"):
                results = simulator.backtest_strategy_on_stocks(bt_strategy, period)
                rows = []
                for sym, res in results.items():
                    s = res.stats
                    rows.append({
                        "Symbol":          sym.replace(".NS",""),
                        "Total Trades":    s.get("total_trades", 0),
                        "Win Rate (%)":    s.get("win_rate_pct", 0),
                        "Total P&L (₹)":  s.get("total_pnl_inr", 0),
                        "Total Return (%)":s.get("total_return_pct", 0),
                        "Max Drawdown (%)":s.get("max_drawdown_pct", 0),
                    })
                if rows:
                    st.session_state.backtest_results = pd.DataFrame(rows)
            st.success("Backtest complete!")

    if st.session_state.backtest_results is not None:
        df = st.session_state.backtest_results
        if not df.empty:
            st.subheader("Results")
            float_cols = list(df.select_dtypes("float").columns)
            styled_bt  = df.style.format({c: "{:.2f}" for c in float_cols})
            if "Total Return (%)" in df.columns:
                styled_bt = styled_bt.apply(_rdylgn, subset=["Total Return (%)"])
            st.dataframe(styled_bt, width="stretch")

            if "Strategy" in df.columns:
                fig = go.Figure()
                for _, row in df.iterrows():
                    fig.add_bar(
                        name=row["Strategy"], x=[row["Strategy"]],
                        y=[row["Total Return (%)"]],
                        text=[f"{row['Total Return (%)']:.2f}%"], textposition="outside",
                    )
                fig.update_layout(
                    title="Strategy Comparison — Total Return %", showlegend=False,
                    paper_bgcolor="#eef2f8", plot_bgcolor="#f8fbff", font=dict(color="#1e2a3a"),
                )
                st.plotly_chart(fig)

                best = df.iloc[0]
                c1, c2, c3 = st.columns(3)
                c1.metric("🏆 Best Strategy", best.get("Strategy", "—"))
                c2.metric("Best Return",      f"{best.get('Total Return (%)', 0):.2f}%")
                c3.metric("Win Rate",         f"{best.get('Win Rate (%)', 0):.1f}%")


# ──────────────────────────────────────────────────────────
# PAGE: SETTINGS
# ──────────────────────────────────────────────────────────
elif _page == "settings":
    _settings_mode_label = "⚡ Live" if _vm == "live" else "📊 Simulation"
    st.header(f"⚙️ Settings — {_settings_mode_label} Mode")
    st.caption(
        "All settings here apply **only to the current mode**. "
        "Toggle the switch in the sidebar to configure the other mode independently."
    )

    # Helper: read from per-mode store (already applied to config by _apply_mode_config)
    # Helper: write to both config module AND the per-mode store
    def _cfg(key):
        return st.session_state.get(f"config_{_vm}", {}).get(key, getattr(config, key))

    def _set(key, val):
        setattr(config, key, val)
        st.session_state.setdefault(f"config_{_vm}", {})[key] = val

    # ── Key suffix so sim and live sliders are independent Streamlit widgets ──
    _k = _vm  # e.g. "sim" or "live"

    try:
        with st.expander("💰 Capital", expanded=True):
            st.caption("Starting capital for this mode. Takes effect on next Restart.")
            _cap = st.number_input(
                "Starting Capital (₹)",
                min_value=10_000, max_value=10_000_000,
                value=max(10_000, min(10_000_000, int(_cfg("CAPITAL")))),
                step=10_000,
                key=f"cfg_capital_{_k}",
            )
            _set("CAPITAL", float(_cap))
    except Exception as _e:
        st.error(f"Capital error: {_e}")

    try:
        with st.expander("📉 Risk Management", expanded=True):
            col1, col2 = st.columns(2)
            _sl  = col1.slider("Stop Loss %",        0.5, 5.0,  max(0.5, min(5.0,  round(_cfg("STOP_LOSS_PCT")     * 100, 1))), step=0.1, key=f"cfg_sl_{_k}")
            _tp  = col2.slider("Take Profit %",      1.0, 10.0, max(1.0, min(10.0, round(_cfg("TARGET_PCT")         * 100, 1))), step=0.5, key=f"cfg_tp_{_k}")
            _ts  = col1.slider("Trailing Stop %",    0.5, 3.0,  max(0.5, min(3.0,  round(_cfg("TRAILING_STOP_PCT") * 100, 1))), step=0.1, key=f"cfg_ts_{_k}")
            _mop = col2.slider("Max Open Positions", 1,   10,   max(1,   min(10,   _cfg("MAX_OPEN_POSITIONS"))),                             key=f"cfg_mop_{_k}")
            _mps = col1.slider("Max Position Size %",5,   50,   max(5,   min(50,   int(_cfg("MAX_POSITION_PCT") * 100))),         step=5,   key=f"cfg_mps_{_k}")
            _set("STOP_LOSS_PCT",     _sl  / 100)
            _set("TARGET_PCT",        _tp  / 100)
            _set("TRAILING_STOP_PCT", _ts  / 100)
            _set("MAX_OPEN_POSITIONS",_mop)
            _set("MAX_POSITION_PCT",  _mps / 100)
    except Exception as _e:
        st.error(f"Risk Management error: {_e}")

    try:
        with st.expander("📊 Strategy Parameters", expanded=False):
            st.subheader("Moving Average")
            col1, col2 = st.columns(2)
            _ma_s = col1.slider("Short MA Period", 5,  30,  max(5,  min(30,  _cfg("MA_SHORT_PERIOD"))), key=f"cfg_mas_{_k}")
            _ma_l = col2.slider("Long MA Period",  20, 100, max(20, min(100, _cfg("MA_LONG_PERIOD"))),  key=f"cfg_mal_{_k}")
            _set("MA_SHORT_PERIOD", _ma_s)
            _set("MA_LONG_PERIOD",  _ma_l)

            st.subheader("RSI + MACD")
            col1, col2 = st.columns(2)
            _rsi_p  = col1.slider("RSI Period",     7,  21, max(7,  min(21, _cfg("RSI_PERIOD"))),     key=f"cfg_rsip_{_k}")
            _rsi_os = col1.slider("RSI Oversold",   20, 40, max(20, min(40, _cfg("RSI_OVERSOLD"))),   key=f"cfg_rsios_{_k}")
            _rsi_ob = col2.slider("RSI Overbought", 60, 80, max(60, min(80, _cfg("RSI_OVERBOUGHT"))), key=f"cfg_rsiob_{_k}")
            _set("RSI_PERIOD",     _rsi_p)
            _set("RSI_OVERSOLD",   _rsi_os)
            _set("RSI_OVERBOUGHT", _rsi_ob)

            st.subheader("Momentum")
            _ml = st.slider("Lookback Days",         10, 60,  max(10, min(60,  _cfg("MOMENTUM_LOOKBACK"))), key=f"cfg_ml_{_k}")
            _tn = st.slider("Top N Stocks to Trade", 3,  100, max(3,  min(100, _cfg("TOP_N_STOCKS"))),      key=f"cfg_tn_{_k}")
            _set("MOMENTUM_LOOKBACK", _ml)
            _set("TOP_N_STOCKS",      _tn)
    except Exception as _e:
        st.error(f"Strategy Parameters error: {_e}")

    if _vm == "live":
        try:
            with st.expander("💳 Live Trading Cap", expanded=True):
                st.caption("Maximum capital this bot may deploy. Overrides account balance.")
                _ltc = st.number_input(
                    "Live Trading Cap (₹)",
                    min_value=10_000, max_value=10_000_000,
                    value=max(10_000, int(_cfg("LIVE_TRADING_CAP"))),
                    step=10_000, key=f"cfg_ltc_{_k}",
                )
                _set("LIVE_TRADING_CAP", float(_ltc))
        except Exception as _e:
            st.error(f"Live Trading Cap error: {_e}")

        try:
            with st.expander("🔑 Zerodha API Credentials", expanded=True):
                st.warning("These credentials are required for real order placement.")
                _api_key  = st.text_input("API Key",              value=config.ZERODHA_API_KEY,      key=f"cfg_apikey_{_k}")
                _api_sec  = st.text_input("API Secret",           value=config.ZERODHA_API_SECRET,   type="password", key=f"cfg_apisec_{_k}")
                _api_tok  = st.text_input("Access Token (daily)", value=config.ZERODHA_ACCESS_TOKEN, type="password", key=f"cfg_apitok_{_k}")
                if st.button("💾 Save API Credentials", key=f"save_api_{_k}"):
                    config.ZERODHA_API_KEY      = _api_key
                    config.ZERODHA_API_SECRET   = _api_sec
                    config.ZERODHA_ACCESS_TOKEN = _api_tok
                    st.success("✅ Saved for this session. Edit config.py to make permanent.")
                st.markdown("""
**How to get credentials:**
1. [developers.kite.trade](https://developers.kite.trade/) → create an app (~₹2000/month)
2. Each morning: `python zerodha_trader.py --generate-token`
3. Paste the fresh Access Token here before starting the bot.
                """)
        except Exception as _e:
            st.error(f"Zerodha API error: {_e}")
    else:
        st.info("🔑 Zerodha API credentials and Live Trading Cap are only shown in **⚡ Live** mode. Toggle the switch in the sidebar to configure them.")

    try:
        with st.expander("📊 Dashboard", expanded=False):
            _drs = st.slider(
                "Default refresh interval (seconds)", 10, 300,
                max(10, min(300, _cfg("DASHBOARD_REFRESH_SECONDS"))),
                step=10, key=f"cfg_drs_{_k}",
            )
            _set("DASHBOARD_REFRESH_SECONDS", _drs)
    except Exception as _e:
        st.error(f"Dashboard settings error: {_e}")

    try:
        with st.expander("⚠️ Important Disclaimers", expanded=False):
            st.error("""
**Risk Warning — Please Read**

- Day trading involves **substantial risk of loss**. You may lose more than you invest.
- This bot is a tool — **not financial advice**. Past backtested performance does not guarantee future results.
- SEBI regulations apply to automated trading in India. Consult a SEBI-registered advisor before live trading.
- Always start with **simulation mode** and run backtests before risking real money.
- The bot cannot guarantee profits. Markets can move against any strategy.
- The author bears no responsibility for any financial losses.
            """)
    except Exception as _e:
        st.error(f"Disclaimers section error: {_e}")


# ──────────────────────────────────────────────────────────
# Footer (shown on every page)
# ──────────────────────────────────────────────────────────
st.markdown(
    f"""
    <div class="footer">
        <div class="copy">📈 Yash's Trading Bot</div>
        NSE · NSE 300 · India Day Trading Simulation<br>
        <span style="color:#8b6a00;font-weight:600">
        ⚠️ Use at your own risk &nbsp;—&nbsp;
        Always run simulation first before using real money &nbsp;—&nbsp;
        Not financial advice &nbsp;—&nbsp;
        Past performance does not guarantee future results
        </span><br>
        &copy; {datetime.now().year} Yash. All rights reserved. &nbsp;·&nbsp;
        Built for personal use on local infrastructure.
    </div>
    """,
    unsafe_allow_html=True,
)
