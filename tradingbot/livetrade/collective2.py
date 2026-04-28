import logging
from typing import Dict, Literal, Optional
import httpx
from .broker import LiveBroker
from .symbol_map import SymbolMapper
from utils.data_service import DataService

logger = logging.getLogger(__name__)

class Collective2Broker(LiveBroker):
    # C2 API v4 base URL
    BASE_URL = "https://api4-general.collective2.com"

    def __init__(self, api_key: str, system_id: str, symbol_mapper: SymbolMapper = None, data_service: DataService = None):
        self.name = "collective2"
        self.api_key = api_key
        self.system_id = system_id
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.client = httpx.Client(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        self.data_service = data_service or DataService()

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, json_data: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.client.post(url, json=json_data)
        if response.status_code >= 400:
            logger.error(f"C2 POST {endpoint} -> {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    def _strategy_details(self) -> dict:
        """Returns the first strategy object from GetStrategyDetails response."""
        data = self._get("Strategies/GetStrategyDetails", {"StrategyId": self.system_id})
        # v4 typically returns { "Results": [ { ... } ] } but field names vary.
        results = data.get("Results") or data.get("Strategy") or []
        if isinstance(results, list) and results:
            money_fields = {
                k: results[0].get(k)
                for k in ["Equity", "Cash", "StartingCash", "ModelAccountValue", "BuyingPower", "MarginUsed", "IsAlive"]
                if k in results[0]
            }
            logger.info(f"Strategy money/state fields: {money_fields}")
            return results[0]
        if isinstance(results, dict):
            logger.info(f"Strategy object keys: {list(results.keys())}")
            return results
        logger.warning(f"Could not extract strategy object from response: {data}")
        return {}

    def _extract_money_field(self, obj: dict, candidates: list[str]) -> float:
        """Try multiple field name candidates and prefer non-zero values
        (C2 leaves some money fields like Equity at 0 for sim/model strategies)."""
        for key in candidates:
            if key in obj and obj[key] not in (None, ""):
                try:
                    val = float(obj[key])
                except (TypeError, ValueError):
                    continue
                if val != 0:
                    return val
        return 0.0

    def get_cash(self) -> float:
        obj = self._strategy_details()
        return self._extract_money_field(obj, ["Cash", "AvailableCash", "BuyingPower", "ModelAccountCash"])

    def get_total_equity(self) -> float:
        obj = self._strategy_details()
        return self._extract_money_field(
            obj,
            ["Equity", "ModelAccountValue", "NAV", "AccountValue", "TotalValue", "ModelAccountEquity"],
        )

    def get_positions(self) -> Dict[str, float]:
        """Returns broker_symbol -> quantity."""
        data = self._get("Strategies/GetStrategyOpenPositions", {"StrategyIds": [int(self.system_id)]})
        positions = {}
        # v4 returns { "Results": [ { ... } ] }
        for pos in data.get("Results", []):
            symbol = pos.get("Symbol")
            qty = float(pos.get("Quantity", 0.0))
            positions[symbol] = qty
        return positions

    def get_latest_price(self, broker_symbol: str) -> float:
        """
        C2 API v4 doesn't have a direct quote endpoint.
        Fallback to DataService (yfinance) using inverse mapping.
        """
        logger.debug(f"Fetching fallback price for {broker_symbol} via yfinance")
        yf_symbol = self.symbol_mapper.unmap_symbol(broker_symbol)
        try:
            return self.data_service.get_latest_price(yf_symbol)
        except Exception as e:
            logger.warning(f"Could not fetch price for {broker_symbol} (yf: {yf_symbol}) via yfinance fallback: {e}")
            return 0.0

    def place_order(self, 
                    broker_symbol: str, 
                    quantity: float, 
                    side: Literal["BUY", "SELL"],
                    symbol_type: Optional[str] = None) -> None:
        
        # Determine precision based on type
        precision = 0
        if symbol_type == "crypto":
            precision = 4 # Allow 4 decimals for crypto
        elif symbol_type == "forex":
            precision = 0 # C2 forex is usually integer units

        rounded_qty = round(quantity, precision)
        if rounded_qty == 0:
            logger.warning(f"Quantity {quantity} for {broker_symbol} rounded to 0. Skipping order.")
            return

        # C2 v4 uses "forex", "crypto", "stock", "future", "option"
        if not symbol_type:
            symbol_type = "stock" # default
            
        if symbol_type == "index":
            symbol_type = "stock"

        # Side: 'Buy' or 'Sell'
        # OrderType: 'Market'
        # TIF: 'Day'
        payload = {
            "Order": {
                "StrategyId": int(self.system_id),
                "OrderType": 1,
                "Side": 1 if side == "BUY" else 2,
                "OrderQuantity": abs(rounded_qty),
                "TIF": 1,
                "C2Symbol": {
                    "FullSymbol": broker_symbol,
                    "SymbolType": symbol_type
                }
            }
        }
        
        logger.info(f"Submitting C2 order: {payload}")
        try:
            result = self._post("Strategies/NewStrategyOrder", payload)
            # C2 v4 success is usually Success: True
            if not result.get("Success"):
                # Sometimes error details are in ResponseStatus
                status = result.get("ResponseStatus", {})
                error_msg = status.get("Message") or result.get("Error", {}).get("Message") or "Unknown error"
                logger.error(f"C2 Order Failed: {error_msg} (Payload: {payload})")
            else:
                logger.info(f"C2 Order Success: {result.get('OrderID')}")
        except Exception as e:
            logger.error(f"Exception submitting C2 order: {e}")
            raise

    def map_symbol(self, yf_symbol: str) -> dict | None:
        return self.symbol_mapper.map_symbol(yf_symbol)

    def search_symbol(self, query: str) -> list[dict]:
        """
        Search for matching symbols on C2.
        """
        logger.info(f"Searching C2 for '{query}'")
        try:
            # v4 search endpoint: /Strategies/GetSupportedSymbols
            data = self._get("Strategies/GetSupportedSymbols", {"SearchText": query})
            candidates = []
            # v4 returns { "Results": [ ... ] }
            for item in data.get("Results", []):
                candidates.append({
                    "symbol": item.get("Symbol"),
                    "description": item.get("Description"),
                    "type": item.get("SymbolType", "stock"),
                    "exchange": item.get("Exchange"),
                    "score": 100
                })
            return candidates
        except Exception as e:
            logger.error(f"C2 symbol search failed: {e}")
            return []
