import logging
from typing import Literal

from ibind import IbkrClient, OrderRequest, QuestionType

from livetrade.broker import LiveBroker
from livetrade.symbol_map import SymbolMapper
from utils.data_service import DataService

logger = logging.getLogger(__name__)

# Auto-confirm every order warning IBKR may raise. These are the standard
# "are you sure" prompts (size/value limits, missing market data, etc.); we
# answer True to all of them so a market order goes through unattended in the
# cron job. QuestionType members are str subclasses.
_ORDER_ANSWERS = {
    getattr(QuestionType, name): True for name in dir(QuestionType) if name.isupper() and not name.startswith("_")
}


class InteractiveBrokersBroker(LiveBroker):
    """Interactive Brokers broker backed by the IBKR **Web API** (Client Portal
    REST) via the ``ibind`` library with fully headless OAuth 1.0a auth.

    This replaces the previous ``ib_async`` socket connection to a Dockerized
    IB Gateway. No gateway container, no daily browser login: the OAuth 1.0a
    live-session token is obtained programmatically and self-renews. All
    ``IBIND_OAUTH1A_*`` credentials are read from the environment by ``ibind``.
    """

    def __init__(
        self,
        account_id: str = "",
        symbol_mapper: SymbolMapper = None,
        data_service: DataService = None,
    ):
        self.name = "interactive_brokers"
        self.account_id = account_id
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()
        self.client: IbkrClient | None = None
        self._connected = False

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    def connect(self, readonly: bool = False):
        """Construct the OAuth client. ``ibind`` initializes the OAuth session
        and brokerage session on construction (init_oauth / init_brokerage_session
        default True) and starts a background tickler to keep it alive.

        The ``readonly`` flag is accepted for interface compatibility with the
        base broker but has no effect over the Web API.
        """
        if self._connected:
            return
        logger.info(f"Connecting to IBKR Web API via OAuth 1.0a (account={self.account_id or '(default)'})")
        try:
            self.client = IbkrClient(use_oauth=True, account_id=self.account_id or None)
            # Confirm the session is live; raises if auth failed.
            self.client.tickle()
            self._connected = True
            logger.info("IBKR Web API session established")
        except Exception as e:
            logger.error(f"Failed to connect to IBKR Web API: {e}")
            raise

    def disconnect(self):
        if self._connected and self.client is not None:
            try:
                self.client.stop_tickler()
            except Exception as e:
                logger.debug(f"stop_tickler failed: {e}")
            try:
                self.client.oauth_shutdown()
            except Exception as e:
                logger.debug(f"oauth_shutdown failed: {e}")
        self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def _acct(self) -> str | None:
        """The account id to pass to per-account endpoints. ibind falls back to
        its own selected account when None, but we prefer the explicit one."""
        return self.account_id or None

    # ------------------------------------------------------------------ #
    # Account state
    # ------------------------------------------------------------------ #
    def _ledger_value(self, keys: tuple) -> float:
        """Read a value from the portfolio ledger, preferring the USD sub-ledger
        then BASE. The ledger is keyed by currency: {"USD": {...}, "BASE": {...}}."""
        self.connect(readonly=True)
        if self.client is None:
            raise RuntimeError("IBKR client not connected — call connect() first")
        try:
            ledger = self.client.get_ledger(self._acct()).data or {}
        except Exception as e:
            logger.error(f"get_ledger failed: {e}")
            return 0.0

        def _pick(entry) -> float | None:
            if not isinstance(entry, dict):
                return None
            for k in keys:
                if k in entry:
                    try:
                        return float(entry[k])
                    except (TypeError, ValueError):
                        continue
            return None

        usd = _pick(ledger.get("USD"))
        if usd is not None:
            return usd
        base = _pick(ledger.get("BASE"))
        if base is not None:
            return base
        logger.warning(f"_ledger_value: no value found for keys={keys}")
        return 0.0

    def get_cash(self) -> float:
        return self._ledger_value(("cashbalance",))

    def get_total_equity(self) -> float:
        equity = self._ledger_value(("netliquidationvalue", "netliquidation"))
        if equity > 0:
            return equity
        # Fallback: sum position market values + cash.
        try:
            positions = self._raw_positions()
            mv = sum(float(p.get("mktValue", 0.0) or 0.0) for p in positions)
            cash = self.get_cash()
            total = mv + cash
            if total > 0:
                logger.warning(
                    f"get_total_equity: netLiquidation unavailable, "
                    f"falling back to positions sum (mv=${mv:.2f} + cash=${cash:.2f}) = ${total:.2f}"
                )
                return total
        except Exception as e:
            logger.error(f"get_total_equity positions fallback failed: {e}")
        return equity

    def _raw_positions(self) -> list:
        """Fetch all positions, paging until a short page is returned.
        The Web API returns up to 100 positions per page."""
        self.connect(readonly=True)
        if self.client is None:
            raise RuntimeError("IBKR client not connected — call connect() first")
        out: list[dict] = []
        page = 0
        while True:
            rows = self.client.positions(self._acct(), page=page).data or []
            out.extend(rows)
            if len(rows) < 100:
                break
            page += 1
        return out

    def get_positions(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for p in self._raw_positions():
            qty = float(p.get("position", 0.0) or 0.0)
            if qty == 0.0:
                continue
            # Prefer the plain ticker; fall back to the contract description.
            symbol = p.get("ticker") or p.get("contractDesc")
            if symbol:
                result[symbol] = result.get(symbol, 0.0) + qty
        return result

    def _get_native_price(self, broker_symbol: str) -> float:
        # Web API live prices require market-data subscriptions; defer to the
        # base class yfinance fallback (returns 0.0 here).
        return 0.0

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def _resolve_conid(self, broker_symbol: str) -> int | None:
        """Resolve a US-listed stock/ETF symbol to a single conid. Returns None
        if IBKR has no unambiguous US listing for the ticker."""
        self.connect(readonly=True)
        if self.client is None:
            raise RuntimeError("IBKR client not connected — call connect() first")
        try:
            data = self.client.stock_conid_by_symbol(broker_symbol).data or {}
            return data.get(broker_symbol)
        except Exception as e:
            logger.warning(f"conid resolution failed for {broker_symbol}: {e}")
            return None

    def place_order(
        self,
        broker_symbol: str,
        quantity: float,
        side: Literal["BUY", "SELL"],
        symbol_type: str | None = None,
    ) -> None:
        self.connect(readonly=False)
        meta = self.map_symbol(self.symbol_mapper.unmap_symbol(broker_symbol, broker_name=self.name))
        if not meta:
            raise ValueError(f"Could not map {broker_symbol} to IB contract metadata")

        sec_type = meta.get("sec_type", "STK")
        if sec_type != "STK":
            # Non-equity secdef resolution over the Web API differs from the old
            # ib_async _build_contract and isn't implemented yet. The live bots
            # trade equities/ETFs only; surface loudly rather than mis-route.
            raise NotImplementedError(
                f"Web API order routing for sec_type={sec_type} ({broker_symbol}) "
                f"is not implemented. Only STK is supported. TODO: secdef resolution."
            )

        conid = self._resolve_conid(broker_symbol)
        if conid is None:
            logger.warning(f"No unambiguous US conid for {broker_symbol}; skipping {side} order")
            return

        # Stocks must be integer-quantity; floor residuals.
        final_qty = abs(quantity)
        if final_qty < 1.0:
            logger.warning(
                f"Skipping {side} for {broker_symbol}: quantity {final_qty:.4f} is < 1 "
                f"(fractional STK not supported via API)"
            )
            return
        final_qty = float(int(final_qty))

        order = OrderRequest(
            conid=int(conid),
            side=side,
            quantity=final_qty,
            order_type="MKT",
            acct_id=self.account_id,
            tif="DAY",
        )

        logger.info(f"Submitting IB {side} order for {final_qty} {broker_symbol} (conid={conid})")
        if self.client is None:
            raise RuntimeError("IBKR client not connected — call connect() first")
        try:
            result = self.client.place_order(order, _ORDER_ANSWERS, self._acct()).data
            logger.info(f"IB Order response: {result}")
        except Exception as e:
            logger.error(f"place_order failed for {broker_symbol}: {e}")
            raise

    def cancel_open_orders(self) -> int:
        """Cancel this account's live (working) orders."""
        self.connect(readonly=False)
        if self.client is None:
            raise RuntimeError("IBKR client not connected — call connect() first")
        try:
            orders = self.client.live_orders().data or {}
        except Exception as e:
            logger.error(f"live_orders failed: {e}")
            return 0
        rows = orders.get("orders", orders) if isinstance(orders, dict) else orders
        cancelled = 0
        for o in rows or []:
            status = str(o.get("status", "")).lower()
            if status in ("filled", "cancelled", "inactive"):
                continue
            order_id = o.get("orderId") or o.get("order_id")
            if not order_id:
                continue
            try:
                self.client.cancel_order(str(order_id), self._acct())
                cancelled += 1
            except Exception as e:
                logger.debug(f"cancel_order {order_id} failed: {e}")
        return cancelled

    # ------------------------------------------------------------------ #
    # Symbol mapping
    # ------------------------------------------------------------------ #
    def map_symbol(self, yf_symbol: str) -> dict | None:
        meta = self.symbol_mapper.map_symbol(yf_symbol, broker_name=self.name)
        if not meta:
            return None

        res = {
            "symbol": meta["symbol"],
            "type": meta.get("type", "stock"),
            "sec_type": "STK",
            "exchange": "SMART",
            "currency": "USD",
            "yf_symbol": yf_symbol,
            "source": meta.get("source", "unknown"),
        }

        if meta["type"] == "forex":
            res["sec_type"] = "CASH"
            res["exchange"] = "IDEALPRO"
        elif meta["type"] == "crypto":
            res["sec_type"] = "CRYPTO"
            res["exchange"] = "PAXOS"
        elif meta["type"] == "future":
            res["sec_type"] = "FUT"
            res["exchange"] = meta.get("exchange", "CME")

        return res

    def search_symbol(self, query: str) -> list[dict]:
        self.connect(readonly=True)
        if self.client is None:
            raise RuntimeError("IBKR client not connected — call connect() first")
        try:
            results = self.client.security_stocks_by_symbol(query).data or {}
        except Exception as e:
            logger.warning(f"search_symbol failed for {query}: {e}")
            return []
        candidates = []
        for symbol, instruments in results.items():
            for inst in instruments or []:
                candidates.append(
                    {
                        "symbol": symbol,
                        "description": inst.get("name", ""),
                        "type": inst.get("assetClass", "STK"),
                        "exchange": (inst.get("contracts") or [{}])[0].get("exchange", ""),
                        "score": 100,
                    }
                )
        return candidates

    def print_account_summary(self) -> None:
        self.connect(readonly=True)
        if self.client is None:
            raise RuntimeError("IBKR client not connected — call connect() first")
        accounts = self.client.portfolio_accounts().data
        print(f"Portfolio accounts: {accounts}")
        print(f"Configured account: {self.account_id or '(none — using default)'}")

        cash = self.get_cash()
        equity = self.get_total_equity()
        print(f"\nAccount {self.account_id}")
        print(f"  Cash (USD):       {cash:>15,.2f}")
        print(f"  Net Liquidation:  {equity:>15,.2f}")

        positions = self._raw_positions()
        print(f"\nPositions ({len(positions)}):")
        if not positions:
            print("  (none)")
            return
        print(f"  {'Symbol':<12} {'Qty':>12} {'Mkt Value':>15}")
        for p in positions:
            sym = p.get("ticker") or p.get("contractDesc") or "?"
            print(f"  {sym:<12} {float(p.get('position', 0) or 0):>12.4f} {float(p.get('mktValue', 0) or 0):>15.2f}")


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    broker = InteractiveBrokersBroker(account_id=os.getenv("IB_ACCOUNT_ID", ""))
    try:
        broker.print_account_summary()
    finally:
        broker.disconnect()
