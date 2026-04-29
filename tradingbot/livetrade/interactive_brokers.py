import logging
import asyncio
import time
from typing import Dict, Literal, List, Optional
from ib_async import IB, Stock, Forex, Crypto, Future, MarketOrder, util
from livetrade.broker import LiveBroker
from livetrade.symbol_map import SymbolMapper
from utils.data_service import DataService

logger = logging.getLogger(__name__)

class InteractiveBrokersBroker(LiveBroker):
    def __init__(self, 
                 host: str = "127.0.0.1", 
                 port: int = 4004, 
                 client_id: int = 17, 
                 account_id: str = "",
                 symbol_mapper: SymbolMapper = None,
                 data_service: DataService = None):
        self.name = "interactive_brokers"
        self.host = host
        self.port = port
        self.client_id = client_id
        self.account_id = account_id
        self.ib = IB()
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()
        self._connected = False

    def connect(self, readonly: bool = False):
        if not self._connected:
            logger.info(f"Connecting to IB at {self.host}:{self.port} (clientId={self.client_id}, readonly={readonly})")
            try:
                self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=readonly)
                self._connected = True
            except Exception as e:
                logger.error(f"Failed to connect to IB: {e}")
                raise

    def disconnect(self):
        if self._connected:
            self.ib.disconnect()
            self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def _read_tags(self, tags: tuple) -> float:
        """Look up a value across accountValues() and accountSummary().

        accountValues() is fed by reqAccountUpdates (auto-issued on connect)
        and uses tag names like 'NetLiquidationByCurrency'. accountSummary()
        uses 'NetLiquidation'. Pass all candidate tag names. Prefers USD,
        then BASE/empty currency."""
        self.connect(readonly=True)

        def _pick(rows) -> Optional[float]:
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
            if usd_val is not None:
                return usd_val
            if base_val is not None:
                return base_val
            return None

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
        # Last-resort fallback: sum portfolio marketValue + cash. Portfolio is
        # populated by reqAccountUpdates and we see updatePortfolio events on
        # connect, so this should be reliable when accountValues lookup fails.
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

    def get_positions(self) -> Dict[str, float]:
        self.connect(readonly=True)
        positions = self.ib.positions(self.account_id)
        # IB positions returns [Position(account, contract, position, avgCost), ...]
        return {p.contract.symbol: float(p.position) for p in positions}

    def _get_native_price(self, broker_symbol: str) -> float:
        self.connect(readonly=True)
        yf_symbol = self.symbol_mapper.unmap_symbol(broker_symbol, broker_name=self.name)
        meta = self.map_symbol(yf_symbol)
        if not meta:
            return 0.0
        contract = self._build_contract(meta)
        tickers = self.ib.reqTickers(contract)
        if not tickers:
            return 0.0
        price = tickers[0].marketPrice()
        if price and not util.isNan(price) and price > 0:
            return float(price)
        return 0.0

    def _resolve_stk_contract(self, symbol: str):
        """Ask IB for valid listings of `symbol` and pick the best one.

        Tries SMART/USD first, then any-exchange/USD, then any/any. Among the
        results, prefers USD listings. Returns a fully-qualified contract
        (with conId/primaryExchange) routed via SMART for execution, or None
        if IB has no listing for this ticker."""
        self.connect(readonly=True)
        attempts = [Stock(symbol, "SMART", "USD"), Stock(symbol, "", "USD"), Stock(symbol, "", "")]
        details = []
        for c in attempts:
            try:
                details = self.ib.reqContractDetails(c)
            except Exception as e:
                logger.debug(f"reqContractDetails failed for {symbol} on {c.exchange or 'ANY'}/{c.currency or 'ANY'}: {e}")
                details = []
            if details:
                break
        if not details:
            return None
        usd = [d for d in details if d.contract.currency == "USD"]
        chosen = (usd or details)[0].contract
        # Route via SMART for execution; primaryExchange disambiguates the listing.
        if chosen.secType == "STK" and chosen.primaryExchange and chosen.exchange != "SMART":
            chosen.exchange = "SMART"
        return chosen

    def _build_contract(self, meta: dict):
        sec_type = meta.get("sec_type", "STK")
        symbol = meta.get("symbol")
        exchange = meta.get("exchange", "SMART")
        currency = meta.get("currency", "USD")

        if sec_type == "STK":
            return Stock(symbol, exchange, currency)
        elif sec_type == "CASH":
            # IB Forex symbols are the base currency (e.g. 'EUR' for EURUSD)
            if len(symbol) == 6:
                base = symbol[:3]
                quote = symbol[3:]
                return Forex(symbol=base, exchange=exchange, currency=quote)
            return Forex(symbol=symbol, exchange=exchange, currency=currency)
        elif sec_type == "CRYPTO":
            return Crypto(symbol, exchange, currency)
        elif sec_type == "FUT":
            return Future(symbol, exchange=exchange, currency=currency)
        return Stock(symbol, exchange, currency)

    def place_order(self, broker_symbol: str, quantity: float, side: Literal["BUY", "SELL"], symbol_type: Optional[str] = None) -> None:
        self.connect(readonly=False)
        yf_symbol = self.symbol_mapper.unmap_symbol(broker_symbol, broker_name=self.name)
        meta = self.map_symbol(yf_symbol)
        if not meta:
            raise ValueError(f"Could not map {broker_symbol} to IB contract metadata")

        # Contract resolution strategy:
        #   1. SELLs: reuse the existing position's contract (correct exchange/conId).
        #   2. STK BUYs (or SELLs on no-position): ask IB via reqContractDetails
        #      to pick the right listing. Avoids guessing SMART/USD which fails
        #      for non-US listings (CSNDX/EBS, ADRs, LSE/XETRA names).
        #   3. Non-STK or unresolvable: fall back to symbol_map metadata.
        contract = None
        if side == "SELL":
            for p in self.ib.positions(self.account_id):
                if p.contract.symbol == broker_symbol and float(p.position) != 0.0:
                    contract = p.contract
                    break
        if contract is None and meta.get("sec_type", "STK") == "STK":
            contract = self._resolve_stk_contract(broker_symbol)
            if contract is None:
                logger.warning(f"reqContractDetails returned no listings for {broker_symbol}; falling back to symbol_map metadata")
        if contract is None:
            contract = self._build_contract(meta)

        # Stocks must be integer-quantity; floor residuals.
        final_qty = abs(quantity)
        if meta.get("sec_type") == "STK":
            if final_qty < 1.0:
                logger.warning(f"Skipping {side} for {broker_symbol}: quantity {final_qty:.4f} is < 1 (fractional STK not supported via API)")
                return
            final_qty = float(int(final_qty))

        order = MarketOrder(side, final_qty)
        
        logger.info(f"Submitting IB {side} order for {final_qty} {broker_symbol}")
        trade = self.ib.placeOrder(contract, order)
        
        # Brief pause to catch the first acknowledgement/status update
        self.ib.sleep(0.3)
        logger.info(f"IB Order Status: {trade.orderStatus.status}")

    def cancel_open_orders(self) -> int:
        """Cancel open orders submitted by this clientId."""
        self.connect(readonly=False)
        # Filter by clientId to avoid cancelling manual orders or orders from other bots
        open_trades = [t for t in self.ib.openTrades() if not t.isDone() and t.order.clientId == self.client_id]
        if not open_trades:
            return 0
            
        for t in open_trades:
            self.ib.cancelOrder(t.order)
            
        self.ib.sleep(1)  # let cancellations propagate
        return len(open_trades)

    def map_symbol(self, yf_symbol: str) -> dict | None:
        meta = self.symbol_mapper.map_symbol(yf_symbol, broker_name=self.name)
        if not meta:
            return None
        
        # Add IB specific fields
        res = {
            "symbol": meta["symbol"],
            "type": meta.get("type", "stock"),
            "sec_type": "STK",
            "exchange": "SMART",
            "currency": "USD",
            "yf_symbol": yf_symbol,
            "source": meta.get("source", "unknown")
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
        matches = self.ib.reqMatchingSymbols(query)
        candidates = []
        for m in matches:
            candidates.append({
                "symbol": m.contract.symbol,
                "description": f"{m.contract.primaryExchange} {m.contract.currency} ({m.contract.secType})",
                "type": m.contract.secType,
                "exchange": m.contract.primaryExchange,
                "score": 100
            })
        return candidates

    def print_account_summary(self) -> None:
        self.connect(readonly=True)
        accounts = self.ib.managedAccounts()
        print(f"Managed accounts: {accounts}")
        print(f"Configured account: {self.account_id or '(none — using first managed)'}")
        acct = self.account_id or (accounts[0] if accounts else "")
        if not acct:
            print("No account available.")
            return

        cash = self.get_cash()
        equity = self.get_total_equity()
        print(f"\nAccount {acct}")
        print(f"  Cash (USD):       {cash:>15,.2f}")
        print(f"  Net Liquidation:  {equity:>15,.2f}")

        positions = self.ib.positions(acct)
        print(f"\nPositions ({len(positions)}):")
        if not positions:
            print("  (none)")
            return
        print(f"  {'Symbol':<12} {'SecType':<8} {'Exchange':<10} {'Currency':<8} {'Qty':>12} {'Avg Cost':>12}")
        for p in positions:
            c = p.contract
            print(f"  {c.symbol:<12} {c.secType:<8} {(c.exchange or c.primaryExchange or ''):<10} {c.currency:<8} {float(p.position):>12.4f} {float(p.avgCost):>12.4f}")


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    broker = InteractiveBrokersBroker(
        host=os.getenv("IB_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("IB_GATEWAY_PORT", "4004")),
        client_id=int(os.getenv("IB_CLIENT_ID", "19")),
        account_id=os.getenv("IB_ACCOUNT_ID", ""),
    )
    try:
        broker.print_account_summary()
    finally:
        broker.disconnect()
