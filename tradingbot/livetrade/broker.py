from abc import ABC, abstractmethod
from typing import Literal, Dict

class LiveBroker(ABC):
    @abstractmethod
    def get_cash(self) -> float:
        """Return the current cash balance in USD."""
        pass

    @abstractmethod
    def get_positions(self) -> Dict[str, float]:
        """Return current open positions as a dict: broker_symbol -> quantity."""
        pass

    @abstractmethod
    def get_total_equity(self) -> float:
        """Return total equity (cash + mark-to-market of positions)."""
        pass

    @abstractmethod
    def get_latest_price(self, broker_symbol: str) -> float:
        """Fetch the latest price for a broker-specific symbol."""
        pass

    @abstractmethod
    def place_order(self, broker_symbol: str, quantity: float, side: Literal["BUY", "SELL"]) -> None:
        """Place a market order."""
        pass

    @abstractmethod
    def map_symbol(self, yf_symbol: str) -> dict | None:
        """Translate yfinance symbol (e.g. EURUSD=X) to broker-specific metadata."""
        pass

    @abstractmethod
    def search_symbol(self, query: str) -> list[dict]:
        """Return candidates: [{symbol, description, type, exchange, score}, ...]"""
        pass
