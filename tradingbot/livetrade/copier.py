import logging
import os
import time
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
        min_order_usd: float = 50.0,
        dry_run: bool = False,
        portfolio_fraction: float = 1.0
    ):
        self.broker = broker
        self.bot_weights = dict(bot_weights)
        self.min_order_usd = min_order_usd
        self.dry_run = dry_run
        self.portfolio_fraction = portfolio_fraction
        self.data_service = DataService()
        self.bot_repo = BotRepository()
        self.strict_mapping = os.getenv("LIVETRADE_STRICT_MAPPING", "false").lower() == "true"
        # Pause between SELL batch and BUY batch so liquidation proceeds settle
        # and brokers update buying power before we size the buys.
        self.settle_delay_seconds = float(os.getenv("LIVETRADE_SETTLE_DELAY_SECONDS", "10"))

        # Inject our data service into the broker if it supports it
        if hasattr(self.broker, 'data_service'):
            self.broker.data_service = self.data_service

    def sync(self) -> None:
        """Main entry point to synchronize live portfolio with paper bot portfolios."""
        logger.info(f"Starting sync (dry_run={self.dry_run}, strict_mapping={self.strict_mapping})")

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

        if total_equity <= 0:
            logger.error(f"Aborting sync due to non-positive equity: ${total_equity:.2f}")
            return

        if self.portfolio_fraction != 1.0:
            scaled = total_equity * self.portfolio_fraction
            logger.info(f"Applying portfolio_fraction={self.portfolio_fraction:.2f}: ${total_equity:.2f} -> ${scaled:.2f}")
            total_equity = scaled

        # 4. Get current positions from broker
        cancelled = self.broker.cancel_open_orders()
        if cancelled:
            logger.info(f"Cancelled {cancelled} stale open orders before sync")

        current_positions = self.broker.get_positions() # broker_symbol -> quantity

        # 5. Calculate orders (full target-state sync: liquidates anything not in target)
        orders = self._calculate_orders(broker_target_weights, current_positions, total_equity)

        # 6. Execute orders: Sells first, then Buys (with a settle pause in between)
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
            current_qty = current_positions.get(symbol, 0.0)

            # Full liquidation: not in target, just sell the whole position.
            # No price lookup needed — broker knows the quantity.
            if target_weight == 0.0 and current_qty > 0:
                orders.append({
                    "symbol": symbol,
                    "quantity": current_qty,
                    "side": "SELL",
                    "value": 0.0,  # unknown without a price; not used for execution
                    "type": target_meta["type"]
                })
                continue

            target_value = total_equity * target_weight
            current_price = self.broker.get_latest_price(symbol)
            if current_price <= 0:
                logger.error(f"No price available for {symbol}; skipping")
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

        def _submit(o: Dict) -> None:
            if self.dry_run:
                logger.info(f"[DRY RUN] Would {o['side']} {o['quantity']:.4f} {o['symbol']} "
                            f"(type={o['type']}, ~${o['value']:.2f})")
                return
            try:
                self.broker.place_order(o["symbol"], o["quantity"], o["side"], symbol_type=o["type"])
                logger.info(f"Submitted {o['side']} {o['quantity']:.4f} {o['symbol']}")
            except Exception as e:
                logger.error(f"Failed to execute {o['side']} for {o['symbol']}: {e}")

        for o in sells:
            _submit(o)

        # Let sells settle (cash become available) before issuing buys.
        # Skipped on dry run and when there's nothing to wait for.
        if sells and buys and not self.dry_run and self.settle_delay_seconds > 0:
            logger.info(f"Waiting {self.settle_delay_seconds:.0f}s for sells to settle before buying...")
            time.sleep(self.settle_delay_seconds)

        # Buys are sized against total equity, but brokers enforce margin/cash
        # at submission time. If sells haven't fully settled (common on C2
        # paper accounts) the broker rejects with a PreMarginCheck error.
        # Clamp total buy notional to currently-available cash, with a small
        # safety buffer for slippage between price snapshot and fill.
        if buys and not self.dry_run:
            try:
                available_cash = self.broker.get_cash()
            except Exception as e:
                logger.warning(f"Could not fetch cash for buy clamp: {e}; submitting buys unscaled")
                available_cash = None

            if available_cash is not None and available_cash > 0:
                total_buy = sum(o["value"] for o in buys)
                budget = available_cash * 0.98  # 2% buffer for price drift
                if total_buy > budget:
                    scale = budget / total_buy
                    logger.warning(
                        f"Buy notional ${total_buy:.2f} exceeds available cash "
                        f"${available_cash:.2f}; scaling buys by {scale:.4f}"
                    )
                    for o in buys:
                        o["quantity"] *= scale
                        o["value"] *= scale
                    buys = [o for o in buys if o["value"] >= self.min_order_usd]
            elif available_cash is not None:
                logger.error(f"Available cash is ${available_cash:.2f}; skipping all buys")
                buys = []

        for o in buys:
            _submit(o)
