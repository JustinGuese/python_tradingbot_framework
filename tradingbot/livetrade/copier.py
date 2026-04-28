import logging
import os
import json
from pathlib import Path
from typing import Dict, List, Literal
from .broker import LiveBroker
from utils.bot_repository import BotRepository
from utils.data_service import DataService

logger = logging.getLogger(__name__)

class LiveTradeCopier:
    def __init__(
        self,
        broker: LiveBroker,
        bot_weights: Dict[str, float],
        copy_open_trades: bool = True,
        min_order_usd: float = 50.0,
        dry_run: bool = False
    ):
        self.broker = broker
        self.bot_weights = dict(bot_weights)
        self.copy_open_trades = copy_open_trades
        self.min_order_usd = min_order_usd
        self.dry_run = dry_run
        self.data_service = DataService()
        self.bot_repo = BotRepository()
        self.strict_mapping = os.getenv("LIVETRADE_STRICT_MAPPING", "false").lower() == "true"
        self.marker_file = Path("data/state/livetrade_seen.json")
        
        # Inject our data service into the broker if it supports it
        if hasattr(self.broker, 'data_service'):
            self.broker.data_service = self.data_service

    def _load_seen_markers(self) -> Dict[str, bool]:
        if not self.marker_file.exists():
            return {}
        try:
            with open(self.marker_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading marker file {self.marker_file}: {e}")
            return {}

    def _save_seen_marker(self, broker_name: str, bot_name: str):
        markers = self._load_seen_markers()
        key = f"{broker_name}:{bot_name}"
        markers[key] = True
        
        self.marker_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.marker_file, "w") as f:
                json.dump(markers, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving marker file {self.marker_file}: {e}")

    def sync(self) -> None:
        """Main entry point to synchronize live portfolio with paper bot portfolios."""
        logger.info(f"Starting sync (dry_run={self.dry_run}, strict_mapping={self.strict_mapping})")
        
        # Check for first-run skip if copy_open_trades=False
        if not self.copy_open_trades:
            broker_name = getattr(self.broker, "name", "generic")
            markers = self._load_seen_markers()
            all_marked = True
            
            for bot_name in self.bot_weights:
                key = f"{broker_name}:{bot_name}"
                if not markers.get(key):
                    all_marked = False
                    self._save_seen_marker(broker_name, bot_name)
                    logger.info(f"Marked first-run for {bot_name} on {broker_name}")
            
            if not all_marked:
                logger.info("First run with copy_open_trades=False: Skipping initial rebalance.")
                return

        # 1. Calculate target weights across all bots (yf_symbol -> weight)
        target_weights = self._calculate_target_weights()
        if not target_weights:
            logger.warning("No target weights calculated. Are bot portfolios empty or weights 0?")
            return

        # 2. Map target weights to broker symbols
        broker_target_weights = {} # broker_symbol -> {"weight": float, "type": str}
        unmapped_tickers = []

        for yf_symbol, weight in target_weights.items():
            meta = self.broker.map_symbol(yf_symbol)
            if meta and meta.get("symbol"):
                broker_target_weights[meta["symbol"]] = {
                    "weight": weight,
                    "type": meta.get("type", "stock")
                }
            else:
                logger.warning(f"Ticker {yf_symbol} is unmapped")
                unmapped_tickers.append(yf_symbol)

        if unmapped_tickers and self.strict_mapping:
            logger.error(f"STRICT MODE: Aborting sync due to unmapped tickers: {unmapped_tickers}")
            return

        # 3. Get total equity from broker
        total_equity = self.broker.get_total_equity()
        logger.info(f"Broker total equity: ${total_equity:.2f}")

        # 4. Get current positions from broker
        current_positions = self.broker.get_positions() # broker_symbol -> quantity
        
        # 5. Calculate orders
        orders = self._calculate_orders(broker_target_weights, current_positions, total_equity)
        
        # 6. Execute orders: Sells first, then Buys
        self._execute_orders(orders)
        logger.info("Sync complete")

    def _calculate_target_weights(self) -> Dict[str, float]:
        """Aggregate weighted portfolios from all source bots into yf_symbol -> weight."""
        aggregated_weights = {}
        total_user_weight = sum(self.bot_weights.values())
        if total_user_weight == 0:
            return {}

        for bot_name, user_weight in self.bot_weights.items():
            if user_weight <= 0:
                continue
            
            bot = self.bot_repo.create_or_get_bot(bot_name)
            portfolio = bot.portfolio or {}
            
            symbols = [s for s in portfolio.keys() if s != "USD"]
            if not symbols:
                continue
                
            prices = self.data_service.get_latest_prices_batch(symbols)
            
            bot_total_value = float(portfolio.get("USD", 0.0))
            symbol_values = {}
            for s in symbols:
                price = prices.get(s, 0.0)
                if price <= 0:
                    logger.warning(f"Could not get price for {s}, skipping in weight calc")
                    continue
                val = float(portfolio[s]) * price
                symbol_values[s] = val
                bot_total_value += val
            
            if bot_total_value <= 0:
                continue

            normalized_user_weight = user_weight / total_user_weight
            for s, val in symbol_values.items():
                weight_in_live = (val / bot_total_value) * normalized_user_weight
                aggregated_weights[s] = aggregated_weights.get(s, 0.0) + weight_in_live
                
        return aggregated_weights

    def _calculate_orders(
        self, 
        target_weights: Dict[str, Dict], 
        current_positions: Dict[str, float],
        total_equity: float
    ) -> List[Dict]:
        """Diff target vs current to produce order list."""
        orders = []
        all_symbols = set(target_weights.keys()) | set(current_positions.keys())
        
        for symbol in all_symbols:
            target_meta = target_weights.get(symbol, {"weight": 0.0, "type": "stock"})
            target_weight = target_meta["weight"]
            target_value = total_equity * target_weight
            
            current_qty = current_positions.get(symbol, 0.0)
            current_price = self.broker.get_latest_price(symbol)
            
            if current_price <= 0:
                logger.warning(f"Broker price for {symbol} is 0, trying yfinance fallback")
                try:
                    yf_symbol = self.broker.symbol_mapper.unmap_symbol(symbol)
                    current_price = self.data_service.get_latest_price(yf_symbol)
                except Exception as e:
                    logger.error(f"Could not get fallback price for {symbol}: {e}")
                    continue

            current_value = current_qty * current_price
            diff_value = target_value - current_value
            
            if abs(diff_value) < self.min_order_usd:
                continue
                
            qty_to_trade = diff_value / current_price
            side: Literal["BUY", "SELL"] = "BUY" if qty_to_trade > 0 else "SELL"
            
            orders.append({
                "symbol": symbol,
                "quantity": abs(qty_to_trade),
                "side": side,
                "value": abs(diff_value),
                "type": target_meta["type"]
            })
            
        return orders

    def _execute_orders(self, orders: List[Dict]) -> None:
        sells = [o for o in orders if o["side"] == "SELL"]
        buys = [o for o in orders if o["side"] == "BUY"]
        
        for order in sells + buys:
            if self.dry_run:
                logger.info(f"[DRY RUN] Would {order['side']} {order['quantity']:.4f} {order['symbol']} "
                            f"(type={order['type']}, ~${order['value']:.2f})")
            else:
                try:
                    self.broker.place_order(
                        order["symbol"], 
                        order["quantity"], 
                        order["side"], 
                        symbol_type=order["type"]
                    )
                    logger.info(f"Executed {order['side']} {order['quantity']:.4f} {order['symbol']}")
                except Exception as e:
                    logger.error(f"Failed to execute {order['side']} for {order['symbol']}: {e}")
