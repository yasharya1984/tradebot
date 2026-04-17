from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Configuration
BOT_TOKEN = '8792563638:AAGnC8J3PXsUWZTiXyJoHNygWbkcqs2cyK4'
MY_CHAT_ID = '5582749951'

# 1. SECURITY DECORATOR
def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != MY_CHAT_ID:
            print(f"Unauthorized access by {update.effective_user.id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# 2. COMMAND FUNCTIONS

@restricted
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """General health check of the system."""
    # Logic: Check if WebSocket is connected and PM2 is healthy
    text = (
        "🤖 *System Status*\n"
        "━━━━━━━━━━━━━━\n"
        "🟢 **Engine:** Running\n"
        "🌐 **NSE Feed:** Connected\n"
        "📊 **Mode:** Hybrid (Sim + Live)"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

@restricted
async def sim_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Current P&L for Simulation trades."""
    # Logic: Pull from your local simulation DB/Variable
    pnl = 4250.75 # Replace with actual data
    win_rate = "64%"
    text = (
        "🧪 *Simulation Report*\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 **Total P&L:** ₹{pnl:,.2f}\n"
        f"🎯 **Win Rate:** {win_rate}\n"
        "📅 **Period:** Today"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

@restricted
async def profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Current Realized/Unrealized P&L from Zerodha."""
    try:
        # Example: positions = kite.positions()['day']
        # net_pnl = sum(pos['pnl'] for pos in positions)
        net_pnl = 0.00 
        text = f"💰 *Live P&L:* `₹{net_pnl:,.2f}`"
    except Exception as e:
        text = f"❌ *Error fetching Live P&L:* `{str(e)}`"
    
    await update.message.reply_text(text, parse_mode='Markdown')

@restricted
async def live_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View the last 5 real orders placed today."""
    try:
        # orders = kite.orders()[-5:]
        text = "📝 *Recent Live Orders*\n━━━━━━━━━━━━━━\n"
        text += "• RELIANCE | BUY | COMPLETED\n• ZOMATO | SELL | OPEN"
    except Exception as e:
        text = f"❌ *Error fetching orders:* {str(e)}"
    
    await update.message.reply_text(text, parse_mode='Markdown')

@restricted
async def sim_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View the active simulation orders."""
    # Logic: Pull from your local sim_manager list
    text = "🧪 *Active Sim Trades*\n━━━━━━━━━━━━━━\n"
    text += "• TMPV.NS | BUY | @ ₹450.20\n• UNITDSPR.NS | SELL | @ ₹1120.00"
    await update.message.reply_text(text, parse_mode='Markdown')

# 3. MAIN RUNNER
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Register Handlers
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("simprofit", sim_profit))
    app.add_handler(CommandHandler("profit", profit))
    app.add_handler(CommandHandler("order", live_order))
    app.add_handler(CommandHandler("simorder", sim_order))

    print("Bot is polling...")
    app.run_polling()

if __name__ == "__main__":
    main()