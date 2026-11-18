"""
Live state utilities for trading bots.

Provides stateless helpers for reading a bot's persisted positions, portfolio value,
and current weights from the database / market state.
"""

from __future__ import annotations

import logging
from typing import Any

from tradingbot.utils.botclass import DUST_QTY

logger = logging.getLogger(__name__)


def current_weights(bot: Any) -> dict[str, float]:
    """Return signed weights of the bot's persisted holdings valued at latest prices, excluding 'USD'.

    Returns ``{}`` when the portfolio is flat or holds only cash.

    Uses ``bot.dbBot`` to read persisted positions, ``bot.getLatestPricesBatch`` to price
    non-USD holdings, and ``bot.portfolio_value`` to compute the total portfolio worth.
    """
    try:
        db_bot = getattr(bot, "dbBot", None)
    except (AttributeError, LookupError):
        return {}

    if db_bot is None:
        return {}

    portfolio = getattr(db_bot, "portfolio", None)
    if not portfolio or not isinstance(portfolio, dict):
        return {}

    held = {sym: float(qty) for sym, qty in portfolio.items() if sym != "USD" and abs(float(qty)) > DUST_QTY}
    if not held:
        return {}

    symbols = list(held.keys())
    prices = bot.getLatestPricesBatch(symbols)
    if not isinstance(prices, dict):
        raise RuntimeError(f"Expected dict from getLatestPricesBatch, got {type(prices).__name__}")

    missing = [sym for sym in symbols if sym not in prices or prices[sym] is None]
    if missing:
        raise RuntimeError(f"Missing price for symbols: {missing}")

    pv = (
        bot.portfolio_value()
        if callable(getattr(bot, "portfolio_value", None))
        else getattr(bot, "portfolio_value", None)
    )
    if pv is None:
        raise RuntimeError(f"{type(bot).__name__} has no portfolio_value")
    total_val = float(pv)
    if total_val <= 0:
        raise ValueError(f"Portfolio value is non-positive ({total_val}); cannot compute weights")

    return {sym: (qty * float(prices[sym])) / total_val for sym, qty in held.items()}


def is_live_run(rows: dict[str, Any]) -> bool:
    """True when ``targetWeights`` was handed live rows.

    Live frames carry a ``timestamp`` column; backtest rows are indexed by label
    (the convention used by ``InstitutionalFlowBot._bar_timestamp``).
    """
    return any(hasattr(r, "index") and "timestamp" in r.index for r in rows.values())


def live_holdings(bot: Any, rows: dict[str, Any]) -> dict[str, float] | None:
    """Held weights for a live run, ``None`` in a backtest.

    ``None`` tells the caller to keep using its instance state (the backtest path).
    ``{}`` means a live book that is flat. A failed read raises on purpose: treating
    "could not read holdings" as "flat" would rebuild the book on every run, which
    is the daily-rebalance bug this exists to prevent.
    """
    if not is_live_run(rows):
        return None
    return current_weights(bot)
