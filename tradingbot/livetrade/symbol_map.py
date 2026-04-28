import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class SymbolMapper:
    def __init__(self, map_file: str | None = None):
        if map_file is None:
            map_file = str(Path(__file__).parent / "symbol_map.json")
        
        self.overrides = {}
        if os.path.exists(map_file):
            try:
                with open(map_file, "r") as f:
                    self.overrides = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load symbol map from {map_file}: {e}")

    def map_symbol(self, yf_symbol: str) -> Optional[Dict]:
        """
        Translate yfinance symbol to broker symbol metadata.
        Returns: {"symbol": str, "type": str, "verified": str, "source": str} or None
        """
        if yf_symbol in self.overrides:
            return self.overrides[yf_symbol]

        # Default rules
        broker_symbol = yf_symbol
        symbol_type = "stock"
        source = "default-rule"
        
        # FX: EURUSD=X -> EURUSD
        if broker_symbol.endswith("=X"):
            broker_symbol = broker_symbol[:-2]
            symbol_type = "forex"
        
        # Crypto: BTC-USD -> BTCUSD
        elif "-USD" in broker_symbol:
            broker_symbol = broker_symbol.replace("-USD", "USD")
            symbol_type = "crypto"
        
        # Indices
        elif broker_symbol == "^GSPC":
            broker_symbol = "SPX"
            symbol_type = "index"
        elif broker_symbol == "^NDX":
            broker_symbol = "NDX"
            symbol_type = "index"
        elif broker_symbol == "^IXIC":
            broker_symbol = "COMP"
            symbol_type = "index"

        return {
            "symbol": broker_symbol,
            "type": symbol_type,
            "verified": datetime.now().strftime("%Y-%m-%d"),
            "source": source
        }

    def unmap_symbol(self, broker_symbol: str) -> str:
        """
        Heuristic to translate broker symbol back to yfinance for price lookups.
        """
        # 1. Exact inverse overrides
        for yf, meta in self.overrides.items():
            if meta.get("symbol") == broker_symbol:
                return yf

        # 2. Known Crypto heuristics (C2 BTCUSD -> yf BTC-USD)
        # Check common crypto bases. If it looks like CRYPTOUSD, it's likely crypto.
        crypto_bases = ["BTC", "ETH", "SOL", "ADA", "DOT", "XRP", "LTC", "DOGE", "AVAX"]
        for cb in crypto_bases:
            if broker_symbol == f"{cb}USD":
                return f"{cb}-USD"

        # 3. FX heuristics (6 letters, no digits)
        if len(broker_symbol) == 6 and broker_symbol.isupper() and not any(c.isdigit() for c in broker_symbol):
            major_currencies = ["EUR", "USD", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]
            if broker_symbol[:3] in major_currencies and broker_symbol[3:] in major_currencies:
                return f"{broker_symbol}=X"
            
        # 4. Indices
        if broker_symbol == "SPX": return "^GSPC"
        if broker_symbol == "NDX": return "^NDX"
        if broker_symbol == "COMP": return "^IXIC"
        
        return broker_symbol
