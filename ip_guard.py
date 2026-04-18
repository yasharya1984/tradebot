"""
ip_guard.py
===========
SEBI Static-IP Compliance (April 2026 mandate)
-----------------------------------------------
SEBI requires all algo-trading systems to operate from a whitelisted static IP
and to maintain an audit trail of the machine's public IP at regular intervals.

Public API
----------
  verify_ip_compliance()          – Call once at startup (live mode only).
                                    Exits the process with a CRITICAL error if the
                                    current public IP is not in config.ALLOWED_IPS.

  start_ip_heartbeat(interval_s)  – Launch a background thread that appends an IP
                                    record to trade_data/live/audit_log.json every
                                    `interval_s` seconds (default: 3600 = 1 hour).

  get_public_ip()                 – Return the current public IP (string) or None.

  log_ip_once(label)              – Write one record to audit_log.json immediately.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

import config

logger = logging.getLogger(__name__)

_AUDIT_LOG_PATH = Path(__file__).parent / "trade_data" / "live" / "audit_log.json"

# Public IP services tried in order — falls back down the list on timeout
_IP_SERVICES = [
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
]

_TIMEOUT_SECS = 5


# ──────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────

def _fetch_ip_from(url: str) -> str | None:
    """Hit one IP-echo service; return stripped IP string or None on failure."""
    try:
        with urlopen(url, timeout=_TIMEOUT_SECS) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def _load_audit_log() -> list:
    if not _AUDIT_LOG_PATH.exists():
        return []
    try:
        with open(_AUDIT_LOG_PATH) as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except Exception as exc:
        logger.error(f"ip_guard: could not read audit log: {exc}")
        return []


def _append_audit_record(record: dict) -> None:
    _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entries = _load_audit_log()
    entries.append(record)
    try:
        with open(_AUDIT_LOG_PATH, "w") as fh:
            json.dump(entries, fh, indent=2, default=str)
    except Exception as exc:
        logger.error(f"ip_guard: could not write audit log: {exc}")


# ──────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────

def get_public_ip() -> str | None:
    """
    Return the machine's current public IPv4 address by querying lightweight
    IP-echo services.  Tries up to three services before giving up.
    Returns None if all services are unreachable (no internet / strict firewall).
    """
    for url in _IP_SERVICES:
        ip = _fetch_ip_from(url)
        if ip:
            return ip
    logger.warning("ip_guard: could not determine public IP — all services unreachable")
    return None


def log_ip_once(label: str = "heartbeat") -> dict:
    """
    Write one IP record to audit_log.json and return the record dict.

    Record format:
    {
      "timestamp": "2026-04-18T09:15:00.123456",
      "label":     "startup" | "heartbeat" | "compliance_check",
      "public_ip": "1.2.3.4",
      "allowed":   true | false | null   (null when ALLOWED_IPS is empty)
    }
    """
    ip      = get_public_ip()
    allowed = _check_allowed(ip)
    record  = {
        "timestamp": datetime.now().isoformat(),
        "label":     label,
        "public_ip": ip or "UNKNOWN",
        "allowed":   allowed,
    }
    _append_audit_record(record)
    logger.info(
        f"ip_guard [{label}]: public_ip={ip or 'UNKNOWN'}  "
        f"allowed={'YES' if allowed else ('NOT_CHECKED' if allowed is None else 'NO')}"
    )
    return record


def _check_allowed(ip: str | None) -> bool | None:
    """
    Return True if ip is in ALLOWED_IPS, False if not, None if list is empty/unconfigured.
    """
    allowed_list = getattr(config, "ALLOWED_IPS", [])
    # Strip empty strings so an unconfigured [""] list reads as "not set"
    configured   = [a.strip() for a in allowed_list if a and a.strip()]
    if not configured:
        return None   # not configured — skip enforcement
    if ip is None:
        return False  # can't verify → block
    return ip in configured


def verify_ip_compliance() -> str | None:
    """
    SEBI startup IP check for live mode.

    1. Fetches the current public IP.
    2. Compares it against config.ALLOWED_IPS.
    3. Writes a "startup" record to audit_log.json regardless of outcome.
    4. If the IP is not in the allowed list, logs CRITICAL and calls sys.exit(1).

    Returns the public IP string on success, or exits the process on failure.
    If ALLOWED_IPS is empty / not configured the check is skipped (returns ip).
    """
    ip      = get_public_ip()
    allowed = _check_allowed(ip)

    record = {
        "timestamp": datetime.now().isoformat(),
        "label":     "startup_compliance_check",
        "public_ip": ip or "UNKNOWN",
        "allowed":   allowed,
    }
    _append_audit_record(record)

    if allowed is None:
        # ALLOWED_IPS not configured — log a warning but continue
        logger.warning(
            f"ip_guard: ALLOWED_IPS is empty in config.py — IP check skipped. "
            f"Current public IP: {ip or 'UNKNOWN'}. "
            "Set config.ALLOWED_IPS to enforce SEBI static-IP compliance."
        )
        return ip

    if allowed:
        logger.info(
            f"✅ ip_guard: IP compliance PASSED — {ip} is whitelisted."
        )
        return ip

    # IP mismatch — SEBI compliance failure
    logger.critical(
        f"🚨 INSECURE IP DETECTED — ALGO EXECUTION HALTED FOR COMPLIANCE.\n"
        f"   Current IP : {ip or 'UNKNOWN'}\n"
        f"   Allowed IPs: {getattr(config, 'ALLOWED_IPS', [])}\n"
        "   Register this IP with Zerodha and add it to config.ALLOWED_IPS, "
        "or run the bot from a whitelisted machine."
    )
    sys.exit(
        "Insecure IP detected. Algo execution halted for compliance. "
        f"Current IP: {ip or 'UNKNOWN'}"
    )


def start_ip_heartbeat(interval_s: int = 3600) -> threading.Thread:
    """
    Start a daemon thread that appends an IP record to audit_log.json every
    `interval_s` seconds (default 3600 = 1 hour).

    The thread is daemon=True so it does not prevent clean process exit.
    Returns the started Thread object.
    """
    def _loop():
        while True:
            time.sleep(interval_s)
            try:
                log_ip_once("heartbeat")
            except Exception as exc:
                logger.error(f"ip_guard heartbeat error: {exc}")

    t = threading.Thread(target=_loop, daemon=True, name="ip-heartbeat")
    t.start()
    logger.info(
        f"ip_guard: heartbeat started — logging IP to audit_log.json "
        f"every {interval_s // 60} min."
    )
    return t
