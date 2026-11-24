"""
Manual rebalance / notification broker.

Instead of executing orders with a venue, ManualBroker maintains a virtual book
in the `manual_portfolio` table, sizes trades against it, and sends a Telegram
notification summarizing the rebalance when finished.
"""

import logging
import math
import os
from datetime import UTC, datetime
from typing import Literal

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.symbol_map import SymbolMapper
from tradingbot.utils.data_service import DataService
from tradingbot.utils.db import ManualPortfolio, get_db_session
from tradingbot.utils.telegram_notify import send_telegram_message

logger = logging.getLogger(__name__)


def _format_orders_message(
    orders: list[dict],
    cash: float,
    positions: dict[str, int],
    prices: dict[str, float],
) -> str:
    """Format Telegram rebalance notification text.

    Header with UTC date and order count.
    SELL lines first, then BUY lines.
    Target after table with symbol, shares, weight %, and cash.
    """
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    count = len(orders)
    order_word = "order" if count == 1 else "orders"
    lines = [
        f"Manual Rebalance — {date_str} ({count} {order_word})",
        "",
    ]

    sells = [o for o in orders if o["side"] == "SELL"]
    buys = [o for o in orders if o["side"] == "BUY"]

    for o in sells:
        lines.append(f"SELL {o['quantity']} {o['symbol']}  ~${o['approx_value']:,.0f}")
    for o in buys:
        lines.append(f"BUY {o['quantity']} {o['symbol']}  ~${o['approx_value']:,.0f}")

    lines.append("")
    lines.append("Target after:")

    total_equity = cash + sum(positions.get(s, 0) * prices.get(s, 0.0) for s in positions)
    for sym in sorted(positions.keys()):
        qty = positions[sym]
        if qty <= 0:
            continue
        px = prices.get(sym, 0.0)
        val = qty * px
        pct = (val / total_equity * 100.0) if total_equity > 0 else 0.0
        lines.append(f"  {sym:<6} {qty:>6} shares ({pct:>5.1f}%)  ~${val:,.0f}")

    cash_pct = (cash / total_equity * 100.0) if total_equity > 0 else 0.0
    lines.append(f"  Cash: ${cash:,.0f} ({cash_pct:.1f}%)")
    lines.append(f"  Total Equity: ~${total_equity:,.0f}")

    return "\n".join(lines)


class ManualBroker(LiveBroker):
    name = "manual"
    cancels_orders_implicitly = True
    supports_short = False

    def __init__(
        self,
        telegram_token: str,
        telegram_chat_id: str,
        portfolio_name: str | None = None,
        name: str = "manual",
        symbol_mapper: SymbolMapper | None = None,
        data_service: DataService | None = None,
    ):
        self.name = name
        self.portfolio_name = portfolio_name or name
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()
        self.pending: list[dict] = []
        self.cash: float = 0.0
        self.positions: dict[str, int] = {}

    @property
    def account_ref(self) -> str:
        return "manual"

    @property
    def is_sandbox(self) -> bool:
        return True

    def connect(self, readonly: bool = False) -> None:
        self.pending = []
        with get_db_session() as session:
            row = session.get(ManualPortfolio, self.portfolio_name)
            if row is not None:
                self.cash = float(row.cash)
                self.positions = {k: int(v) for k, v in (row.positions or {}).items() if int(v) > 0}
            else:
                seed_cash = float(os.getenv("LIVETRADE_MANUAL_EQUITY_USD", "50000"))
                self.cash = seed_cash
                self.positions = {}
        logger.info(f"ManualBroker connected (cash=${self.cash:,.2f}, positions={len(self.positions)})")

    def disconnect(self) -> None:
        pass

    def get_cash(self) -> float:
        return self.cash

    def get_positions(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.positions.items() if v > 0}

    def get_total_equity(self) -> float:
        equity = self.cash
        for symbol, qty in self.positions.items():
            if qty > 0:
                price = self.get_latest_price(symbol)
                equity += qty * price
        return equity

    def place_order(
        self,
        broker_symbol: str,
        quantity: float,
        side: Literal["BUY", "SELL", "SHORT", "COVER"],
        symbol_type: str | None = None,
        limit_price: float | None = None,
    ) -> None:
        held_qty = self.positions.get(broker_symbol, 0)

        if side == "BUY":
            shares = math.floor(quantity)
            if shares <= 0:
                logger.info(f"BUY quantity {quantity} for {broker_symbol} rounds to 0 shares; skipping")
                return

            price = self.get_latest_price(broker_symbol)
            if price <= 0:
                logger.warning(f"No price available for {broker_symbol}; skipping order")
                return

            # Ensure cash is never negative: cannot buy more than available cash allows
            max_affordable = math.floor(self.cash / price)
            shares = min(shares, max_affordable)
            if shares <= 0:
                logger.warning(
                    f"Not enough cash (${self.cash:.2f}) to buy 1 share of {broker_symbol} at ${price:.2f}; skipping"
                )
                return

            cost = shares * price
            self.cash = max(0.0, self.cash - cost)
            self.positions[broker_symbol] = held_qty + shares
            self.pending.append(
                {
                    "symbol": broker_symbol,
                    "quantity": shares,
                    "side": "BUY",
                    "price": price,
                    "approx_value": cost,
                }
            )
            logger.info(f"Virtual BUY {shares} {broker_symbol} @ ~${price:.2f} (~${cost:.2f})")

        elif side == "SELL":
            if held_qty <= 0:
                logger.warning(f"SELL for {broker_symbol} called but held quantity is {held_qty}; skipping")
                return

            # Full liquidation sells exact held qty; partial sell floors
            shares = held_qty if quantity >= held_qty else math.floor(quantity)

            if shares <= 0:
                logger.info(f"SELL quantity {quantity} for {broker_symbol} rounds to 0 shares; skipping")
                return

            price = self.get_latest_price(broker_symbol)
            proceeds = shares * max(0.0, price)
            self.cash += proceeds
            new_qty = held_qty - shares
            if new_qty > 0:
                self.positions[broker_symbol] = new_qty
            else:
                self.positions.pop(broker_symbol, None)

            self.pending.append(
                {
                    "symbol": broker_symbol,
                    "quantity": shares,
                    "side": "SELL",
                    "price": price,
                    "approx_value": proceeds,
                }
            )
            logger.info(f"Virtual SELL {shares} {broker_symbol} @ ~${price:.2f} (~${proceeds:.2f})")

        else:
            logger.warning(f"Unsupported side {side} for ManualBroker; skipping")

    def is_tradeable(self, yf_symbol: str) -> bool:
        """US stocks/ETFs only (copied from C2 adapter logic)."""
        meta = self.map_symbol(yf_symbol)
        if not meta or not meta.get("symbol") or meta.get("type") == "crypto":
            return False
        mapped = meta["symbol"]
        return not any(ch in mapped for ch in ".-^")

    def map_symbol(self, yf_symbol: str) -> dict | None:
        return self.symbol_mapper.map_symbol(yf_symbol, broker_name=self.name)

    def search_symbol(self, query: str) -> list[dict]:
        return []

    def finish_sync(self, dry_run: bool) -> None:
        if not self.pending or dry_run:
            return

        prices: dict[str, float] = {}
        for s in self.positions:
            prices[s] = self.get_latest_price(s)
        for o in self.pending:
            if o["symbol"] not in prices:
                prices[o["symbol"]] = self.get_latest_price(o["symbol"])

        text = _format_orders_message(self.pending, self.cash, self.positions, prices)
        send_telegram_message(self.telegram_token, self.telegram_chat_id, text)
        self._save_state()
        self.pending = []

    def _save_state(self) -> None:
        with get_db_session() as session:
            row = session.get(ManualPortfolio, self.portfolio_name)
            now = datetime.now(UTC).replace(tzinfo=None)
            if row is None:
                row = ManualPortfolio(
                    name=self.portfolio_name,
                    cash=self.cash,
                    positions=dict(self.positions),
                    updated_at=now,
                )
                session.add(row)
            else:
                row.cash = self.cash
                row.positions = dict(self.positions)
                row.updated_at = now
            session.commit()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")

    broker = ManualBroker(telegram_token=token, telegram_chat_id=chat_id)
    broker.connect()
    broker.print_account_summary()
