import logging
from datetime import datetime, timedelta
from typing import Dict, Literal, Optional, List
import httpx
from livetrade.broker import LiveBroker
from livetrade.symbol_map import SymbolMapper
from utils.data_service import DataService

logger = logging.getLogger(__name__)

class DarwinexBroker(LiveBroker):
    """
    Darwinex DXtrade LiveBroker implementation using the /dxsca-web REST API.
    Docs: https://dxtrade.darwinex.com/dxsca-web/specs (if available)
    """
    
    # Darwinex serves both demo and live accounts off the same DXtrade host;
    # the account_id distinguishes them. Override LIVE_URL here if Darwinex
    # ever splits them onto separate subdomains.
    DEMO_URL = "https://dxtrade.darwinex.com/dxsca-web"
    LIVE_URL = "https://dxtrade.darwinex.com/dxsca-web"

    def __init__(self, username: str, password: str, account_id: Optional[str] = None, 
                 demo: bool = True, symbol_mapper: SymbolMapper = None, 
                 data_service: DataService = None):
        self.name = "darwinex"
        self.username = username
        self.password = password
        self.account_id = account_id
        self.demo = demo
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()
        
        self.base_url = self.DEMO_URL if demo else self.LIVE_URL
        self.client = httpx.Client(base_url=self.base_url, timeout=30.0)
        
        self._session_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._instrument_cache: Dict[str, dict] = {}

    def _login(self):
        """Authenticate and get a session token."""
        logger.info(f"Logging in to Darwinex DXtrade as {self.username}")
        payload = {
            "username": self.username,
            "password": self.password,
            "domain": "default"
        }
        response = self.client.post("/login", json=payload)
        response.raise_for_status()
        
        data = response.json()
        self._session_token = data.get("sessionToken")
        # DXtrade tokens usually last 24h, but let's be conservative or check if provided
        # If expiry isn't in JSON, default to 2 hours for safety.
        self._token_expires_at = datetime.now() + timedelta(hours=2)
        
        if not self.account_id:
            self._resolve_account_id()

    def _ensure_session(self):
        """Check if session is valid, login if not."""
        if not self._session_token or not self._token_expires_at or datetime.now() >= self._token_expires_at:
            self._login()

    def _get_headers(self) -> dict:
        self._ensure_session()
        return {
            "Authorization": f"DXAPI {self._session_token}",
            "Content-Type": "application/json"
        }

    def _get(self, path: str, params: dict = None) -> dict:
        response = self.client.get(path, params=params, headers=self._get_headers())
        if response.status_code == 401:
            # Force re-login once
            self._login()
            response = self.client.get(path, params=params, headers=self._get_headers())
        
        if response.status_code >= 400:
            logger.error(f"Darwinex GET {path} -> {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, json_data: dict = None) -> dict:
        response = self.client.post(path, json=json_data, headers=self._get_headers())
        if response.status_code == 401:
            self._login()
            response = self.client.post(path, json=json_data, headers=self._get_headers())
            
        if response.status_code >= 400:
            logger.error(f"Darwinex POST {path} -> {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    def _delete(self, path: str) -> dict:
        response = self.client.delete(path, headers=self._get_headers())
        if response.status_code == 401:
            self._login()
            response = self.client.delete(path, headers=self._get_headers())
        
        if response.status_code >= 400:
            logger.error(f"Darwinex DELETE {path} -> {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    def _resolve_account_id(self):
        """Fetch accounts for the user and pick the first one if not specified."""
        # Endpoint: GET /users/{username}/accounts
        try:
            data = self._get(f"/users/{self.username}/accounts")
            accounts = data.get("accounts", [])
            if not accounts:
                logger.error(f"No accounts found for Darwinex user {self.username}")
                return
            
            self.account_id = accounts[0].get("id")
            logger.info(f"Automatically selected Darwinex account: {self.account_id}")
        except Exception as e:
            logger.error(f"Failed to resolve Darwinex account ID: {e}")

    def get_cash(self) -> float:
        try:
            data = self._get(f"/accounts/{self.account_id}/metrics")
            return float(data.get("balance", 0.0))
        except Exception as e:
            logger.error(f"Failed to get Darwinex cash: {e}")
            return 0.0

    def get_total_equity(self) -> float:
        try:
            data = self._get(f"/accounts/{self.account_id}/metrics")
            return float(data.get("equity", 0.0))
        except Exception as e:
            logger.error(f"Failed to get Darwinex equity: {e}")
            return 0.0

    def get_positions(self) -> Dict[str, float]:
        """
        Return current open positions as a dict: instrumentCode -> signed quantity.
        DXtrade nets automatically, so we just aggregate positions by instrument.
        """
        try:
            data = self._get(f"/accounts/{self.account_id}/portfolio")
            positions = {}
            for pos in data.get("positions", []):
                symbol = pos.get("instrumentCode")
                qty = float(pos.get("quantity", 0.0))
                # DXtrade positions usually have a 'side' (BUY/SELL)
                side = pos.get("side", "BUY")
                signed_qty = qty if side == "BUY" else -qty
                positions[symbol] = positions.get(symbol, 0.0) + signed_qty
            return positions
        except Exception as e:
            logger.error(f"Failed to get Darwinex positions: {e}")
            return {}

    def _get_native_price(self, broker_symbol: str) -> float:
        """
        DXtrade native quotes are WebSocket-only. 
        For v1, return 0.0 to trigger yfinance fallback.
        """
        # TODO: WebSocket md subscription for live quotes
        return 0.0

    def place_order(self, broker_symbol: str, quantity: float, side: Literal["BUY", "SELL"], 
                    symbol_type: Optional[str] = None) -> None:
        """Place a MARKET order on Darwinex."""
        payload = {
            "instrument": broker_symbol,
            "quantity": abs(quantity),
            "side": side,
            "type": "MARKET"
        }
        logger.info(f"Submitting Darwinex order: {payload}")
        try:
            self._post(f"/accounts/{self.account_id}/orders", payload)
        except Exception as e:
            logger.error(f"Darwinex order failed: {e}")

    def map_symbol(self, yf_symbol: str) -> dict | None:
        """
        Two-step symbol mapping:
        1. Check manual overrides in symbol_map.json
        2. Catalog search via /instruments
        """
        # 1. Manual map check
        mapped = self.symbol_mapper.map_symbol(yf_symbol, broker_name=self.name)
        if mapped and mapped.get("source") != "default-rule":
            return mapped

        if yf_symbol in self._instrument_cache:
            return self._instrument_cache[yf_symbol]

        # 2. Search
        # Try original and .US suffix for equities
        search_queries = [yf_symbol]
        if "-" not in yf_symbol and "=" not in yf_symbol and "^" not in yf_symbol:
            search_queries.append(f"{yf_symbol}.US")

        for query in search_queries:
            results = self.search_symbol(query)
            # Look for exact match in results
            for res in results:
                if res["symbol"].upper() == query.upper() or res["symbol"].upper() == yf_symbol.upper():
                    meta = {
                        "symbol": res["symbol"],
                        "type": res.get("type", "stock"),
                        "verified": datetime.now().strftime("%Y-%m-%d"),
                        "source": "darwinex_search"
                    }
                    self._instrument_cache[yf_symbol] = meta
                    return meta
        
        logger.warning(f"Could not resolve Darwinex symbol for {yf_symbol}")
        return None

    def search_symbol(self, query: str) -> list[dict]:
        """Search for instruments matching the query."""
        try:
            data = self._get("/instruments", params={"symbol": query})
            # Based on community refs, this returns a list of instrument objects
            instruments = data if isinstance(data, list) else data.get("instruments", [])
            candidates = []
            for inst in instruments:
                candidates.append({
                    "symbol": inst.get("symbol"),
                    "description": inst.get("description"),
                    "type": inst.get("type", "stock"),
                    "currency": inst.get("currency"),
                    "score": 100 # Default score
                })
            return candidates
        except Exception as e:
            logger.error(f"Darwinex symbol search failed for {query}: {e}")
            return []

    def cancel_open_orders(self) -> int:
        """Cancel PENDING orders."""
        try:
            # GET /accounts/{id}/orders?status=PENDING
            data = self._get(f"/accounts/{self.account_id}/orders", params={"status": "PENDING"})
            orders = data if isinstance(data, list) else data.get("orders", [])
            count = 0
            for order in orders:
                order_id = order.get("id")
                if order_id:
                    self._delete(f"/accounts/{self.account_id}/orders/{order_id}")
                    count += 1
            return count
        except Exception as e:
            logger.error(f"Failed to cancel Darwinex orders: {e}")
            return 0

    def print_account_summary(self) -> None:
        try:
            metrics = self._get(f"/accounts/{self.account_id}/metrics")
            portfolio = self._get(f"/accounts/{self.account_id}/portfolio")
            
            cash = float(metrics.get("balance", 0.0))
            equity = float(metrics.get("equity", 0.0))
            positions = portfolio.get("positions", [])
            
            env_label = "DEMO" if self.demo else "LIVE"
            print(f"\nDarwinex Account ({env_label}) - {self.account_id}")
            print(f"  Cash:             {cash:>15,.2f}")
            print(f"  Equity:           {equity:>15,.2f}")
            print(f"\nPositions ({len(positions)}):")
            if not positions:
                print("  (none)")
                return
            
            print(f"  {'Symbol':<14} {'Qty':>12} {'Side':<6} {'OpenPrice':>12}")
            for p in positions:
                print(f"  {p.get('instrumentCode', ''):<14} "
                      f"{float(p.get('quantity', 0)):>12.4f} "
                      f"{p.get('side', ''):<6} "
                      f"{float(p.get('openPrice', 0)):>12.4f}")
        except Exception as e:
            logger.error(f"Failed to print Darwinex summary: {e}")


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    username = os.getenv("DARWINEX_USERNAME")
    password = os.getenv("DARWINEX_PASSWORD")
    if not username or not password:
        raise SystemExit("DARWINEX_USERNAME and DARWINEX_PASSWORD must be set in .env")

    account_id = os.getenv("DARWINEX_ACCOUNT_ID") or None
    demo = os.getenv("DARWINEX_DEMO", "true").lower() == "true"
    broker = DarwinexBroker(username=username, password=password, account_id=account_id, demo=demo)
    broker.print_account_summary()
