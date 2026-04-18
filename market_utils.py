"""
market_utils.py
===============
Single source of truth for NSE market-hours logic.

Imported by bot_orders, data_fetcher, main, simulator, and dashboard so
every module uses the same holiday calendar and open/closed rules.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN_H,  MARKET_OPEN_M  = 9,  15   # 09:15 IST
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30   # 15:30 IST

# ── NSE public holidays (market CLOSED on these dates) ───────────────────────
# Update annually from the official NSE holiday calendar.
NSE_HOLIDAYS: frozenset = frozenset({
    # ── 2025 ──
    date(2025,  1, 26),   # Republic Day
    date(2025,  2, 26),   # Maha Shivratri
    date(2025,  3, 14),   # Holi
    date(2025,  3, 31),   # Id-Ul-Fitr (Ramzan Eid)
    date(2025,  4, 14),   # Dr. B.R. Ambedkar Jayanti
    date(2025,  4, 18),   # Good Friday
    date(2025,  5,  1),   # Maharashtra Day
    date(2025,  8, 15),   # Independence Day
    date(2025, 10,  2),   # Gandhi Jayanti / Dussehra
    date(2025, 10, 21),   # Diwali Laxmi Puja
    date(2025, 10, 22),   # Diwali Balipratipada
    date(2025, 11,  5),   # Gurunanak Jayanti
    date(2025, 12, 25),   # Christmas
    # ── 2026 (verify against official NSE calendar when published) ──
    date(2026,  1, 26),   # Republic Day
    date(2026,  2, 16),   # Maha Shivratri (approx)
    date(2026,  3,  3),   # Holi (approx)
    date(2026,  4,  3),   # Good Friday
    date(2026,  4, 14),   # Dr. B.R. Ambedkar Jayanti
    date(2026,  5,  1),   # Maharashtra Day
    date(2026,  8, 15),   # Independence Day
    date(2026, 10,  2),   # Gandhi Jayanti
    date(2026, 12, 25),   # Christmas
})


def is_market_open() -> bool:
    """
    Return True if the NSE equity market is currently open.

    Rules:
    - Weekends (Sat/Sun) → always closed.
    - NSE_HOLIDAYS → always closed.
    - Weekday 09:15–15:30 IST → open.
    """
    now   = datetime.now(IST)
    today = now.date()
    if now.weekday() >= 5 or today in NSE_HOLIDAYS:
        return False
    mo = now.replace(hour=MARKET_OPEN_H,  minute=MARKET_OPEN_M,  second=0, microsecond=0)
    mc = now.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0, microsecond=0)
    return mo <= now <= mc


def market_opened_today() -> bool:
    """
    True if the market has already opened today (even if currently closed after 15:30).
    Used for price-staleness checks — on weekends/holidays returns False.
    """
    now   = datetime.now(IST)
    today = now.date()
    if now.weekday() >= 5 or today in NSE_HOLIDAYS:
        return False
    return now.hour > MARKET_OPEN_H or (
        now.hour == MARKET_OPEN_H and now.minute >= MARKET_OPEN_M
    )


def next_open_dt(after: datetime) -> datetime:
    """Return the datetime of the next NSE market open (09:15 IST) strictly after `after`."""
    d = (after + timedelta(days=1)).replace(
        hour=MARKET_OPEN_H, minute=MARKET_OPEN_M, second=0, microsecond=0
    )
    while d.weekday() >= 5 or d.date() in NSE_HOLIDAYS:
        d += timedelta(days=1)
    return d
