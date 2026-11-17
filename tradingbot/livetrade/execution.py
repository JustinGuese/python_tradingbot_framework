"""Execution timing and limit order policy for live trading.

Enforces:
1. Execution timing window: mid-session execution only [open + 30m, close - 15m]
   on US trading days to avoid the open auction spread and close rush.
2. Marketable limit orders: replaces raw market orders with limit orders priced
   within a buffer (default 0.15%) around the latest price.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from tradingbot.utils.market_calendar import is_session, session_close_utc, session_open_utc

logger = logging.getLogger(__name__)
NEW_YORK = ZoneInfo("America/New_York")


class ExecutionPolicy:
    """Execution policy controlling trading windows and limit order pricing."""

    def __init__(
        self,
        min_after_open_minutes: float | None = None,
        min_before_close_minutes: float | None = None,
        limit_band_pct: float | None = None,
    ):
        env_after = os.getenv("LIVETRADE_MIN_MIN_AFTER_OPEN") or os.getenv("LIVETRADE_MIN_AFTER_OPEN") or "30"
        self.min_after_open_minutes = (
            float(env_after) if min_after_open_minutes is None else float(min_after_open_minutes)
        )
        self.min_before_close_minutes = (
            float(os.getenv("LIVETRADE_MIN_BEFORE_CLOSE", "15"))
            if min_before_close_minutes is None
            else float(min_before_close_minutes)
        )
        self.limit_band_pct = (
            float(os.getenv("LIVETRADE_LIMIT_BAND_PCT", "0.0015")) if limit_band_pct is None else float(limit_band_pct)
        )

    def explain_window(self, now: datetime | None = None) -> tuple[bool, str]:
        """Check whether `now` is within the execution window and explain why."""
        if now is None:
            now = datetime.now(UTC)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        now_utc = now.astimezone(UTC)
        ny_date = now_utc.astimezone(NEW_YORK).date()

        if not is_session(ny_date):
            return False, f"{ny_date} is not a NYSE trading session (weekend or market holiday)"

        open_utc = session_open_utc(ny_date)
        close_utc = session_close_utc(ny_date)
        if open_utc is None or close_utc is None:
            return False, f"Could not determine open/close times for session {ny_date}"

        window_start = open_utc + timedelta(minutes=self.min_after_open_minutes)
        window_end = close_utc - timedelta(minutes=self.min_before_close_minutes)

        if now_utc < window_start:
            return (
                False,
                f"Current time {now_utc.isoformat()} is before window start {window_start.isoformat()} "
                f"(open + {self.min_after_open_minutes:.0f}m)",
            )
        if now_utc > window_end:
            return (
                False,
                f"Current time {now_utc.isoformat()} is after window end {window_end.isoformat()} "
                f"(close - {self.min_before_close_minutes:.0f}m)",
            )

        return (
            True,
            f"Current time {now_utc.isoformat()} is within execution window "
            f"[{window_start.isoformat()}, {window_end.isoformat()}]",
        )

    def in_window(self, now: datetime | None = None) -> bool:
        """Return True if `now` is inside the allowed execution window."""
        in_win, _ = self.explain_window(now)
        return in_win

    def marketable_limit(
        self,
        side: Literal["BUY", "SELL"] | str,
        last_price: float,
        band_pct: float | None = None,
    ) -> float:
        """Calculate a marketable limit order price given the last traded price.

        For BUY: last_price * (1 + band_pct)
        For SELL: last_price * (1 - band_pct)
        """
        if last_price <= 0:
            raise ValueError(f"last_price must be positive, got {last_price}")

        pct = self.limit_band_pct if band_pct is None else float(band_pct)
        side_upper = side.upper()
        if side_upper == "BUY":
            return round(last_price * (1.0 + pct), 4)
        elif side_upper == "SELL":
            return round(last_price * (1.0 - pct), 4)
        else:
            raise ValueError(f"Invalid side {side!r}, expected 'BUY' or 'SELL'")


def in_window(now: datetime | None = None, policy: ExecutionPolicy | None = None) -> bool:
    """Helper to check execution window using a default or custom policy."""
    pol = policy or ExecutionPolicy()
    return pol.in_window(now)


def marketable_limit(
    side: Literal["BUY", "SELL"] | str,
    last_price: float,
    band_pct: float | None = None,
) -> float:
    """Helper to compute marketable limit price using default ExecutionPolicy."""
    return ExecutionPolicy().marketable_limit(side, last_price, band_pct=band_pct)
