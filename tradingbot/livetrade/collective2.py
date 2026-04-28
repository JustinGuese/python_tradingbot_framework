import logging
from typing import Dict, Literal, Optional
import httpx
from livetrade.broker import LiveBroker
from livetrade.symbol_map import SymbolMapper
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
            return results[0]
        if isinstance(results, dict):
            return results
        logger.warning(f"Could not extract strategy object from response: {data}")
        return {}

    @staticmethod
    def _money(obj: dict, key: str) -> float:
        try:
            return float(obj.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def get_cash(self) -> float:
        return self._money(self._strategy_details(), "ModelAccountCash")

    def get_total_equity(self) -> float:
        return self._money(self._strategy_details(), "ModelAccountValue")

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

    # C2 API v4 has no native quote endpoint — base class falls back to yfinance.

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
        return self.symbol_mapper.map_symbol(yf_symbol, broker_name=self.name)

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

    def print_account_summary(self) -> None:
        obj = self._strategy_details()
        cash = self.get_cash()
        equity = self.get_total_equity()
        print(f"\nCollective2 Strategy {self.system_id}")
        print(f"  IsAlive:          {obj.get('IsAlive', '?')}")
        print(f"  Cash:             {cash:>15,.2f}")
        print(f"  Equity:           {equity:>15,.2f}")

        positions_data = self._get("Strategies/GetStrategyOpenPositions", {"StrategyIds": [int(self.system_id)]})
        results = positions_data.get("Results", [])
        print(f"\nPositions ({len(results)}):")
        if not results:
            print("  (none)")
            return
        print(f"  {'Symbol':<14} {'Type':<8} {'Qty':>12} {'AvgPrice':>12} {'OpenPnL':>12}")
        for p in results:
            print(f"  {str(p.get('Symbol','')):<14} {str(p.get('SymbolType','')):<8} "
                  f"{float(p.get('Quantity', 0)):>12.4f} "
                  f"{float(p.get('OpenPrice', 0) or 0):>12.4f} "
                  f"{float(p.get('OpenPnL', 0) or 0):>12.2f}")


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    api_key = os.getenv("COLLECTIVE2_API_KEY")
    system_id = os.getenv("COLLECTIVE2_SYSTEM_ID")
    if not api_key or not system_id:
        raise SystemExit("COLLECTIVE2_API_KEY and COLLECTIVE2_SYSTEM_ID must be set in .env")

    broker = Collective2Broker(api_key=api_key, system_id=system_id)
    broker.print_account_summary()
