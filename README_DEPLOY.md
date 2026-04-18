# Production Deployment Guide — India NSE Trading Bot

> **Hosting:** AWS Lightsail $5/month (1 vCPU, 512 MB RAM, 20 GB SSD, Static IP)  
> **Security:** Mutual TLS — dashboard requires a browser-installed client certificate  
> **Storage:** Private GitHub repository (free, replaces S3)  
> **SEBI Compliance:** Static IP whitelisted in Zerodha Kite (April 2026 mandate)

---

## Architecture

```
Your Browser (client.p12 installed)
  │
  │  HTTPS + Client Certificate (TLS 1.3)
  ▼
AWS Lightsail Static IP — Port 443
  │
  ▼ nginx:443 (public — validates client cert, returns 403 if missing)
  │
  ▼ trading-bot:8501 (internal Docker bridge only — NO internet exposure)
        │
        ├─► yfinance (price data, free)
        ├─► Zerodha Kite API (order placement — static IP whitelisted)
        ├─► GitHub REST API (trade_data sync every 15 min)
        └─► Telegram Bot API (push notifications)
```

**Security layers:**
1. Lightsail firewall restricts port 443 to your home IP only
2. Nginx requires a valid `client.p12` — anyone else gets HTTP 403
3. Streamlit (8501) has no exposed ports — unreachable from the internet
4. All secrets in `.env` (never inside the Docker image)

---

## Pre-Flight Checklist

Before you run a single command, have these ready:

| Item | Where to get it | Used as |
|---|---|---|
| Zerodha API Key | kite.trade → Apps | `ZERODHA_API_KEY` |
| Zerodha API Secret | kite.trade → Apps | `ZERODHA_API_SECRET` |
| Telegram Bot Token | @BotFather on Telegram | `TG_BOT_TOKEN` |
| Telegram Chat ID | see §1.1 below | `TG_CHAT_ID` |
| GitHub PAT | github.com → Settings → Tokens | `GITHUB_PAT` |
| GitHub repo name | `your-username/trading-data` | `GITHUB_REPO` |
| Docker Hub username | hub.docker.com | image name prefix |
| Lightsail Static IP | assigned in §3.2 | `ALLOWED_IPS`, certs SAN |
| Home public IP | `curl https://api.ipify.org` | Lightsail firewall rule |

---

## PHASE 1 — Third-Party Accounts Setup

### 1.1 Telegram Bot

**A. Create the bot:**

1. Open Telegram → search for **@BotFather** → tap **Start**
2. Send: `/newbot`
3. BotFather asks for a **name** (display name) → e.g. `My Trading Bot`
4. BotFather asks for a **username** (must end in `bot`) → e.g. `mynsedaytrading_bot`
5. BotFather replies with your **Bot Token**:
   ```
   Use this token to access the HTTP API:
   8792563638:AAGnC8J3PXsUWZTiXyJoHNygWbkcqs2cyK4
   ```
6. Save this — it becomes `TG_BOT_TOKEN` in your `.env`

**B. Get your Chat ID:**

1. Open Telegram → search for the bot you just created → tap **Start**
2. Send any message (e.g. `/start`)
3. In your browser, open:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
4. Look for `"chat":{"id":` in the JSON response:
   ```json
   "chat": { "id": 5582749951, "first_name": "Yash", "type": "private" }
   ```
5. That number is your `TG_CHAT_ID`

**C. Verify it works (optional):**

```bash
curl "https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>&text=Bot+is+alive"
```
You should receive a Telegram notification.

---

### 1.2 GitHub Private Repository (Data Storage)

The bot commits `orders.json` and portfolio snapshots to this repo every 15 minutes.

1. Go to github.com → top-right **+** → **New repository**
2. Repository name: `trading-data`
3. Visibility: **Private**
4. **Do not** add README or .gitignore (leave completely empty)
5. Click **Create repository**

**Generate a Personal Access Token (PAT):**

1. github.com → top-right avatar → **Settings**
2. Left sidebar → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
3. Click **Generate new token (classic)**
   - Note: `trading-bot-sync`
   - Expiration: **1 year** (add a calendar reminder to rotate it)
   - Scope: check **`repo`** (full control of private repos)
4. Click **Generate token** — copy it immediately (shown only once)
5. This is your `GITHUB_PAT`

---

### 1.3 Docker Hub Private Repository

1. Go to hub.docker.com → log in
2. Click **Create Repository**
3. Name: `trading-bot`
4. Visibility: **Private**
5. Click **Create**
6. Note your Docker Hub username — your image will be `<username>/trading-bot:latest`

**Generate a Docker Hub Access Token** (more secure than your password):

1. hub.docker.com → top-right avatar → **Account Settings**
2. Left sidebar → **Security** → **New Access Token**
3. Description: `lightsail-deploy`
4. Permissions: **Read, Write, Delete**
5. Copy the token — used in `lightsail_startup.sh` as `DOCKER_HUB_PASS`

---

### 1.4 Zerodha Kite Connect — Create API App

1. Go to kite.trade → log in with your Zerodha credentials
2. Click **My Apps** → **Create new app**
3. Fill in:
   - App name: `NSE Trading Bot`
   - App type: **Connect**
   - Redirect URL: `http://127.0.0.1` ← temporary; you'll update this with your Lightsail IP later
   - Description: any text
4. Click **Create**
5. You'll see your **API Key** and **API Secret** — save both
   - `API Key` → `ZERODHA_API_KEY`
   - `API Secret` → `ZERODHA_API_SECRET`

> The IP whitelist in the Kite app will be added in Phase 5 once you have your Lightsail static IP.

---

## PHASE 2 — Local Machine Preparation

### 2.1 Generate mTLS Certificates

You need your Lightsail static IP for the server certificate SAN.  
**You must complete Phase 3 (create Lightsail instance + assign static IP) before this step.**

```bash
# From inside the trading_bot/ directory:
chmod +x generate_certs.sh

SERVER_IP=<YOUR_LIGHTSAIL_STATIC_IP> \
P12_PASSWORD=<CHOOSE_A_STRONG_PASSWORD> \
./generate_certs.sh
```

This generates the `certs/` directory:

| File | Used by | Notes |
|---|---|---|
| `certs/ca.crt` | Nginx (verifies client cert) | Copy to server |
| `certs/ca.key` | Cert signing only | Keep offline — not needed after certs are signed |
| `certs/server.crt` | Nginx (server identity) | Copy to server |
| `certs/server.key` | Nginx (server identity) | Copy to server |
| `certs/client.crt` | Reference | Bundled inside `.p12` |
| `certs/client.key` | Reference | Bundled inside `.p12` |
| `certs/client.p12` | Your browser / Mac Keychain | Install on your Mac + iPhone |

> `certs/` is in `.gitignore` — these files are never committed.

---

### 2.2 Install `client.p12` — Mac Keychain (Chrome + Safari)

1. Double-click `certs/client.p12` in Finder
2. macOS dialog: **"Add Certificate"** → set keychain to **"login"** → click **Add**
3. Enter the `P12_PASSWORD` you set in §2.1
4. Open **Keychain Access** (`⌘ Space` → type "Keychain Access")
5. In the left sidebar select **"login"** keychain → **"My Certificates"** category
6. Find the certificate named **"trader"**
7. Right-click **"trader"** → **Get Info** → expand **Trust**
8. Set **"When using this certificate"** to → **Always Trust**
9. Close the window → macOS asks for your login password → enter it

**Verify Chrome sees the cert:**
Navigate to `https://<YOUR_LIGHTSAIL_IP>` (after Phase 4 is complete).  
Chrome shows a dialog: *"A website wants to use a certificate to identify you"* → select **"trader"** → Continue.

---

### 2.3 Install `client.p12` — iPhone / iPad (optional)

1. Connect iPhone via USB → open Finder → drag `certs/client.p12` to the iPhone in Finder
   *(or AirDrop it — but be careful on shared AirDrop settings)*
2. On iPhone: **Settings** → a new **"Profile Downloaded"** banner appears at the top → tap it
3. Tap **Install** → enter your device passcode
4. Settings → **General** → **VPN & Device Management** → tap the profile → tap **Trust**

---

### 2.4 Build and Push the Docker Image

```bash
# Make sure you're in the trading_bot/ directory

# Log in to Docker Hub
docker login

# Set up buildx for cross-compilation (M1/M2 Mac → Lightsail x86)
docker buildx create --name mybuilder --use 2>/dev/null || docker buildx use mybuilder
docker buildx inspect --bootstrap

# Build and push (linux/amd64 = Lightsail architecture)
docker buildx build \
    --platform linux/amd64 \
    -t <YOUR_DOCKERHUB_USERNAME>/trading-bot:latest \
    --push \
    .
```

Wait for the push to complete. You can verify at hub.docker.com → your repo → Tags.

---

## PHASE 3 — AWS Lightsail Instance

### 3.1 Create the Instance

1. Open console.aws.amazon.com/lightsail → **Create instance**
2. Region: closest to India (e.g. `ap-south-1` Mumbai)
3. Platform: **Linux/Unix**
4. Blueprint: **OS Only → Ubuntu 22.04 LTS**
5. Instance plan: **$5/month** (1 vCPU, 512 MB RAM, 20 GB SSD)
6. Instance name: `trading-bot`
7. Click **Create instance** — wait ~2 minutes for it to start

---

### 3.2 Assign a Static IP

> Without a static IP, your IP changes on every reboot, breaking the Zerodha whitelist and the server cert.

1. Lightsail console → **Networking** tab (top nav) → **Create static IP**
2. Static IP name: `trading-bot-ip`
3. Attach to instance: `trading-bot`
4. Click **Create and attach**
5. **Copy this IP** — you will use it everywhere:
   - As `SERVER_IP` in `generate_certs.sh` (§2.1)
   - As `ALLOWED_IPS` in `.env`
   - In the Zerodha Kite whitelist (§5.1)
   - In the Lightsail firewall rule for HTTPS

---

### 3.3 Configure Lightsail Firewall

Lightsail console → `trading-bot` instance → **Networking** tab → **IPv4 Firewall**

**Delete the default HTTP rule** (port 80 open to all) and replace with these:

| Application | Protocol | Port range | Source | Reason |
|---|---|---|---|---|
| SSH | TCP | 22 | `<YOUR_HOME_IP>/32` | Admin access from your home only |
| HTTPS | TCP | 443 | `<YOUR_HOME_IP>/32` | mTLS dashboard from your home only |
| HTTP | TCP | 80 | `0.0.0.0/0` (All IPs) | Redirects to HTTPS; no sensitive data |

> **Find your home IP:** `curl https://api.ipify.org`  
> Use format `x.x.x.x/32` in the Source field.

> Port 8501 is **NOT in the firewall** — Streamlit is not exposed to the internet at all.

> If you're on a dynamic home IP (most broadband): update the SSH and HTTPS rules whenever your IP changes. Or use a cheap VPN with a fixed exit IP.

---

### 3.4 Download SSH Key and Connect

1. Lightsail console → Account (top-right) → **SSH keys** → download default key  
   *(or use the key you created during instance setup)*
2. Move the key file and set permissions:

```bash
mv ~/Downloads/LightsailDefaultKey-ap-south-1.pem ~/.ssh/lightsail.pem
chmod 400 ~/.ssh/lightsail.pem
```

3. Test the connection:

```bash
ssh -i ~/.ssh/lightsail.pem ubuntu@<YOUR_STATIC_IP>
# You should see the Ubuntu welcome message
# Type: exit
```

---

## PHASE 4 — Server Bootstrap

### 4.1 Copy Certificates to the Server

Run these commands from your **local machine** (inside `trading_bot/`):

```bash
# Create the certs directory on the server
ssh -i ~/.ssh/lightsail.pem ubuntu@<YOUR_STATIC_IP> \
    "mkdir -p /opt/trading-bot/certs"

# Copy the three server-side cert files (NOT ca.key, NOT client.*)
scp -i ~/.ssh/lightsail.pem \
    certs/ca.crt \
    certs/server.crt \
    certs/server.key \
    ubuntu@<YOUR_STATIC_IP>:/opt/trading-bot/certs/

# Set correct permissions on the server
ssh -i ~/.ssh/lightsail.pem ubuntu@<YOUR_STATIC_IP> \
    "chmod 600 /opt/trading-bot/certs/server.key && \
     chmod 644 /opt/trading-bot/certs/ca.crt /opt/trading-bot/certs/server.crt"
```

---

### 4.2 Prepare and Copy the Startup Script

1. Open `lightsail_startup.sh` in your editor
2. Fill in **Section 1** — all values are required:

```bash
DOCKER_IMAGE="<YOUR_DOCKERHUB_USERNAME>/trading-bot:latest"
DOCKER_HUB_USER="<YOUR_DOCKERHUB_USERNAME>"
DOCKER_HUB_PASS="<YOUR_DOCKERHUB_ACCESS_TOKEN>"   # from §1.3

ZERODHA_API_KEY="<FROM_KITE_APP>"
ZERODHA_API_SECRET="<FROM_KITE_APP>"
TRADING_MODE="simulation"                           # start safe; switch to live later

GITHUB_PAT="ghp_xxxxxxxxxxxxxxxxxxxx"              # from §1.2
GITHUB_REPO="<YOUR_GITHUB_USERNAME>/trading-data"  # from §1.2
GITHUB_BRANCH="main"

TG_BOT_TOKEN="<FROM_BOTFATHER>"                    # from §1.1A
TG_CHAT_ID="<YOUR_NUMERIC_CHAT_ID>"                # from §1.1B
```

3. Copy the script to the server:

```bash
scp -i ~/.ssh/lightsail.pem \
    lightsail_startup.sh \
    ubuntu@<YOUR_STATIC_IP>:~/
```

---

### 4.3 Run the Startup Script

```bash
# SSH into the server
ssh -i ~/.ssh/lightsail.pem ubuntu@<YOUR_STATIC_IP>

# Run the bootstrap
chmod +x lightsail_startup.sh
sudo ./lightsail_startup.sh
```

The script will:
- Install Docker + Docker Compose plugin
- Write `docker-compose.yml` and `nginx.conf` to `/opt/trading-bot/`
- Write `/opt/trading-bot/.env` with your secrets
- Pull the Docker image from Docker Hub
- Start both containers: `trading-nginx` and `trading-bot`

**Expected output at the end:**
```
╔══════════════════════════════════════════════════════════╗
║           DEPLOYMENT COMPLETE (mTLS)                    ║
║  Dashboard : https://<YOUR_STATIC_IP>                   ║
║  Port 8501 : NOT exposed (internal Docker only)         ║
╚══════════════════════════════════════════════════════════╝
```

**Verify both containers are running:**

```bash
docker ps
# Should show two containers:
#   trading-nginx    Up X minutes (healthy)
#   trading-bot      Up X minutes (healthy)
```

**Test the mTLS 403 (from the server itself — no client cert):**

```bash
curl -k https://localhost
# Expected: 403 Forbidden — "Client certificate required or invalid"
```

---

## PHASE 5 — Post-Deploy Configuration

### 5.1 Whitelist Your Lightsail IP in Zerodha Kite

1. Go to kite.trade → log in → **My Apps** → click your app → **Edit**
2. Under **Redirect URL**: change `http://127.0.0.1` to `https://<YOUR_STATIC_IP>`
3. Under **IP Whitelist**: add `<YOUR_STATIC_IP>`
4. Click **Save**

> This satisfies the SEBI April 2026 Static IP mandate.  
> Every API call from the bot originates from this static IP.

---

### 5.2 Update ALLOWED_IPS in .env

```bash
# On the server:
nano /opt/trading-bot/.env

# Find the ALLOWED_IPS line and update it:
ALLOWED_IPS=<YOUR_LIGHTSAIL_STATIC_IP>

# Save (Ctrl+O, Enter, Ctrl+X), then restart:
cd /opt/trading-bot
docker compose restart
```

---

### 5.3 Verify Telegram Notifications

```bash
# Send a test notification from inside the container
docker exec trading-bot python -c "
from tg_bot import send_notification
import time
send_notification('Bot is live on Lightsail')
time.sleep(3)
print('Notification sent')
"
```

You should receive a Telegram message within a few seconds.

If you don't receive it:
```bash
# Check the bot token is set correctly
docker exec trading-bot env | grep TG_

# Check Telegram API is reachable
docker exec trading-bot curl -s \
    "https://api.telegram.org/bot${TG_BOT_TOKEN}/getMe" | python3 -m json.tool
```

---

### 5.4 Telegram Security Alerts

Every time someone successfully accesses the dashboard using a valid client certificate,
the `mtls-watcher` sidecar sends a Telegram alert like this:

```
🔐 Successful Dashboard Login
━━━━━━━━━━━━━━━━━━━━━━━━
📅 Timestamp: 18 Apr 2026 09:15:30 IST
🌐 IP Address: 203.0.113.42
🪪 Certificate Subject: trader
━━━━━━━━━━━━━━━━━━━━━━━━
Your trading dashboard was accessed via mTLS
```

#### How it works

```
nginx writes → /var/log/nginx/access.log (shared Docker volume: nginx_logs)
                            ↓
           mtls-watcher tails the file
                            ↓
    cert_verify=SUCCESS + HTTP 200/302 on a dashboard path?
                            ↓
           POST to Telegram Bot API
```

The watcher uses a **60-second dedup window** per source IP so that Streamlit's
many parallel sub-resource requests (WebSocket, static assets) only fire one alert
per dashboard open.

#### Prerequisites

`TG_BOT_TOKEN` and `TG_CHAT_ID` must be set in `.env` (see §1.1 and Phase 1 above).
The watcher sidecar is built automatically by `docker compose up -d`.

#### Verify the watcher is running

```bash
# Check container status
docker ps
# Should show: trading-mtls-watcher   Up X minutes

# Follow watcher logs
docker logs -f trading-mtls-watcher
# Expected line: "Watching /var/log/nginx/access.log from byte offset N"
```

#### Test the alert (after mTLS is active)

```bash
# Option A — access the dashboard in your browser (with client.p12 installed)
# Open https://<YOUR_LIGHTSAIL_IP> — you should receive a Telegram message.

# Option B — inject a fake matching log line to test without a browser
docker exec trading-nginx sh -c \
  'echo "203.0.113.42 - \"CN=trader,O=TradingBot\" [$(date +\"%d/%b/%Y:%H:%M:%S +0000\")] \"GET / HTTP/1.1\" 200 1234 \"-\" cert_verify=SUCCESS" >> /var/log/nginx/access.log'

# Watch the watcher respond within ~1 second:
docker logs -f trading-mtls-watcher
```

#### Tuning alert frequency

By default one alert fires per source IP per 60 seconds.
To change this, update `.env`:

```bash
DEDUP_WINDOW_SECONDS=300   # one alert per IP per 5 minutes
```

Then restart the watcher:

```bash
cd /opt/trading-bot && docker compose restart mtls-watcher
```

---

### 5.5 Verify GitHub Sync

```bash
# Trigger a manual push from inside the container
docker exec trading-bot python github_sync.py --push
# Expected: Push result: {'pushed': N, 'failed': 0}

# Check your GitHub repo — you should see trade_data/ files committed
# github.com/<YOUR_USERNAME>/trading-data
```

If the push fails:
```bash
# Check env vars are set
docker exec trading-bot env | grep GITHUB_

# Test GitHub API access
docker exec trading-bot curl -s \
    -H "Authorization: token $GITHUB_PAT" \
    https://api.github.com/user | python3 -m json.tool
```

---

## PHASE 6 — Daily Token Refresh (Every Morning)

The Zerodha access token **expires every 24 hours**. Refresh it before 9:15 AM IST.

### Step 6.1 — Get the Login URL

```bash
ssh -i ~/.ssh/lightsail.pem ubuntu@<YOUR_STATIC_IP>
docker exec -it trading-bot python main.py token
```

Output will be:
```
1. Open this login URL:
   https://kite.zerodha.com/connect/login?api_key=xxxxx&v=3

2. After login, copy the 'request_token' from the browser URL
3. Paste it in config.py → ZERODHA_REQUEST_TOKEN
4. Re-run: python main.py token
```

---

### Step 6.2 — Complete the OAuth Flow

1. Copy the login URL from step 6.1
2. Paste it in your browser (with `client.p12` installed)
3. Log in with your Zerodha credentials
4. After login, the browser redirects to a URL like:
   ```
   https://<YOUR_STATIC_IP>/?request_token=AbCdEfGhIjKl&action=login&status=success
   ```
5. Copy the `request_token` value (`AbCdEfGhIjKl`)

---

### Step 6.3 — Apply the Token

```bash
# On the server, update .env:
nano /opt/trading-bot/.env

# Set:
ZERODHA_REQUEST_TOKEN=AbCdEfGhIjKl

# Save and restart:
cd /opt/trading-bot && docker compose restart

# Generate the access token:
docker exec -it trading-bot python main.py token
# Output: ✅ Access token: yyyyyyyyyyyy...

# Update .env again:
nano /opt/trading-bot/.env
# Set:   ZERODHA_ACCESS_TOKEN=yyyyyyyyyyyy...
# Clear: ZERODHA_REQUEST_TOKEN=

# Final restart:
docker compose restart
```

> Set a daily alarm at **8:40 AM IST** for this 2-minute routine.

---

## PHASE 7 — Verify Dashboard Access

1. Open Chrome on your Mac (with `client.p12` installed in Keychain)
2. Navigate to `https://<YOUR_LIGHTSAIL_STATIC_IP>`
3. Chrome shows: *"A website wants to use a certificate to identify you"*
4. Select **"trader"** → click **Continue**
5. You should see the Streamlit Trading Bot dashboard

**If you see a browser SSL warning** about an untrusted certificate: this is expected for self-signed certs. Click **Advanced → Proceed to `<ip>` (unsafe)** — it is safe because you generated this cert yourself.

**If you see 403 Forbidden:** your `client.p12` is not installed or not trusted. Re-do §2.2.

---

## PHASE 8 — Switching to Live Trading

When you're confident the simulation is working correctly:

1. Ensure `ALLOWED_IPS` is set and Kite whitelist is done (§5.1–5.2)
2. Ensure a fresh `ZERODHA_ACCESS_TOKEN` is set (§6)
3. Update `.env` on the server:

```bash
nano /opt/trading-bot/.env
# Change: TRADING_MODE=live

cd /opt/trading-bot && docker compose restart
```

4. Watch the logs to confirm live orders are being placed:

```bash
docker logs -f trading-bot
```

> **Warning:** In live mode the bot places real orders with real money up to `LIVE_TRADING_CAP` (default ₹50,000). Do not switch to live until you have watched simulation mode work correctly for several days.

---

## PHASE 9 — Daily Operations

### Your typical trading day:

| Time (IST) | Action |
|---|---|
| 08:40 AM | SSH in → run daily token refresh (Phase 6) |
| 08:45 AM | Container restarts → GitHub pulls previous day's data |
| 09:15 AM | Market opens → trading loop activates automatically |
| During day | Bot scans 300 stocks every 30 seconds |
| Every 15 min | GitHub sync: `orders.json` + portfolio committed |
| 03:30 PM | Market closes → final GitHub sync → bot enters wait mode |
| Evening | Open dashboard: check P&L, review orders on GitHub |

### Monitoring commands:

```bash
# Container status
docker ps

# Live trading bot log
docker logs -f trading-bot

# Live nginx log (shows cert verification status)
docker logs -f trading-nginx

# Last 50 lines from the bot
docker logs --tail 50 trading-bot

# Check nginx access log (see which IPs connected + cert status)
docker exec trading-nginx cat /var/log/nginx/access.log | tail -20

# Manually trigger a GitHub push
docker exec trading-bot python github_sync.py --push

# Manually pull latest data from GitHub
docker exec trading-bot python github_sync.py --pull

# Check what's in the GitHub-synced data
docker exec trading-bot python -c "
import json, pathlib
f = pathlib.Path('trade_data/sim/orders.json')
if f.exists():
    orders = json.loads(f.read_text())
    print(f'{len(orders)} orders loaded')
    if orders: print('Latest:', orders[-1]['symbol'], orders[-1]['status'])
"
```

---

## PHASE 10 — Updating the Bot Code

```bash
# 1. On your local machine — rebuild and push
docker buildx build --platform linux/amd64 \
    -t <YOUR_DOCKERHUB_USERNAME>/trading-bot:latest --push .

# 2. On the server — pull new image and restart
ssh -i ~/.ssh/lightsail.pem ubuntu@<YOUR_STATIC_IP>
cd /opt/trading-bot
docker compose pull
docker compose up -d
```

> Nginx does not need to be rebuilt — it uses the official `nginx:1.25-alpine` image.  
> Only `trading-bot` image needs rebuilding after code changes.

---

## Troubleshooting

### Bot container won't start / keeps restarting

```bash
docker logs trading-bot --tail 100
# Look for: ImportError, ModuleNotFoundError, or missing env vars
```

### Nginx 403 on every request

```bash
docker logs trading-nginx --tail 20
# Look for: ssl_client_verify lines

# Common cause: client.p12 not installed in Keychain
# Fix: redo §2.2 — ensure "Always Trust" is set in Keychain Access
```

### Nginx can't start (cert errors)

```bash
docker logs trading-nginx
# Look for: cannot load certificate, no such file

# Check certs are present on server:
ls -la /opt/trading-bot/certs/
# Should show: ca.crt, server.crt, server.key

# If missing, re-run §4.1 to copy certs
```

### GitHub sync failing

```bash
docker exec trading-bot python github_sync.py --push
# Look for HTTP 401 (bad PAT) or 404 (wrong repo name)

# Regenerate PAT at github.com → Settings → Developer settings
# Update .env: GITHUB_PAT=new_token
# Restart: docker compose restart
```

### Telegram notifications not arriving

```bash
# Verify token and chat ID
docker exec trading-bot env | grep TG_

# Test API directly
docker exec trading-bot python -c "
import os, urllib.request
token = os.environ.get('TG_BOT_TOKEN', '')
chat  = os.environ.get('TG_CHAT_ID', '')
url = f'https://api.telegram.org/bot{token}/sendMessage?chat_id={chat}&text=Test'
print(urllib.request.urlopen(url).read().decode())
"
```

### Zerodha access token expired

```bash
docker logs trading-bot | grep -i "access token\|kite\|auth"
# If you see 403/401 errors from Kite, redo Phase 6
```

---

## Quick Reference — All Commands

```bash
# ── SSH ────────────────────────────────────────────────────────────
ssh -i ~/.ssh/lightsail.pem ubuntu@<YOUR_STATIC_IP>

# ── Container management ───────────────────────────────────────────
docker ps                                          # list running containers
docker compose -f /opt/trading-bot/docker-compose.yml restart    # restart all
docker compose -f /opt/trading-bot/docker-compose.yml down       # stop all
docker compose -f /opt/trading-bot/docker-compose.yml up -d      # start all

# ── Logs ───────────────────────────────────────────────────────────
docker logs -f trading-bot                         # live bot log
docker logs -f trading-nginx                       # live nginx log
docker logs --tail 100 trading-bot                 # last 100 bot lines

# ── GitHub sync ────────────────────────────────────────────────────
docker exec trading-bot python github_sync.py --push    # push data now
docker exec trading-bot python github_sync.py --pull    # restore from GitHub

# ── Zerodha token ─────────────────────────────────────────────────
docker exec -it trading-bot python main.py token        # daily token flow

# ── Edit config ────────────────────────────────────────────────────
nano /opt/trading-bot/.env                         # edit secrets
cd /opt/trading-bot && docker compose restart      # apply changes

# ── Server IP ─────────────────────────────────────────────────────
curl https://api.ipify.org                         # verify server public IP

# ── Update bot image ───────────────────────────────────────────────
# (local) docker buildx build --platform linux/amd64 -t user/trading-bot:latest --push .
cd /opt/trading-bot && docker compose pull && docker compose up -d

# ── Cert test (from server, no client cert → should get 403) ───────
curl -k https://localhost
```

---

## Cost Breakdown

| Service | Cost/month |
|---|---|
| AWS Lightsail $5 plan (1 vCPU, 512 MB, static IP) | ~₹430 |
| Zerodha Kite Connect API subscription | ₹500 |
| Docker Hub (1 free private repo) | Free |
| GitHub (private repo, < 1 GB storage) | Free |
| Telegram Bot API | Free |
| **Total hosting** | **~₹430** |
| **Total including Kite** | **~₹930** |

> Pure infrastructure cost (Lightsail + GitHub + Docker Hub + Telegram) = **~₹430/month**, well under ₹500.  
> Kite Connect is a separate brokerage tool subscription, not hosting.
