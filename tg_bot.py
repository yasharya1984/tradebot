"""
Telegram Interface
==================
Provides both a command bot (polling) and a fire-and-forget push helper.

Commands:
  /status     – System health + all open sim positions with current TSL/BE/SL
  /simorder   – Active sim positions (dynamic stop level, phase badge, P&L)
  /simprofit  – Closed-trade report with TSL vs hard-SL exit breakdown
  /profit     – Live Kite P&L  (stub until Kite is connected)
  /order      – Recent live orders (stub until Kite is connected)

Push notifications (importable from simulator.py or anywhere):
  send_notification(text)              – generic fire-and-forget push
  notify_breakeven(symbol, entry_price) – 🛡️ risk-free trade alert
"""

import asyncio
import logging
import os
import sys
import threading
from datetime import datetime
from functools import wraps

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Make sure sibling modules are importable when this file is run directly
sys.path.insert(0, os.path.dirname(__file__))

import bot_orders
import config
import trade_store

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Credentials
# ─────────────────────────────────────────────────────────────
BOT_TOKEN  = "8792563638:AAGnC8J3PXsUWZTiXyJoHNygWbkcqs2cyK4"
MY_CHAT_ID = 5582749951   # int — compared against update.effective_user.id (also int)


# ─────────────────────────────────────────────────────────────
# Push-notification helpers  (safe to call from sync code)
# ─────────────────────────────────────────────────────────────

def send_notification(text: str) -> None:
    """
    Fire-and-forget push to MY_CHAT_ID.

    Spawns a daemon thread so it never blocks the caller.
    Safe to call from synchronous OR async code (Streamlit, simulator, etc.).
    """
    def _run() -> None:
        async def _send() -> None:
            try:
                bot = Bot(token=BOT_TOKEN)
                await bot.send_message(
                    chat_id=MY_CHAT_ID,
                    text=text,
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.error(f"Telegram push failed: {exc}")

        asyncio.run(_send())

    threading.Thread(target=_run, daemon=True).start()


def notify_breakeven(symbol: str, entry_price: float) -> None:
    """
    Push a 🛡️ risk-free alert when the bot moves SL to break-even.
    Call this from simulator.py whenever `breakeven_set` flips to True.
    """
    bare = symbol.replace(".NS", "")
    send_notification(
        f"🛡️ *Risk-Free Trade*\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 *Symbol:* `{bare}`\n"
        f"✅ Stop Loss moved to entry price: `₹{entry_price:,.2f}`\n"
        f"_Position is now protected — cannot lose \\(excl. fees\\)_"
    )


def notify_tsl_triggered(symbol: str, exit_price: float, pnl: float) -> None:
    """Push alert when a Trailing Stop fires and locks in a profit."""
    bare = symbol.replace(".NS", "")
    send_notification(
        f"📈 *Trailing Stop Triggered*\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 *Symbol:* `{bare}`\n"
        f"💰 Exit price: `₹{exit_price:,.2f}`\n"
        f"🏆 Profit locked: `+₹{pnl:,.2f}`"
    )


# ─────────────────────────────────────────────────────────────
# Security decorator
# ─────────────────────────────────────────────────────────────

def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != MY_CHAT_ID:
            logger.warning(f"Telegram: blocked unauthorized user {update.effective_user.id}")
            await update.message.reply_text("⛔ Unauthorized.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _load_open_positions(mode: str = "sim") -> dict:
    """
    Return {symbol: position_dict} for all open positions across
    every saved strategy in `mode`.  Adds `_strategy` key to each entry.
    """
    result = {}
    for strategy in trade_store.list_saved_strategies(mode):
        data = trade_store.load_portfolio(strategy, mode)
        if not data:
            continue
        for sym, pos_d in data.get("positions", {}).items():
            pos_d["_strategy"] = strategy
            result[sym] = pos_d
    return result


def _sl_label(pos: dict) -> str:
    """Return a compact label for the current effective stop level."""
    sl = pos.get("stop_loss", 0.0)
    if pos.get("tsl_active") and pos.get("trailing_stop"):
        return f"TSL ₹{pos['trailing_stop']:,.2f}"
    if pos.get("breakeven_set"):
        return f"BE  ₹{sl:,.2f}"
    return f"SL  ₹{sl:,.2f}"


def _phase_badge(pos: dict) -> str:
    """Short label for the exit-management phase a position is in."""
    if pos.get("tsl_active"):
        return "📈 TSL"
    if pos.get("breakeven_set"):
        return "🛡️ Risk-free"
    return "🔒 Initial SL"


def _fmt_pnl(v: float) -> str:
    return f"+₹{v:,.0f}" if v >= 0 else f"-₹{abs(v):,.0f}"


# ─────────────────────────────────────────────────────────────
# /status
# ─────────────────────────────────────────────────────────────

@restricted
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    System health check + open sim positions.
    Shows the *current* stop level (TSL / break-even / initial SL)
    rather than the static entry-time SL.
    """
    positions   = _load_open_positions("sim")
    last_prices = trade_store.load_last_prices("sim")
    now_str     = datetime.now().strftime("%d %b %Y · %H:%M")

    unreal = sum(
        (last_prices[s] - p["entry_price"]) * p["quantity"]
        for s, p in positions.items()
        if s in last_prices
    )

    lines = [
        "🤖 *System Status*",
        "━━━━━━━━━━━━━━",
        f"🟢 *Engine:* Running",
        f"📊 *Mode:* Simulation",
        f"🕐 *Time:* {now_str} IST",
        f"📂 *Open positions:* {len(positions)}",
        f"📊 *Unrealized P&L:* `{_fmt_pnl(unreal)}`",
        "",
    ]

    if not positions:
        lines.append("_No open positions_")
    else:
        lines.append("*Positions & Current Stops*")
        for sym, pos in positions.items():
            bare = sym.replace(".NS", "")
            ep   = pos["entry_price"]
            qty  = pos["quantity"]
            lp   = last_prices.get(sym)
            sl_lbl   = _sl_label(pos)
            ph_badge = _phase_badge(pos)

            if lp is not None:
                pnl_str  = _fmt_pnl((lp - ep) * qty)
                cmp_str  = f"₹{lp:,.2f}"
            else:
                pnl_str = "—"
                cmp_str = "—"

            lines.append(
                f"\n• *{bare}* ×{qty} @ ₹{ep:,.2f}\n"
                f"  CMP {cmp_str}  P&L {pnl_str}\n"
                f"  🎯 {sl_lbl}  {ph_badge}"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# /simorder
# ─────────────────────────────────────────────────────────────

@restricted
async def sim_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Active sim positions — shows the *current* dynamic stop (TSL / BE / SL)
    instead of the static initial stop recorded at entry time.
    """
    positions   = _load_open_positions("sim")
    last_prices = trade_store.load_last_prices("sim")

    if not positions:
        await update.message.reply_text(
            "🧪 *Active Sim Trades*\n━━━━━━━━━━━━━━\n_No open positions_",
            parse_mode="Markdown",
        )
        return

    lines = ["🧪 *Active Sim Trades*", "━━━━━━━━━━━━━━"]

    for sym, pos in positions.items():
        bare    = sym.replace(".NS", "")
        ep      = pos["entry_price"]
        qty     = pos["quantity"]
        strat   = pos.get("_strategy", "?").upper()
        lp      = last_prices.get(sym)
        sl_lbl  = _sl_label(pos)
        ph      = _phase_badge(pos)

        if lp is not None:
            pnl     = (lp - ep) * qty
            pnl_str = _fmt_pnl(pnl)
            cmp_str = f"₹{lp:,.2f}"
        else:
            pnl_str = "—"
            cmp_str = "—  _(price unavailable)_"

        lines.append(
            f"\n*{bare}* `[{strat}]` ×{qty}\n"
            f"  Entry ₹{ep:,.2f}  →  CMP {cmp_str}\n"
            f"  P&L {pnl_str}\n"
            f"  🎯 {sl_lbl}  —  {ph}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# /simprofit
# ─────────────────────────────────────────────────────────────

@restricted
async def sim_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Closed-trade P&L summary with an exit-type breakdown:
      Trailing Stop (TSL) vs hard Stop Loss vs Strategy Signal vs Manual.
    """
    orders   = bot_orders.get_all_orders("sim")
    executed = [o for o in orders if o.get("status") == "EXECUTED"]

    total_pnl = sum(o.get("pnl") or 0.0 for o in executed)
    wins      = [o for o in executed if (o.get("pnl") or 0.0) > 0]
    losses    = [o for o in executed if (o.get("pnl") or 0.0) <= 0]

    # ── Exit-type buckets ─────────────────────────────────
    tsl_exits    = [o for o in executed if o.get("exit_reason") == "Trailing Stop"]
    gap_exits    = [o for o in executed if o.get("exit_reason") == "Gap Down Exit"]
    hard_sl_exits = [
        o for o in executed
        if o.get("exit_reason") == "Stop Loss"
        and (o.get("pnl") or 0.0) < 0          # P&L < 0 → hit initial hard SL
    ]
    be_sl_exits  = [
        o for o in executed
        if o.get("exit_reason") == "Stop Loss"
        and abs(o.get("pnl") or 0.0) < 5        # near-zero → exited at break-even SL
    ]
    ema_exits    = [o for o in executed if o.get("exit_reason") == "EMA Exit"]
    sig_exits    = [o for o in executed if o.get("exit_reason") == "Strategy Signal"]
    manual_exits = [o for o in executed if o.get("exit_reason") == "Manual Cancel"]

    win_rate = f"{len(wins)/len(executed)*100:.0f}%" if executed else "—"
    avg_win  = (sum(o.get("pnl", 0.0) for o in wins)   / max(len(wins),   1))
    avg_loss = (sum(o.get("pnl", 0.0) for o in losses) / max(len(losses), 1))

    lines = [
        "🧪 *Simulation Report*",
        "━━━━━━━━━━━━━━",
        f"💰 *Total P&L:*  `{_fmt_pnl(total_pnl)}`",
        f"🎯 *Win Rate:*   `{win_rate}`  ({len(wins)}W / {len(losses)}L  —  {len(executed)} closed)",
        f"📈 *Avg Win:*    `{_fmt_pnl(avg_win)}`",
        f"📉 *Avg Loss:*   `{_fmt_pnl(avg_loss)}`",
        "",
        "*Exit Breakdown*",
        f"  📈 Trailing Stop \\(TSL\\):   `{len(tsl_exits)}`",
        f"  🛡️ Break-even SL exit:    `{len(be_sl_exits)}`",
        f"  🔴 Hard 2% SL hit:        `{len(hard_sl_exits)}`",
        f"  ⬇️  Gap-down exit:          `{len(gap_exits)}`",
        f"  📊 EMA / Signal exit:     `{len(ema_exits) + len(sig_exits)}`",
        f"  ✕  Manual close:          `{len(manual_exits)}`",
    ]

    # TSL profitability note
    if tsl_exits:
        tsl_pnl  = sum(o.get("pnl", 0.0) for o in tsl_exits)
        tsl_avg  = tsl_pnl / len(tsl_exits)
        lines += [
            "",
            f"_TSL avg profit per trade: {_fmt_pnl(tsl_avg)}_",
        ]

    # Open unrealized
    positions   = _load_open_positions("sim")
    last_prices = trade_store.load_last_prices("sim")
    unreal = sum(
        (last_prices[s] - p["entry_price"]) * p["quantity"]
        for s, p in positions.items()
        if s in last_prices
    )
    if positions:
        lines += [
            "",
            f"📂 *Open:* {len(positions)} positions",
            f"📊 *Unrealized:* `{_fmt_pnl(unreal)}`",
        ]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# /profit  (Kite live — stub)
# ─────────────────────────────────────────────────────────────

@restricted
async def profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live Zerodha P&L — requires Kite connection."""
    try:
        # Uncomment once Kite is connected:
        # positions = kite.positions()['day']
        # net_pnl = sum(pos['pnl'] for pos in positions)
        net_pnl = 0.00
        text = (
            f"💰 *Live P&L:* `₹{net_pnl:,.2f}`\n"
            f"_Connect Kite to see real data_"
        )
    except Exception as exc:
        text = f"❌ *Error:* `{exc}`"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# /order  (Kite live — stub)
# ─────────────────────────────────────────────────────────────

@restricted
async def live_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recent live orders from Zerodha — requires Kite connection."""
    try:
        text = (
            "📝 *Recent Live Orders*\n━━━━━━━━━━━━━━\n"
            "_Connect Kite to see real orders_"
        )
    except Exception as exc:
        text = f"❌ *Error:* {exc}"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("status",    status))
    app.add_handler(CommandHandler("simprofit", sim_profit))
    app.add_handler(CommandHandler("profit",    profit))
    app.add_handler(CommandHandler("order",     live_order))
    app.add_handler(CommandHandler("simorder",  sim_order))
    print("Telegram bot is polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
