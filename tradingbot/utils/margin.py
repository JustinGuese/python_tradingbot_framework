"""
Margin, equity, and leverage calculations for short selling and long books (IBKR / Reg T model).

Pure functions without database or network I/O. Shared by live execution, paper trading,
and backtest loops (PLAN.md S0).
"""

from __future__ import annotations

from typing import Any


class MissingPriceError(KeyError, ValueError):
    """Raised when valuing a position whose current price is missing or invalid."""

    pass


def _is_option(symbol: str) -> bool:
    try:
        from .options import is_option_symbol

        return is_option_symbol(symbol)
    except Exception:
        return False


def _option_underlyings(portfolio: dict[str, Any]) -> set[str]:
    try:
        from .options import option_underlyings

        return option_underlyings(portfolio)
    except Exception:
        return set()


def book_values(portfolio: dict[str, Any], prices: dict[str, float]) -> tuple[float, float, float]:
    """
    Compute total long market value, short market value, and net equity for a book.

    Stock shorts only; option underlyings keep options.margin_requirement.
    Fail-closed: raises MissingPriceError if any un-hedged held stock lacks a valid positive price.

    Args:
        portfolio: Dict mapping symbols to quantities, plus 'USD' cash.
        prices: Dict mapping stock symbols to current prices.

    Returns:
        tuple of (long_value, short_value, equity).
    """
    cash = float(portfolio.get("USD", 0.0) or 0.0)
    unders = _option_underlyings(portfolio)

    long_val = 0.0
    short_val = 0.0

    for symbol, qty in portfolio.items():
        if symbol == "USD":
            continue
        q = float(qty or 0.0)
        if abs(q) < 1e-9:
            continue
        if _is_option(symbol) or symbol in unders:
            continue

        if symbol not in prices or prices[symbol] is None:
            raise MissingPriceError(f"Missing price for symbol {symbol!r}")

        price = float(prices[symbol])
        if price <= 0:
            raise MissingPriceError(f"Non-positive price {price} for symbol {symbol!r}")

        val = q * price
        if val > 0:
            long_val += val
        else:
            short_val += abs(val)

    equity = cash + long_val - short_val
    return (float(long_val), float(short_val), float(equity))


def initial_ok(portfolio: dict[str, Any], prices: dict[str, float], max_gross: float = 1.0) -> bool:
    """
    Check whether the book satisfies Reg T initial margin and maximum gross leverage.

    Reg T requires: equity >= 0.5 * long + 0.5 * short
    Gross leverage requires: (long + short) / equity <= max_gross

    Args:
        portfolio: Dict mapping symbols to quantities, plus 'USD' cash.
        prices: Dict mapping stock symbols to current prices.
        max_gross: Maximum gross leverage ratio (default: 1.0).

    Returns:
        True if all constraints are satisfied, False otherwise.
    """
    long_val, short_val, equity = book_values(portfolio, prices)
    if equity <= 0:
        return False

    reg_t_req = 0.5 * long_val + 0.5 * short_val
    if equity < reg_t_req - 1e-9:
        return False

    gross_exposure = long_val + short_val
    if (gross_exposure / equity) > (max_gross + 1e-9):
        return False

    return True


def maintenance_breach(portfolio: dict[str, Any], prices: dict[str, float]) -> bool:
    """
    Check whether the book has breached maintenance margin requirements.

    Maintenance requirement: equity < 0.25 * long + 0.30 * short

    Args:
        portfolio: Dict mapping symbols to quantities, plus 'USD' cash.
        prices: Dict mapping stock symbols to current prices.

    Returns:
        True if in breach (liquidation needed), False otherwise.
    """
    long_val, short_val, equity = book_values(portfolio, prices)
    maint_req = 0.25 * long_val + 0.30 * short_val
    return bool(equity < maint_req - 1e-9)


def spendable_cash(portfolio: dict[str, Any], prices: dict[str, float]) -> float:
    """
    Calculate cash available to spend, deducting short collateral.

    Cash minus short collateral, floored at 0; equals USD for a long-only book.

    Args:
        portfolio: Dict mapping symbols to quantities, plus 'USD' cash.
        prices: Dict mapping stock symbols to current prices.

    Returns:
        Spendable cash in USD.
    """
    long_val, short_val, _ = book_values(portfolio, prices)
    cash = float(portfolio.get("USD", 0.0) or 0.0)
    if short_val <= 1e-9:
        return float(cash)
    return float(max(0.0, cash - short_val))


def cover_order(portfolio: dict[str, Any], prices: dict[str, float]) -> str | None:
    """
    Identify the largest short position by market value to cover first during liquidation.

    Args:
        portfolio: Dict mapping symbols to quantities, plus 'USD' cash.
        prices: Dict mapping stock symbols to current prices.

    Returns:
        Symbol of the largest short position by value, or None if no shorts exist.
    """
    # Valuate whole book first to ensure all holdings have valid prices (fail closed)
    book_values(portfolio, prices)

    unders = _option_underlyings(portfolio)
    largest_symbol: str | None = None
    largest_value: float = -1.0

    for symbol, qty in portfolio.items():
        if symbol == "USD":
            continue
        q = float(qty or 0.0)
        if q >= -1e-9:
            continue
        if _is_option(symbol) or symbol in unders:
            continue

        price = float(prices[symbol])
        val = abs(q) * price
        if val > largest_value:
            largest_value = val
            largest_symbol = symbol

    return largest_symbol
