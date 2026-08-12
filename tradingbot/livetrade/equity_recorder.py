"""
Snapshot real broker/vault equity into the live_equity table.

The live-trading side is otherwise stateless: it re-derives everything from the
broker on each run and writes nothing. That is fine for trading but useless for a
track record, so this module records one row per (broker, account, UTC day).

Broker-agnostic on purpose — any LiveBroker can be passed. Never raises: an
equity snapshot failing must not fail a trading run.
"""

import json
import logging
from datetime import UTC, datetime

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.utils.db import LiveEquity, get_db_session, init_db

logger = logging.getLogger(__name__)


def record_live_equity(broker: LiveBroker, bot_weights: dict | None = None) -> None:
    """Upsert today's equity snapshot for `broker`. Idempotent per UTC day.

    Re-running on the same day overwrites the row (last write wins), matching
    calculate_portfolio_worth's one-point-per-day contract.
    """
    try:
        init_db()

        equity = float(broker.get_total_equity())
        if equity <= 0:
            # An API outage returns 0.0 from every adapter. Writing that would
            # punch a fake drawdown into the published curve.
            logger.warning(
                f"Skipping {broker.name} equity snapshot: equity={equity} "
                f"(broker unreachable, or the account is genuinely empty)"
            )
            return

        try:
            cash = float(broker.get_cash())
        except Exception:
            cash = None
        try:
            positions = {k: float(v) for k, v in (broker.get_positions() or {}).items()}
        except Exception:
            positions = {}

        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        # LiveBroker.account_ref / .is_sandbox, not a getattr() chain over
        # adapter-specific attribute names. The old chain looked broker-agnostic
        # but only ever matched Hyperliquid: eToro and Darwinex spell their paper
        # flag `demo`, so a demo account would have been recorded is_testnet=False
        # — paper money entering the published live track record.
        account_id = broker.account_ref
        is_testnet = broker.is_sandbox

        with get_db_session() as session:
            row = (
                session.query(LiveEquity)
                .filter(
                    LiveEquity.broker == broker.name,
                    LiveEquity.account_id == account_id,
                    LiveEquity.date == today,
                )
                .one_or_none()
            )
            if row is None:
                row = LiveEquity(broker=broker.name, account_id=account_id, date=today)
                session.add(row)

            row.timestamp = now.replace(tzinfo=None)
            row.equity = equity
            row.cash = cash
            row.positions = positions
            row.bot_weights = json.dumps(bot_weights) if bot_weights else None
            row.is_testnet = is_testnet

        logger.info(
            f"Recorded {broker.name} equity ${equity:,.2f} (cash ${cash:,.2f}) for {today.date()}"
            if cash is not None
            else f"Recorded {broker.name} equity ${equity:,.2f} for {today.date()}"
        )
    except Exception as e:
        logger.error(f"Failed to record live equity: {e}", exc_info=True)
