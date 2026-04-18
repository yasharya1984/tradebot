#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# generate_certs.sh — Generate all certificates for Mutual TLS (mTLS)
#
# Produces:
#   certs/ca.crt        — Root CA certificate  (trusted by Nginx)
#   certs/ca.key        — Root CA private key   (keep secret, used to sign)
#   certs/server.crt    — Server TLS certificate (Nginx presents this)
#   certs/server.key    — Server private key
#   certs/client.crt    — Client certificate    (browser presents this)
#   certs/client.key    — Client private key
#   certs/client.p12    — Client cert + key in PKCS#12 (import into Chrome / Mac Keychain)
#
# Usage:
#   SERVER_IP=<your-lightsail-ip> P12_PASSWORD=<your-password> ./generate_certs.sh
#
# Defaults (if env vars not set):
#   SERVER_IP     = 127.0.0.1
#   P12_PASSWORD  = trading123   ← change before importing into browser
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

CERTS_DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$CERTS_DIR"
cd "$CERTS_DIR"

SERVER_IP="${SERVER_IP:-127.0.0.1}"
P12_PASSWORD="${P12_PASSWORD:-trading123}"
VALIDITY_DAYS=3650   # 10-year validity

echo "==================================================="
echo " Generating mTLS certificates"
echo " Server IP : $SERVER_IP"
echo " Output    : $CERTS_DIR"
echo "==================================================="

# ─────────────────────────────────────────────────────────────
# Step 1 — Root Certificate Authority
# ─────────────────────────────────────────────────────────────
echo ""
echo "[1/4] Generating Root CA..."

openssl genrsa -out ca.key 4096

openssl req -new -x509 \
    -days   "$VALIDITY_DAYS" \
    -key    ca.key \
    -out    ca.crt \
    -subj   "/CN=TradingBot-Root-CA/O=TradingBot/C=IN/ST=Maharashtra/L=Mumbai"

echo "  ✓ ca.crt  ca.key"

# ─────────────────────────────────────────────────────────────
# Step 2 — Server Certificate (Nginx)
# ─────────────────────────────────────────────────────────────
echo ""
echo "[2/4] Generating Server Certificate (SAN IP: $SERVER_IP)..."

openssl genrsa -out server.key 2048

openssl req -new \
    -key    server.key \
    -out    server.csr \
    -subj   "/CN=trading-bot-server/O=TradingBot/C=IN"

# Server SAN extension — browser requires SAN to match the host/IP
cat > server_ext.cnf << EXTEOF
[v3_server]
basicConstraints       = CA:FALSE
keyUsage               = critical, digitalSignature, keyEncipherment
extendedKeyUsage       = serverAuth
subjectAltName         = IP:${SERVER_IP},IP:127.0.0.1
EXTEOF

openssl x509 -req \
    -days     "$VALIDITY_DAYS" \
    -in       server.csr \
    -CA       ca.crt \
    -CAkey    ca.key \
    -CAcreateserial \
    -out      server.crt \
    -extfile  server_ext.cnf \
    -extensions v3_server

echo "  ✓ server.crt  server.key"

# ─────────────────────────────────────────────────────────────
# Step 3 — Client Certificate (Browser / you)
# ─────────────────────────────────────────────────────────────
echo ""
echo "[3/4] Generating Client Certificate..."

openssl genrsa -out client.key 2048

openssl req -new \
    -key    client.key \
    -out    client.csr \
    -subj   "/CN=trader/O=TradingBot/C=IN/emailAddress=trader@tradingbot.local"

cat > client_ext.cnf << EXTEOF
[v3_client]
basicConstraints       = CA:FALSE
keyUsage               = critical, digitalSignature
extendedKeyUsage       = clientAuth
EXTEOF

openssl x509 -req \
    -days     "$VALIDITY_DAYS" \
    -in       client.csr \
    -CA       ca.crt \
    -CAkey    ca.key \
    -CAcreateserial \
    -out      client.crt \
    -extfile  client_ext.cnf \
    -extensions v3_client

echo "  ✓ client.crt  client.key"

# ─────────────────────────────────────────────────────────────
# Step 4 — Export Client Certificate to PKCS#12 for browser import
# ─────────────────────────────────────────────────────────────
echo ""
echo "[4/4] Exporting client.p12 (password: $P12_PASSWORD)..."

# -legacy flag needed on OpenSSL 3.x for broad browser compatibility
if openssl pkcs12 -help 2>&1 | grep -q "\-legacy"; then
    LEGACY_FLAG="-legacy"
else
    LEGACY_FLAG=""
fi

openssl pkcs12 -export \
    $LEGACY_FLAG \
    -out      client.p12 \
    -inkey    client.key \
    -in       client.crt \
    -certfile ca.crt \
    -name     "TradingBot Client Cert" \
    -passout  "pass:$P12_PASSWORD"

echo "  ✓ client.p12"

# ─────────────────────────────────────────────────────────────
# Cleanup temporary files
# ─────────────────────────────────────────────────────────────
rm -f server.csr client.csr server_ext.cnf client_ext.cnf ca.srl

# ─────────────────────────────────────────────────────────────
# Secure file permissions
# ─────────────────────────────────────────────────────────────
chmod 600 ca.key server.key client.key client.p12
chmod 644 ca.crt server.crt client.crt

# ─────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────
echo ""
echo "─── Certificate Chain Verification ────────────────────"
echo -n "  Server cert issued by CA : "
openssl verify -CAfile ca.crt server.crt 2>&1 | grep -E "OK|error" || true
echo -n "  Client cert issued by CA : "
openssl verify -CAfile ca.crt client.crt 2>&1 | grep -E "OK|error" || true

echo ""
echo "╔═══════════════════════════════════════════════════════╗"
echo "║               CERTIFICATES READY                     ║"
echo "╠═══════════════════════════════════════════════════════╣"
echo "║  Nginx:                                              ║"
echo "║    ssl_certificate     → certs/server.crt           ║"
echo "║    ssl_certificate_key → certs/server.key           ║"
echo "║    ssl_client_certificate → certs/ca.crt            ║"
echo "╠═══════════════════════════════════════════════════════╣"
echo "║  Browser import:                                     ║"
echo "║    File    : certs/client.p12                       ║"
echo "║    Password: $P12_PASSWORD                           "
echo "╠═══════════════════════════════════════════════════════╣"
echo "║  IMPORTANT: Never commit certs/ to Git!             ║"
echo "║  Keep ca.key and client.p12 OFFLINE after setup.    ║"
echo "╚═══════════════════════════════════════════════════════╝"
