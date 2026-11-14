"""
Short carry management: borrow fee accrual, dividends in lieu, and maintenance liquidation.

Follows IBKR paper model (PLAN.md S1d).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from .bot_repository import BotRepository
from .config import ShortConfig
from .data_service import DataService
from .db import Bot as BotModel
from .db import CashAdjustment, DividendEvent, get_db_session
from .margin import (
    MissingPriceError,
    _is_option,
    _option_underlyings,
    book_values,
    cover_order,
    maintenance_breach,
)

logger = logging.getLogger(__name__)

# IBKR US equity interest & borrow fee calculation convention: Act/360
DAYS_PER_YEAR = 360.0


def apply_carry(
    bot_name: str,
    today: date | datetime | None = None,
    session: Session | None = None,
    data_service: DataService | None = None,
    prices: dict[str, float] | None = None,
) -> list[CashAdjustment]:
    """
    Apply daily carry adjustments for a bot:
    1. Daily borrow fee on short stock market value (calendar days since last accrual).
    2. Dividends in lieu on ex-date for short stock holdings.
    3. Maintenance liquidation force-cover if margin maintenance is breached.

    Idempotent via the `cash_adjustments` table. Fail-closed: raises MissingPriceError
    if any held short lacks a valid positive price.

    Args:
        bot_name: Name of the bot to process.
        today: Valuation date (defaults to UTC today).
        session: Optional database session; opens a new transaction if None.
        data_service: Optional DataService for fetching missing prices.
        prices: Optional pre-fetched prices mapping symbol -> price.

    Returns:
        List of created CashAdjustment records.
    """
    if today is None:
        today_date = datetime.now(UTC).date()
    elif isinstance(today, datetime):
        today_date = today.date()
    else:
        today_date = today

    if session is None:
        with get_db_session() as sess:
            result = _apply_carry_session(bot_name, today_date, sess, data_service, prices)
            sess.commit()
            return result
    else:
        return _apply_carry_session(bot_name, today_date, session, data_service, prices)


def _apply_carry_session(
    bot_name: str,
    today_date: date,
    session: Session,
    data_service: DataService | None,
    prices: dict[str, float] | None,
) -> list[CashAdjustment]:
    bot = session.query(BotModel).filter_by(name=bot_name).with_for_update().one_or_none()
    if bot is None:
        return []

    portfolio = dict(bot.portfolio or {})
    unders = _option_underlyings(portfolio)

    # Short stock positions (excluding cash, options, and option underlyings)
    short_positions: dict[str, float] = {}
    for s, q in portfolio.items():
        if s == "USD":
            continue
        qty = float(q or 0.0)
        if qty < -1e-9 and not _is_option(s) and s not in unders:
            short_positions[s] = qty

    resolved_prices = dict(prices) if prices is not None else {}

    # Fetch missing prices if data_service is available
    if data_service is not None:
        held_symbols = [
            s
            for s, q in portfolio.items()
            if s != "USD" and abs(float(q or 0.0)) > 1e-9 and not _is_option(s) and s not in unders
        ]
        missing_symbols = [s for s in held_symbols if s not in resolved_prices or resolved_prices[s] is None]
        if missing_symbols:
            try:
                batch = data_service.get_latest_prices_batch(missing_symbols)
                for s, p in batch.items():
                    if p is not None and p > 0:
                        resolved_prices[s] = float(p)
            except Exception:
                pass
            for s in missing_symbols:
                if s not in resolved_prices or resolved_prices[s] is None:
                    try:
                        p = data_service.get_latest_price(s)
                        if p is not None and p > 0:
                            resolved_prices[s] = float(p)
                    except Exception:
                        pass

    # Fail closed on missing or non-positive short prices
    for s, _ in short_positions.items():
        p = resolved_prices.get(s)
        if p is None or p <= 0:
            raise MissingPriceError(f"Missing price for short holding {s!r}")

    cfg = ShortConfig.from_env()
    adjustments: list[CashAdjustment] = []
    portfolio_changed = False

    # 1. Borrow fee accrual
    for s, qty in short_positions.items():
        existing_fee = (
            session.query(CashAdjustment)
            .filter_by(bot_name=bot_name, date=today_date, symbol=s, kind="borrow_fee")
            .first()
        )
        if existing_fee is not None:
            continue

        last_adj = (
            session.query(CashAdjustment)
            .filter(
                CashAdjustment.bot_name == bot_name,
                CashAdjustment.symbol == s,
                CashAdjustment.kind == "borrow_fee",
                CashAdjustment.date < today_date,
            )
            .order_by(CashAdjustment.date.desc())
            .first()
        )

        days = (today_date - last_adj.date).days if last_adj is not None else 1
        if days <= 0:
            continue

        annual_rate_pct = cfg.borrow_overrides.get(s, cfg.borrow_rate_pct)
        annual_rate = annual_rate_pct / 100.0
        price = float(resolved_prices[s])
        market_value = abs(qty) * price
        fee = market_value * annual_rate * (days / DAYS_PER_YEAR)

        if fee > 0:
            portfolio["USD"] = float(portfolio.get("USD", 0.0)) - fee
            portfolio_changed = True
            adj = CashAdjustment(
                bot_name=bot_name,
                date=today_date,
                symbol=s,
                kind="borrow_fee",
                amount=-fee,
            )
            session.add(adj)
            adjustments.append(adj)

    # 2. Dividends in lieu on ex-date (shorts only)
    for s, qty in short_positions.items():
        existing_div = (
            session.query(CashAdjustment)
            .filter_by(bot_name=bot_name, date=today_date, symbol=s, kind="dividend")
            .first()
        )
        if existing_div is not None:
            continue

        div_event = (
            session.query(DividendEvent)
            .filter(DividendEvent.symbol == s, DividendEvent.ex_date == today_date)
            .order_by(DividendEvent.id.desc())
            .first()
        )
        if div_event is not None and div_event.amount > 0:
            div_amount = float(div_event.amount)
            total_div = abs(qty) * div_amount
            if total_div > 0:
                portfolio["USD"] = float(portfolio.get("USD", 0.0)) - total_div
                portfolio_changed = True
                adj = CashAdjustment(
                    bot_name=bot_name,
                    date=today_date,
                    symbol=s,
                    kind="dividend",
                    amount=-total_div,
                )
                session.add(adj)
                adjustments.append(adj)

    # 3. Maintenance margin breach and liquidation
    if short_positions:
        while maintenance_breach(portfolio, resolved_prices):
            cover_sym = cover_order(portfolio, resolved_prices)
            if not cover_sym:
                break

            long_val, short_val, equity = book_values(portfolio, resolved_prices)
            maint_req = 0.25 * long_val + 0.30 * short_val
            cover_qty = float(portfolio.pop(cover_sym))
            cover_shares = abs(cover_qty)
            cover_price = float(resolved_prices[cover_sym])
            cover_cost = cover_shares * cover_price

            portfolio["USD"] = float(portfolio.get("USD", 0.0)) - cover_cost
            portfolio_changed = True

            reason = (
                f"maintenance margin breach: equity {equity:.2f} < requirement {maint_req:.2f}; "
                f"force covering {cover_shares} shares of {cover_sym} at {cover_price:.2f}"
            )

            BotRepository.log_trade(
                bot_name=bot_name,
                symbol=cover_sym,
                quantity=cover_shares,
                price=cover_price,
                is_buy=True,
                action="COVER",
                session=session,
            )
            logger.warning("Maintenance liquidation for %s: %s", bot_name, reason)

    if portfolio_changed:
        bot.portfolio = portfolio
        BotRepository.update_bot(bot, session=session)

    session.flush()
    return adjustments
