import logging
import uuid
from datetime import datetime
from typing import Dict, Literal, Optional
import httpx
from livetrade.broker import LiveBroker
from livetrade.symbol_map import SymbolMapper
from utils.data_service import DataService

logger = logging.getLogger(__name__)

class EtoroBroker(LiveBroker):
    """
    eToro LiveBroker implementation using the Public REST API.
    Docs: https://api-portal.etoro.com/
    """
    BASE_URL = "https://public-api.etoro.com"

    def __init__(self, api_key: str, user_key: str, demo: bool = True,
                 symbol_mapper: SymbolMapper = None, data_service: DataService = None):
        self.name = "etoro"
        self.api_key = api_key
        self.user_key = user_key
        self.demo = demo
        self._env = "demo/" if demo else ""
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()
        
        # httpx client with persistent headers
        self.client = httpx.Client(
            timeout=30.0,
            headers={
                "x-api-key": self.api_key,
                "x-user-key": self.user_key,
                "Content-Type": "application/json"
            }
        )
        
        # Caches
        self._instrument_cache: Dict[str, str] = {}  # ticker -> instrument_id
        self._position_id_map: Dict[str, str] = {}   # instrument_id -> positionId (last seen)

    def _get_headers(self) -> dict:
        """Generate unique request headers for eToro API."""
        return {
            "x-request-id": str(uuid.uuid4())
        }

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.client.get(url, params=params, headers=self._get_headers())
        if response.status_code >= 400:
            logger.error(f"eToro GET {endpoint} -> {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, json_data: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.client.post(url, json=json_data, headers=self._get_headers())
        if response.status_code >= 400:
            logger.error(f"eToro POST {endpoint} -> {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    def _portfolio_endpoint(self) -> str:
        return f"api/v1/trading/info/{self._env}portfolio"

    def _portfolio(self) -> dict:
        """Fetch portfolio response and unwrap the 'clientPortfolio' envelope."""
        data = self._get(self._portfolio_endpoint())
        if not isinstance(data, dict):
            return {}
        return data.get("clientPortfolio") or data.get("portfolio") or data

    def get_cash(self) -> float:
        try:
            p = self._portfolio()
            return float(p.get("credit", 0.0) or 0.0)
        except Exception as e:
            logger.error(f"Failed to get eToro cash: {e}")
            return 0.0

    def get_total_equity(self) -> float:
        """eToro doesn't return equity directly; compute as credit + sum(position notional)."""
        try:
            p = self._portfolio()
            cash = float(p.get("credit", 0.0) or 0.0)
            notional = 0.0
            for pos in p.get("positions", []) or []:
                units = float(pos.get("units") or 0.0)
                rate = float(pos.get("openRate") or 0.0)
                amount = float(pos.get("amount") or 0.0)
                # Prefer mark-to-market via current price; fall back to opening amount.
                instr_id = str(pos.get("instrumentID") or pos.get("instrumentId") or "")
                price = self._get_native_price(instr_id) if instr_id else 0.0
                if price > 0 and units > 0:
                    notional += units * price
                elif units > 0 and rate > 0:
                    notional += units * rate
                else:
                    notional += amount
            return cash + notional
        except Exception as e:
            logger.error(f"Failed to get eToro equity: {e}")
            return 0.0

    def get_positions(self) -> Dict[str, float]:
        """
        Return current open positions as a dict: instrument_id -> quantity.
        Also updates the internal position_id_map for closing orders.
        """
        try:
            p = self._portfolio()
            positions = {}
            self._position_id_map = {}

            for pos in p.get("positions", []) or []:
                instr_id = str(pos.get("instrumentID") or pos.get("instrumentId") or "")
                if not instr_id:
                    continue
                units = float(pos.get("units") or 0.0)
                positions[instr_id] = positions.get(instr_id, 0.0) + units
                self._position_id_map[instr_id] = str(pos.get("positionID") or pos.get("positionId") or "")
                
            return positions
        except Exception as e:
            logger.error(f"Failed to get eToro positions: {e}")
            return {}

    def _get_native_price(self, broker_symbol: str) -> float:
        """Fetch the latest traded price for an instrument ID via market rates."""
        try:
            data = self._get(
                "api/v1/market-data/instruments/rates",
                {"instrumentIds": broker_symbol},
            )
            rates = data.get("rates") or []
            if not rates:
                return 0.0
            r = rates[0]
            last = float(r.get("lastExecution") or 0.0)
            if last > 0:
                return last
            bid = float(r.get("bid") or 0.0)
            ask = float(r.get("ask") or 0.0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return bid or ask or 0.0
        except Exception as e:
            logger.debug(f"eToro native price fetch failed for {broker_symbol}: {e}")
        return 0.0

    def place_order(self, 
                    broker_symbol: str, 
                    quantity: float, 
                    side: Literal["BUY", "SELL"], 
                    symbol_type: Optional[str] = None) -> None:
        """
        Place a market order.
        BUY: uses 'market-open-orders/by-amount' (USD notional)
        SELL: uses 'market-close-orders/positions/{positionId}' (Full close)
        """
        if side == "BUY":
            price = self.get_latest_price(broker_symbol)
            if price <= 0:
                logger.error(f"Cannot place BUY order for {broker_symbol}: price is {price}")
                return
            
            amount = abs(quantity * price)
            payload = {
                "InstrumentID": int(broker_symbol),
                "IsBuy": True,
                "Leverage": 1,
                "Amount": amount
            }
            logger.info(f"Submitting eToro BUY order: {payload}")
            endpoint = f"api/v1/trading/execution/{self._env}market-open-orders/by-amount"
            try:
                self._post(endpoint, payload)
            except Exception as e:
                logger.error(f"eToro BUY order failed: {e}")
        
        else:
            # SELL -> Close existing position
            # We need the positionId from the last get_positions() call
            pos_id = self._position_id_map.get(broker_symbol)
            if not pos_id:
                # One retry: refresh positions
                logger.info(f"Position ID for {broker_symbol} not found, refreshing positions...")
                self.get_positions()
                pos_id = self._position_id_map.get(broker_symbol)
            
            if not pos_id:
                logger.error(f"Cannot place SELL order for {broker_symbol}: no positionId found.")
                return

            logger.info(f"Submitting eToro SELL (close) order for position {pos_id} (ID: {broker_symbol})")
            endpoint = f"api/v1/trading/execution/{self._env}market-close-orders/positions/{pos_id}"
            try:
                self._post(endpoint, {"InstrumentId": int(broker_symbol)})
            except Exception as e:
                logger.error(f"eToro SELL order failed: {e}")

    def map_symbol(self, yf_symbol: str) -> dict | None:
        """
        Translate yfinance symbol to eToro instrument ID.
        Uses search API and caches results.
        """
        # 1. Check if we already have it in the symbol_map.json (manual overrides)
        mapped = self.symbol_mapper.map_symbol(yf_symbol, broker_name=self.name)
        if mapped and mapped.get("source") != "default-rule":
            return mapped

        # 2. Resolve via eToro search API. eToro uses bare tickers (no -USD suffix
        #    for crypto), so strip common quote-currency suffixes.
        ticker = mapped["symbol"] if mapped else yf_symbol
        query_ticker = ticker
        for suffix in ("-USD", "-USDT", "USD=X"):
            if query_ticker.endswith(suffix):
                query_ticker = query_ticker[: -len(suffix)]
                break

        if ticker in self._instrument_cache:
            instr_id = self._instrument_cache[ticker]
        else:
            try:
                logger.info(f"Resolving eToro instrument for {ticker} (query={query_ticker})")
                data = self._get(
                    "api/v1/market-data/search",
                    {
                        "internalSymbolFull": query_ticker,
                        "fields": "instrumentId,internalSymbolFull,symbol,instrumentType",
                    },
                )
                items = data.get("items") or data.get("results") or []
                if not items:
                    logger.warning(f"No eToro instrument found for ticker: {ticker}")
                    return None

                # Prefer exact match on internalSymbolFull, else first result
                match = next(
                    (i for i in items if str(i.get("internalSymbolFull", "")).upper() == query_ticker.upper()),
                    items[0],
                )
                instr_id = str(match.get("instrumentId"))
                self._instrument_cache[ticker] = instr_id
            except Exception as e:
                logger.error(f"Failed to search eToro instrument for {ticker}: {e}")
                return None

        return {
            "symbol": instr_id,
            "type": "stock",
            "verified": datetime.now().strftime("%Y-%m-%d"),
            "source": "etoro_search"
        }

    def print_account_summary(self) -> None:
        portfolio = self._portfolio()
        cash = float(portfolio.get("credit", 0.0) or 0.0)
        equity = self.get_total_equity()
        positions = portfolio.get("positions", []) or []
        env_label = "DEMO" if self.demo else "LIVE"
        print(f"\neToro Account ({env_label})")
        print(f"  Cash:             {cash:>15,.2f}")
        print(f"  Equity:           {equity:>15,.2f}")
        print(f"\nPositions ({len(positions)}):")
        if not positions:
            print("  (none)")
            return
        print(f"  {'InstrumentID':<14} {'Units':>12} {'OpenRate':>12} {'Amount':>12} {'PositionID':<14}")
        for p in positions:
            print(f"  {str(p.get('instrumentID', '')):<14} "
                  f"{float(p.get('units', 0) or 0):>12.4f} "
                  f"{float(p.get('openRate', 0) or 0):>12.4f} "
                  f"{float(p.get('amount', 0) or 0):>12.2f} "
                  f"{str(p.get('positionID', '')):<14}")

    def search_symbol(self, query: str) -> list[dict]:
        """Search for matching symbols on eToro."""
        logger.info(f"Searching eToro for '{query}'")
        try:
            data = self._get(
                "api/v1/market-data/search",
                {
                    "searchText": query,
                    "fields": "instrumentId,internalSymbolFull,symbol,instrumentDisplayName,exchangeName,instrumentType",
                },
            )
            candidates = []
            for item in (data.get("items") or data.get("results") or []):
                candidates.append({
                    "symbol": str(item.get("instrumentId")),
                    "description": item.get("instrumentDisplayName") or item.get("internalSymbolFull"),
                    "type": "stock",
                    "exchange": item.get("exchangeName"),
                    "score": 100
                })
            return candidates
        except Exception as e:
            logger.error(f"eToro symbol search failed: {e}")
            return []


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")
    if not api_key or not user_key:
        raise SystemExit("ETORO_API_KEY and ETORO_USER_KEY must be set in .env")

    demo = os.getenv("ETORO_DEMO", "true").lower() == "true"
    broker = EtoroBroker(api_key=api_key, user_key=user_key, demo=demo)
    broker.print_account_summary()
