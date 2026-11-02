import logging
import time
from typing import Literal

from ib_async import IB, Contract, Crypto, Forex, Future, MarketOrder, Stock, util

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.symbol_map import SymbolMapper
from tradingbot.utils.data_service import DataService

logger = logging.getLogger(__name__)


class InteractiveBrokersBroker(LiveBroker):
    """Interactive Brokers broker backed by the TWS socket API (``ib_async``)
    against the IB Gateway deployed in the cluster
    (``ib-gateway-service.interactivebrokersgateway:4002``, paper).

    The gateway owns the IBKR login (and its daily restart); this adapter only
    opens a socket session to it. Locally, ``kubectl port-forward`` the service
    and point ``IB_GATEWAY_HOST`` / ``IB_GATEWAY_PORT`` at it.

    ``client_id`` must be stable across runs: ``cancel_open_orders`` only sees
    (and only cancels) orders placed under the same clientId, so a previous
    run's still-working orders are cancelled and manual orders are left alone.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 17,
        account_id: str = "",
        symbol_mapper: SymbolMapper | None = None,
        data_service: DataService | None = None,
    ):
        self.name = "interactive_brokers"
        self.host = host
        self.port = port
        self.client_id = client_id
        self.account_id = account_id
        self.ib = IB()
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()
        self._connected = False

    @property
    def account_ref(self) -> str:
        return str(self.account_id or "")

    @property
    def is_sandbox(self) -> bool:
        # IBKR paper accounts are the DU-prefixed ones (live accounts are U…).
        # There is no API flag for this — the account id is the signal.
        return str(self.account_id or "").upper().startswith("DU")

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    def connect(self, readonly: bool = False):
        if self._connected:
            return
        logger.info(f"Connecting to IB at {self.host}:{self.port} (clientId={self.client_id}, readonly={readonly})")
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=readonly, timeout=30)
        except Exception as e:
            logger.error(f"Failed to connect to IB: {e}")
            raise
        self._connected = True
        accounts = self.ib.managedAccounts()
        if self.account_id and accounts and self.account_id not in accounts:
            self.disconnect()
            raise ValueError(f"IB_ACCOUNT_ID {self.account_id!r} is not managed by this gateway (managed: {accounts})")

    def disconnect(self):
        if self._connected:
            self.ib.disconnect()
            self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    # ------------------------------------------------------------------ #
    # Account state
    # ------------------------------------------------------------------ #
    def _read_tags(self, tags: tuple) -> float:
        """Look up a value across accountValues() and accountSummary().

        accountValues() is fed by reqAccountUpdates (auto-issued on connect)
        and uses tag names like 'NetLiquidationByCurrency'. accountSummary()
        uses 'NetLiquidation'. Pass all candidate tag names. Prefers USD,
        then BASE/empty currency."""
        self.connect(readonly=True)

        def _pick(rows) -> float | None:
            usd_val = None
            base_val = None
            for r in rows:
                if r.tag not in tags:
                    continue
                if self.account_id and getattr(r, "account", "") not in (self.account_id, "", "All"):
                    continue
                try:
                    v = float(r.value)
                except (TypeError, ValueError):
                    continue
                if r.currency == "USD":
                    usd_val = v
                elif r.currency in ("", "BASE"):
                    base_val = v
            return usd_val if usd_val is not None else base_val

        # Wait briefly for reqAccountUpdates to populate accountValues.
        deadline = time.time() + 5.0
        while True:
            picked = _pick(self.ib.accountValues())
            if picked is not None:
                return picked
            if time.time() >= deadline:
                break
            self.ib.sleep(0.25)

        # Fallback: explicit accountSummary request.
        rows = self.ib.accountSummary(self.account_id) if self.account_id else self.ib.accountSummary()
        picked = _pick(rows)
        if picked is not None:
            return picked
        logger.warning(f"_read_tags: no value found for tags={tags} account={self.account_id}")
        return 0.0

    def get_cash(self) -> float:
        return self._read_tags(("TotalCashValue", "CashBalance"))

    def get_total_equity(self) -> float:
        equity = self._read_tags(("NetLiquidation", "NetLiquidationByCurrency"))
        if equity > 0:
            return equity
        # Last resort: portfolio market value + cash.
        try:
            items = self.ib.portfolio(self.account_id) if self.account_id else self.ib.portfolio()
            mv = sum(float(getattr(p, "marketValue", 0.0) or 0.0) for p in items)
            cash = self.get_cash()
            total = mv + cash
            if total > 0:
                logger.warning(
                    f"get_total_equity: NetLiquidation unavailable, "
                    f"falling back to portfolio sum (mv=${mv:.2f} + cash=${cash:.2f}) = ${total:.2f}"
                )
                return total
        except Exception as e:
            logger.error(f"get_total_equity portfolio fallback failed: {e}")
        return equity

    def get_positions(self) -> dict[str, float]:
        self.connect(readonly=True)
        result: dict[str, float] = {}
        for p in self.ib.positions(self.account_id):
            qty = float(p.position)
            if qty == 0.0:
                continue
            result[p.contract.symbol] = result.get(p.contract.symbol, 0.0) + qty
        return result

    def _get_native_price(self, broker_symbol: str) -> float:
        self.connect(readonly=True)
        yf_symbol = self.symbol_mapper.unmap_symbol(broker_symbol, broker_name=self.name)
        meta = self.map_symbol(yf_symbol)
        if not meta:
            return 0.0
        tickers = self.ib.reqTickers(self._build_contract(meta))
        if not tickers:
            return 0.0
        price = tickers[0].marketPrice()
        if price and not util.isNan(price) and price > 0:
            return float(price)
        return 0.0

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def _resolve_stk_contract(self, symbol: str) -> Contract | None:
        """Ask IB for valid listings of ``symbol`` and pick the best one.

        Tries SMART/USD first, then any-exchange/USD, then any/any, preferring
        USD listings. Returns a contract routed via SMART for execution, or
        None if IB has no listing for this ticker."""
        self.connect(readonly=True)
        attempts = [Stock(symbol, "SMART", "USD"), Stock(symbol, "", "USD"), Stock(symbol, "", "")]
        details: list = []
        for c in attempts:
            try:
                details = self.ib.reqContractDetails(c)
            except Exception as e:
                logger.debug(
                    f"reqContractDetails failed for {symbol} on {c.exchange or 'ANY'}/{c.currency or 'ANY'}: {e}"
                )
                details = []
            if details:
                break
        if not details:
            return None
        usd = [d for d in details if d.contract.currency == "USD"]
        chosen = (usd or details)[0].contract
        if chosen.secType == "STK" and chosen.primaryExchange and chosen.exchange != "SMART":
            chosen.exchange = "SMART"
        return chosen

    def _build_contract(self, meta: dict) -> Contract:
        sec_type = meta.get("sec_type", "STK")
        symbol = meta["symbol"]
        exchange = meta.get("exchange", "SMART")
        currency = meta.get("currency", "USD")

        if sec_type == "CASH":
            # IB Forex symbols are the base currency (e.g. 'EUR' for EURUSD)
            if len(symbol) == 6:
                return Forex(symbol=symbol[:3], exchange=exchange, currency=symbol[3:])
            return Forex(symbol=symbol, exchange=exchange, currency=currency)
        if sec_type == "CRYPTO":
            return Crypto(symbol, exchange, currency)
        if sec_type == "FUT":
            return Future(symbol, exchange=exchange, currency=currency)
        return Stock(symbol, exchange, currency)

    def place_order(
        self,
        broker_symbol: str,
        quantity: float,
        side: Literal["BUY", "SELL"],
        symbol_type: str | None = None,
    ) -> None:
        self.connect(readonly=False)
        yf_symbol = self.symbol_mapper.unmap_symbol(broker_symbol, broker_name=self.name)
        meta = self.map_symbol(yf_symbol)
        if not meta:
            raise ValueError(f"Could not map {broker_symbol} to IB contract metadata")

        # Contract resolution: SELLs reuse the held position's contract (correct
        # exchange/conId); STK BUYs ask IB via reqContractDetails, which avoids
        # guessing SMART/USD for non-US listings; anything else falls back to
        # the symbol_map metadata.
        contract = None
        if side == "SELL":
            for p in self.ib.positions(self.account_id):
                if p.contract.symbol == broker_symbol and float(p.position) != 0.0:
                    contract = p.contract
                    break
        if contract is None and meta.get("sec_type", "STK") == "STK":
            contract = self._resolve_stk_contract(broker_symbol)
            if contract is None:
                logger.warning(
                    f"reqContractDetails returned no listings for {broker_symbol}; falling back to symbol_map metadata"
                )
        if contract is None:
            contract = self._build_contract(meta)

        # Stocks must be integer-quantity; floor residuals.
        final_qty = abs(quantity)
        if meta.get("sec_type") == "STK":
            if final_qty < 1.0:
                logger.warning(
                    f"Skipping {side} for {broker_symbol}: quantity {final_qty:.4f} is < 1 "
                    f"(fractional STK not supported via API)"
                )
                return
            final_qty = float(int(final_qty))

        order = MarketOrder(side, final_qty)
        if self.account_id:
            order.account = self.account_id

        logger.info(f"Submitting IB {side} order for {final_qty} {broker_symbol}")
        trade = self.ib.placeOrder(contract, order)

        # Give the gateway a moment to acknowledge, so a rejection surfaces here
        # instead of vanishing: an unacknowledged order looks identical to a
        # filled one from the caller's side.
        deadline = time.time() + 5.0
        while time.time() < deadline and trade.orderStatus.status in ("", "PendingSubmit", "ApiPending"):
            self.ib.sleep(0.25)
        status = trade.orderStatus.status
        logger.info(f"IB Order Status: {status}")
        if status in ("Cancelled", "ApiCancelled", "Inactive"):
            last = trade.log[-1].message if trade.log else ""
            raise RuntimeError(f"IB rejected {side} {final_qty} {broker_symbol}: {status} {last}".strip())

    def cancel_open_orders(self) -> int:
        """Cancel open orders submitted under this clientId."""
        self.connect(readonly=False)
        open_trades = [t for t in self.ib.openTrades() if not t.isDone() and t.order.clientId == self.client_id]
        if not open_trades:
            return 0
        for t in open_trades:
            self.ib.cancelOrder(t.order)
        self.ib.sleep(1)  # let cancellations propagate
        return len(open_trades)

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
        candidates = []
        for m in self.ib.reqMatchingSymbols(query) or []:
            c = m.contract
            if c is None:
                continue
            candidates.append(
                {
                    "symbol": c.symbol,
                    "description": f"{c.primaryExchange} {c.currency} ({c.secType})",
                    "type": c.secType,
                    "exchange": c.primaryExchange,
                    "score": 100,
                }
            )
        return candidates

    # print_account_summary() itself lives on LiveBroker; these are IBKR's hooks.
    def _pre_summary_lines(self) -> list[str]:
        self.connect(readonly=True)
        return [
            f"Managed accounts: {self.ib.managedAccounts()}",
            f"Configured account: {self.account_id or '(none — using first managed)'}",
        ]

    def _summary_header(self) -> str:
        return f"Account {self.account_id or (self.ib.managedAccounts() or [''])[0]}"

    def _account_lines(self) -> list[str]:
        return [
            self._summary_line("Cash (USD):", self.get_cash()),
            self._summary_line("Net Liquidation:", self.get_total_equity()),
        ]

    def _position_rows(self) -> tuple[str, list[str]]:
        self.connect(readonly=True)
        header = f"  {'Symbol':<12} {'SecType':<8} {'Exchange':<10} {'Currency':<8} {'Qty':>12} {'Avg Cost':>12}"
        rows = [
            f"  {p.contract.symbol:<12} {p.contract.secType:<8} "
            f"{(p.contract.exchange or p.contract.primaryExchange or ''):<10} {p.contract.currency:<8} "
            f"{float(p.position):>12.4f} {float(p.avgCost):>12.4f}"
            for p in self.ib.positions(self.account_id)
        ]
        return header, rows


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    broker = InteractiveBrokersBroker(
        host=os.getenv("IB_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("IB_GATEWAY_PORT", "4002")),
        client_id=int(os.getenv("IB_CLIENT_ID", "17")),
        account_id=os.getenv("IB_ACCOUNT_ID", ""),
    )
    try:
        broker.print_account_summary()
    finally:
        broker.disconnect()
