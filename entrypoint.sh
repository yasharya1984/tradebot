#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# entrypoint.sh — Container startup sequence
#
# Steps:
#   1. Pull the latest trade_data from GitHub (restores state after restart)
#   2. Launch the trading loop in the background
#   3. Launch the Streamlit dashboard in the foreground
#      (keeping the container alive; Docker monitors this process)
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

echo "============================================================"
echo "  India NSE Trading Bot — Container Starting"
echo "  $(date '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Mode: ${TRADING_MODE:-simulation}"
echo "============================================================"

# ── Step 1: Restore trade_data from GitHub ───────────────────────
echo ""
echo "[1/3] Pulling trade data from GitHub..."
if python github_sync.py --pull; then
    echo "  ✓ GitHub pull complete"
else
    echo "  ⚠ GitHub pull skipped or failed — starting with local/empty data"
fi

# ── Step 2: Start trading loop (background) ──────────────────────
echo ""
echo "[2/3] Starting trading loop (background)..."
python main.py paper --loop >> /app/trading_bot.log 2>&1 &
LOOP_PID=$!
echo "  ✓ Trading loop started (PID: $LOOP_PID)"

# Write the PID so the healthcheck or a future script can query it
echo "$LOOP_PID" > /tmp/trading_loop.pid

# ── Step 3: Start Streamlit dashboard (foreground) ───────────────
echo ""
echo "[3/3] Launching Streamlit dashboard on port 8501..."
echo "  Access at: http://<LIGHTSAIL_PUBLIC_IP>"
echo ""

# exec replaces the shell — Streamlit becomes PID 1's child and
# Docker SIGTERM/SIGKILL propagate correctly.
exec python -m streamlit run dashboard.py \
    --server.headless        true  \
    --server.address         0.0.0.0 \
    --server.port            8501 \
    --server.enableCORS      false \
    --server.enableXsrfProtection false
