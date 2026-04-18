#!/usr/bin/env python3
"""
mtls_watcher.py — Nginx mTLS Access Log Watcher
================================================
Tails /var/log/nginx/access.log and fires a Telegram security alert
whenever a client certificate is successfully verified (ssl_client_verify=SUCCESS)
and the server returns HTTP 200 or 302.

Nginx log format expected (set in nginx/nginx.conf):
  $remote_addr - "$ssl_client_s_dn" [$time_local] "$request" $status $body_bytes_sent "$http_referer" cert_verify=$ssl_client_verify

Deduplication:
  Only one alert is sent per source IP per DEDUP_WINDOW_SECONDS (default 60 s).
  This prevents flooding when Streamlit makes many concurrent sub-resource
  requests during a single page load.

Resource filtering:
  Alerts fire only on dashboard root/page requests.
  Nginx/Streamlit internal paths (_stcore, stream, static, _health) are skipped.

Environment variables (required):
  TG_BOT_TOKEN   — Telegram bot token from @BotFather
  TG_CHAT_ID     — Your Telegram chat / user ID

Optional:
  NGINX_ACCESS_LOG      — path to the nginx access log (default /var/log/nginx/access.log)
  DEDUP_WINDOW_SECONDS  — cooldown per IP before a new alert fires (default 60)
"""

import os
import re
import time
import logging
from datetime import datetime, timezone, timedelta

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mtls-watcher] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Telegram credentials (required) ──────────────────────────────────────────
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TG_CHAT_ID",   "")

# ── Config ────────────────────────────────────────────────────────────────────
LOG_FILE            = os.environ.get("NGINX_ACCESS_LOG",     "/var/log/nginx/access.log")
DEDUP_WINDOW        = int(os.environ.get("DEDUP_WINDOW_SECONDS", "60"))

# ── IST timezone ──────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

# ── Nginx log_format main regex ───────────────────────────────────────────────
# Matches:  $remote_addr - "$ssl_client_s_dn" [$time_local] "$request" $status $bytes "$referer" cert_verify=$ssl_client_verify
LOG_RE = re.compile(
    r'^(?P<ip>\S+)'
    r' - '
    r'"(?P<cert_dn>[^"]*)"'
    r' \[(?P<time_local>[^\]]+)\]'
    r' "(?P<request>[^"]*)"'
    r' (?P<status>\d{3})'
    r' (?P<bytes>\d+)'
    r' "(?P<referer>[^"]*)"'
    r' cert_verify=(?P<cert_verify>\S+)'
)

# HTTP statuses that represent a real page delivery (not error, not redirect loop)
SUCCESS_STATUSES = {"200", "302"}

# Nginx-internal and Streamlit sub-resource path prefixes to ignore
# (these fire on every page load and are not "dashboard logins")
SKIP_PREFIXES = (
    "/_stcore",
    "/stream",
    "/static",
    "/_health",
    "/favicon",
    "/component/",
    "/vendor/",
)

# In-memory dedup: maps IP → last alert epoch
_last_alert: dict[str, float] = {}


def _extract_cn(cert_dn: str) -> str:
    """Extract Common Name from an X.509 DN string (e.g. 'CN=trader,O=TradingBot')."""
    m = re.search(r'CN=([^,/]+)', cert_dn, re.IGNORECASE)
    return m.group(1).strip() if m else (cert_dn.strip() or "Unknown")


def _parse_nginx_time(time_local: str) -> str:
    """Convert nginx time_local (e.g. '18/Apr/2026:09:15:30 +0000') to IST string."""
    try:
        dt = datetime.strptime(time_local, "%d/%b/%Y:%H:%M:%S %z")
        return dt.astimezone(IST).strftime("%d %b %Y %H:%M:%S")
    except ValueError:
        return time_local  # return raw if format differs


def _extract_path(request_line: str) -> str:
    """Extract the URL path from nginx's $request field (e.g. 'GET /foo HTTP/1.1')."""
    parts = request_line.split()
    return parts[1] if len(parts) >= 2 else "/"


def _is_alertable(m: re.Match) -> bool:
    """
    Return True if this log line should trigger an alert:
      - cert_verify == SUCCESS
      - HTTP status in SUCCESS_STATUSES
      - Not an internal/static sub-resource path
    """
    if m.group("cert_verify") != "SUCCESS":
        return False
    if m.group("status") not in SUCCESS_STATUSES:
        return False
    path = _extract_path(m.group("request"))
    if any(path.startswith(p) for p in SKIP_PREFIXES):
        return False
    return True


def _is_deduped(ip: str) -> bool:
    """Return True if an alert for this IP was sent within the dedup window."""
    last = _last_alert.get(ip, 0.0)
    return (time.monotonic() - last) < DEDUP_WINDOW


def send_telegram_alert(ip: str, cert_dn: str, timestamp: str) -> None:
    """POST a Telegram security notification."""
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("TG_BOT_TOKEN or TG_CHAT_ID not set — alert skipped")
        return

    cn = _extract_cn(cert_dn)
    message = (
        "🔐 *Successful Dashboard Login*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 *Timestamp:* `{timestamp} IST`\n"
        f"🌐 *IP Address:* `{ip}`\n"
        f"🪪 *Certificate Subject:* `{cn}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "_Your trading dashboard was accessed via mTLS_"
    )
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Alert sent — IP=%s  CN=%s", ip, cn)
        _last_alert[ip] = time.monotonic()
    except requests.RequestException as exc:
        logger.error("Failed to send Telegram alert: %s", exc)


def tail_log(path: str):
    """
    Yield new lines from *path* as they are appended.

    Behaviour:
    - Waits (with logging) if the file doesn't exist yet (nginx may not have
      written its first log line immediately after container start).
    - Seeks to EOF on open so only future lines are processed.
    - Detects log rotation by watching for the file to shrink and re-opens.
    """
    while not os.path.exists(path):
        logger.info("Waiting for log file: %s", path)
        time.sleep(5)

    with open(path, "r") as fh:
        fh.seek(0, 2)          # seek to end — skip existing history
        pos = fh.tell()
        logger.info("Watching %s from byte offset %d", path, pos)

        while True:
            line = fh.readline()
            if line:
                yield line.rstrip("\n")
                pos = fh.tell()
            else:
                time.sleep(0.5)
                try:
                    if os.path.getsize(path) < pos:
                        logger.info("Log rotation detected — re-opening %s", path)
                        return   # outer while-True in main() will reopen
                except OSError:
                    pass


def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        logger.error(
            "TG_BOT_TOKEN and TG_CHAT_ID must be set. "
            "Add them to your .env file and restart."
        )
        # Don't exit — still tail the log so the service stays healthy;
        # alerts will be skipped but log lines will be consumed.

    logger.info(
        "mTLS watcher started | log=%s | dedup_window=%ds",
        LOG_FILE, DEDUP_WINDOW,
    )

    while True:      # outer loop handles log rotation
        try:
            for line in tail_log(LOG_FILE):
                m = LOG_RE.match(line)
                if not m:
                    continue
                if not _is_alertable(m):
                    continue
                ip = m.group("ip")
                if _is_deduped(ip):
                    logger.debug("Deduped alert for %s", ip)
                    continue
                ts = _parse_nginx_time(m.group("time_local"))
                send_telegram_alert(ip=ip, cert_dn=m.group("cert_dn"), timestamp=ts)
        except OSError as exc:
            logger.warning("Log file error (%s) — retrying in 5 s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
