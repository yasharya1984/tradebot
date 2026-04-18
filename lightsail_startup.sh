#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# lightsail_startup.sh — One-time bootstrap for a fresh Lightsail Ubuntu instance
#
# Prerequisites (run BEFORE this script):
#   1. Run generate_certs.sh locally with your Lightsail static IP
#   2. SCP ca.crt, server.crt, server.key to the server (see README_DEPLOY.md §0.3)
#
# What this script does:
#   • Installs Docker + Docker Compose plugin
#   • Creates /opt/trading-bot/ with docker-compose.yml, .env, certs/
#   • Pulls the bot image from Docker Hub
#   • Starts both containers (nginx + trading-bot)
#   • Confirms the firewall allows ports 80 and 443
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# SECTION 1 — FILL IN YOUR VALUES BEFORE RUNNING
# ═══════════════════════════════════════════════════════════════

DOCKER_IMAGE="your-dockerhub-username/trading-bot:latest"
DOCKER_HUB_USER="your-dockerhub-username"
DOCKER_HUB_PASS="your-dockerhub-token"   # Use an access token, not your password

ZERODHA_API_KEY="your_kite_api_key"
ZERODHA_API_SECRET="your_kite_api_secret"
TRADING_MODE="simulation"               # change to "live" when ready

GITHUB_PAT="ghp_xxxxxxxxxxxxxxxxxxxx"
GITHUB_REPO="your-github-username/trading-data"
GITHUB_BRANCH="main"

TG_BOT_TOKEN="1234567890:AAxxxxxxxxxxxxxxxxxx"
TG_CHAT_ID="123456789"

INSTALL_DIR="/opt/trading-bot"

# ═══════════════════════════════════════════════════════════════
# SECTION 2 — AUTOMATED SETUP (no edits needed below this line)
# ═══════════════════════════════════════════════════════════════

echo "╔══════════════════════════════════════════════════════╗"
echo "║   Trading Bot — Lightsail Bootstrap (mTLS)          ║"
echo "╚══════════════════════════════════════════════════════╝"

echo ""
echo "=== [1/7] Updating system packages ==="
apt-get update -qq
apt-get upgrade -y -qq

echo "=== [2/7] Installing Docker ==="
apt-get install -y -qq ca-certificates curl gnupg lsb-release

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable --now docker
echo "  ✓ Docker $(docker --version | cut -d' ' -f3 | tr -d ',')"

echo "=== [3/7] Setting up install directory ==="
mkdir -p "$INSTALL_DIR/certs"
mkdir -p "$INSTALL_DIR/nginx"

# Write docker-compose.yml
cat > "$INSTALL_DIR/docker-compose.yml" << 'COMPOSE_EOF'
version: "3.8"

services:
  nginx:
    image: nginx:1.25-alpine
    container_name: trading-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs/server.crt:/certs/server.crt:ro
      - ./certs/server.key:/certs/server.key:ro
      - ./certs/ca.crt:/certs/ca.crt:ro
    networks:
      - bot_net
    depends_on:
      trading-bot:
        condition: service_healthy
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost/_health"]
      interval: 30s
      timeout: 5s
      retries: 3

  trading-bot:
    image: ${DOCKER_IMAGE}
    container_name: trading-bot
    restart: unless-stopped
    environment:
      - ZERODHA_API_KEY=${ZERODHA_API_KEY}
      - ZERODHA_API_SECRET=${ZERODHA_API_SECRET}
      - ZERODHA_ACCESS_TOKEN=${ZERODHA_ACCESS_TOKEN:-}
      - TRADING_MODE=${TRADING_MODE:-simulation}
      - ALLOWED_IPS=${ALLOWED_IPS:-}
      - GITHUB_PAT=${GITHUB_PAT}
      - GITHUB_REPO=${GITHUB_REPO}
      - GITHUB_BRANCH=${GITHUB_BRANCH:-main}
      - TG_BOT_TOKEN=${TG_BOT_TOKEN:-}
      - TG_CHAT_ID=${TG_CHAT_ID:-}
    volumes:
      - trade_data:/app/trade_data
    networks:
      - bot_net
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:8501/_stcore/health"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 45s
    logging:
      driver: "json-file"
      options:
        max-size: "20m"
        max-file: "5"

networks:
  bot_net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.0.0/24

volumes:
  trade_data:
    driver: local
COMPOSE_EOF

# Write nginx.conf
cat > "$INSTALL_DIR/nginx/nginx.conf" << 'NGINX_EOF'
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid       /tmp/nginx.pid;

events { worker_connections 512; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    log_format main '$remote_addr "$ssl_client_s_dn" [$time_local] "$request" $status cert=$ssl_client_verify';
    access_log /var/log/nginx/access.log main;

    map $ssl_client_verify $mtls_valid {
        SUCCESS   1;
        default   0;
    }

    upstream streamlit_backend {
        server trading-bot:8501;
        keepalive 16;
    }

    server {
        listen 80 default_server;
        server_name _;
        location /_health { return 200 "ok\n"; add_header Content-Type text/plain; }
        location / { return 301 https://$host$request_uri; }
    }

    server {
        listen 443 ssl default_server;
        server_name _;

        ssl_certificate     /certs/server.crt;
        ssl_certificate_key /certs/server.key;
        ssl_client_certificate /certs/ca.crt;
        ssl_verify_client      optional;
        ssl_verify_depth       2;

        ssl_protocols             TLSv1.2 TLSv1.3;
        ssl_ciphers               ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256;
        ssl_prefer_server_ciphers off;
        ssl_session_cache   shared:SSL:10m;
        ssl_session_timeout 1d;
        ssl_session_tickets off;

        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Frame-Options           SAMEORIGIN                             always;
        add_header X-Content-Type-Options    nosniff                                always;

        if ($mtls_valid = 0) { return 403; }

        location / {
            proxy_pass         http://streamlit_backend;
            proxy_http_version 1.1;
            proxy_set_header   Upgrade    $http_upgrade;
            proxy_set_header   Connection "upgrade";
            proxy_set_header   Host              $host;
            proxy_set_header   X-Real-IP         $remote_addr;
            proxy_set_header   X-Forwarded-Proto https;
            proxy_read_timeout    86400s;
            proxy_send_timeout    86400s;
            proxy_connect_timeout    10s;
            proxy_buffer_size         128k;
            proxy_buffers             4 256k;
            proxy_busy_buffers_size   256k;
        }

        location ~* ^/(_stcore|stream|static)/ {
            proxy_pass         http://streamlit_backend;
            proxy_http_version 1.1;
            proxy_set_header   Upgrade    $http_upgrade;
            proxy_set_header   Connection "upgrade";
            proxy_set_header   Host       $host;
            proxy_read_timeout 86400s;
        }
    }
}
NGINX_EOF

echo "  ✓ docker-compose.yml and nginx.conf written"

echo "=== [4/7] Writing .env file ==="
cat > "$INSTALL_DIR/.env" << ENV_EOF
DOCKER_IMAGE=${DOCKER_IMAGE}
ZERODHA_API_KEY=${ZERODHA_API_KEY}
ZERODHA_API_SECRET=${ZERODHA_API_SECRET}
ZERODHA_ACCESS_TOKEN=
TRADING_MODE=${TRADING_MODE}
ALLOWED_IPS=
GITHUB_PAT=${GITHUB_PAT}
GITHUB_REPO=${GITHUB_REPO}
GITHUB_BRANCH=${GITHUB_BRANCH}
TG_BOT_TOKEN=${TG_BOT_TOKEN}
TG_CHAT_ID=${TG_CHAT_ID}
ENV_EOF
chmod 600 "$INSTALL_DIR/.env"
echo "  ✓ .env written (permissions: 600)"

echo "=== [5/7] Checking certificates ==="
MISSING_CERTS=()
for cert_file in ca.crt server.crt server.key; do
    if [ ! -f "$INSTALL_DIR/certs/$cert_file" ]; then
        MISSING_CERTS+=("$cert_file")
    fi
done

if [ ${#MISSING_CERTS[@]} -gt 0 ]; then
    echo ""
    echo "  ⚠  Missing certificate files: ${MISSING_CERTS[*]}"
    echo ""
    echo "  Run generate_certs.sh locally, then copy certs to the server:"
    echo "    scp certs/ca.crt certs/server.crt certs/server.key ubuntu@<IP>:$INSTALL_DIR/certs/"
    echo ""
    echo "  Re-run this section after copying certs, then:"
    echo "    cd $INSTALL_DIR && docker compose up -d"
    echo ""
    echo "  Skipping container start until certs are present."
else
    chmod 600 "$INSTALL_DIR/certs/server.key"
    chmod 644 "$INSTALL_DIR/certs/ca.crt" "$INSTALL_DIR/certs/server.crt"
    echo "  ✓ All certificates present"

    echo "=== [6/7] Pulling Docker image ==="
    echo "$DOCKER_HUB_PASS" | docker login -u "$DOCKER_HUB_USER" --password-stdin
    docker pull "$DOCKER_IMAGE"
    echo "  ✓ Image pulled: $DOCKER_IMAGE"

    echo "=== [7/7] Starting containers ==="
    cd "$INSTALL_DIR"
    docker compose up -d
    echo "  ✓ Containers started"

    # ── Summary ──────────────────────────────────────────────────────────
    PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org || echo "unknown")

    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║           DEPLOYMENT COMPLETE (mTLS)                    ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    echo "║  Dashboard : https://${PUBLIC_IP}                       "
    echo "║  Static IP : ${PUBLIC_IP}  ← whitelist in Kite          "
    echo "║  Port 8501 : NOT exposed (internal Docker only)        ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    echo "║  NEXT STEPS:                                            ║"
    echo "║  1. Install client.p12 into Mac Keychain / Chrome      ║"
    echo "║  2. Restrict port 443 to your home IP in Lightsail     ║"
    echo "║  3. Add ${PUBLIC_IP} to Zerodha Kite whitelist          "
    echo "║  4. Update ALLOWED_IPS in ${INSTALL_DIR}/.env           "
    echo "║  5. Refresh Zerodha token daily — see README_DEPLOY.md ║"
    echo "╚══════════════════════════════════════════════════════════╝"
fi
