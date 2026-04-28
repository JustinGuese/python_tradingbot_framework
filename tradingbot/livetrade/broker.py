import logging
from abc import ABC, abstractmethod
from typing import Literal, Dict, Optional

logger = logging.getLogger(__name__)


class LiveBroker(ABC):
    # Subclasses must set these before any get_latest_price call.
    name: str
    symbol_mapper: object  # SymbolMapper
    data_service: object  # DataService

    @abstractmethod
    def get_cash(self) -> float:
        """Return the current cash balance in USD."""

    @abstractmethod
    def get_positions(self) -> Dict[str, float]:
        """Return current open positions as a dict: broker_symbol -> quantity."""

    @abstractmethod
    def get_total_equity(self) -> float:
        """Return total equity (cash + mark-to-market of positions)."""

    def _get_native_price(self, broker_symbol: str) -> float:
        """Broker-native price fetch. Return 0.0 if unavailable so the base
        class can fall back to yfinance via DataService."""
        return 0.0

    def get_latest_price(self, broker_symbol: str) -> float:
        """Fetch the latest price. Tries the broker first, then falls back to
        yfinance via DataService. Subclasses override `_get_native_price`."""
        try:
            price = self._get_native_price(broker_symbol)
            if price and price > 0:
                return price
        except Exception as e:
            logger.debug(f"{self.name} native price fetch failed for {broker_symbol}: {e}")

        yf_symbol = self.symbol_mapper.unmap_symbol(broker_symbol, broker_name=self.name)
        try:
            return self.data_service.get_latest_price(yf_symbol)
        except Exception as e:
            logger.warning(f"yfinance fallback failed for {broker_symbol} (yf: {yf_symbol}): {e}")
            return 0.0

    @abstractmethod
    def place_order(self, broker_symbol: str, quantity: float, side: Literal["BUY", "SELL"], symbol_type: Optional[str] = None) -> None:
        """Place a market order."""

    @abstractmethod
    def map_symbol(self, yf_symbol: str) -> dict | None:
        """Translate yfinance symbol (e.g. EURUSD=X) to broker-specific metadata."""

    @abstractmethod
    def search_symbol(self, query: str) -> list[dict]:
        """Return candidates: [{symbol, description, type, exchange, score}, ...]"""

    def cancel_open_orders(self) -> int:
        """Cancel any orders this broker session has previously submitted that
        are still open. Override per-broker. Returns count cancelled."""
        return 0
