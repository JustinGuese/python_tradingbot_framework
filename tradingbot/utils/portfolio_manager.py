import logging

import pandas as pd
from sqlalchemy.orm import Session

from .bot_repository import BotRepository
from .config import EXECUTION_CONFIG, PORTFOLIO_CONFIG, ExecutionConfig
from .data_service import DataService
from .db import Bot as BotModel
from .db import get_db_session

logger = logging.getLogger(__name__)

# Below this USD amount a "full exit" is just float residue, not a position.
DUST_USD = 0.01


def should_trade(
    diff_usd: float,
    reference_usd: float,
    cfg: ExecutionConfig | None = None,
    *,
    is_full_exit: bool = False,
) -> bool:
    """
    Decide whether a rebalancing adjustment of `diff_usd` clears the no-trade band.

    Full exits ALWAYS trade. Without that bypass a position smaller than the band
    could never be liquidated, so sub-band positions would accumulate forever with
    nothing to alert on. Worse, rebalance_portfolio's min_asset_value_usd filter
    works *by* dropping a symbol from the target so it becomes a full exit —
    banding full exits would silently turn that filter into a no-op for exactly
    the small positions it exists to clear.

    A new position entering from zero does NOT bypass: easy to exit, hard to
    enter, so position count trends down rather than up.
    """
    cfg = cfg or EXECUTION_CONFIG
    magnitude = abs(diff_usd)
    if is_full_exit:
        return magnitude > DUST_USD
    return magnitude >= cfg.no_trade_threshold(reference_usd)


class PortfolioManager:
    """Manages portfolio operations including buying, selling, and rebalancing."""

    def __init__(
        self,
        bot: BotModel,
        bot_name: str,
        data_service: DataService,
        bot_repository: type[BotRepository],
        execution_config: ExecutionConfig | None = None,
    ):
        """
        Initialize portfolio manager.

        Args:
            bot: BotModel instance representing the bot's portfolio
            bot_name: Name of the bot (passed separately to avoid DetachedInstanceError)
            data_service: DataService instance for fetching prices
            bot_repository: BotRepository class (used via its staticmethods, never instantiated)
            execution_config: Costs and no-trade band. Injected rather than read
                from the module singleton so tests can vary it: this module did
                `from .config import EXECUTION_CONFIG`, binding the reference at
                import, so monkeypatching config.EXECUTION_CONFIG would be
                silently invisible here. Bots override via env vars instead.
        """
        self.bot = bot
        self.bot_name = bot_name
        self.data_service = data_service
        self.bot_repository = bot_repository
        self.execution_config = execution_config or EXECUTION_CONFIG

    def _refresh_bot(self, session: Session | None = None) -> None:
        """Ensure the Bot instance is attached to an active session."""
        self.bot = self.bot_repository.create_or_get_bot(self.bot_name, session=session)

    def buy(
        self,
        symbol: str,
        quantity_usd: float = -1,
        cached_data: pd.DataFrame | None = None,
        refresh: bool = True,
        session: Session | None = None,
    ) -> None:
        """
        Buy a quantity of the specified symbol.

        Args:
            symbol: Trading symbol to buy
            quantity_usd: Amount in USD to spend (-1 means use all available cash)
            cached_data: Optional cached DataFrame for price lookup
            refresh: Whether to refresh the bot from DB before executing
            session: Optional existing database session
        """

        def _execute_buy(sess: Session):
            if sess:
                # Lock row if in transaction
                self.bot = self.bot_repository.get_bot_locked(sess, self.bot_name)
            elif refresh:
                self._refresh_bot()

            cfg = self.execution_config
            cash = self.bot.portfolio.get("USD", 0)

            # `quantity_usd` is the GROSS cash budget; commission comes out of it
            # rather than on top. That is what makes the spend-all-cash case
            # incapable of overdrawing at any commission rate: the debit is the
            # budget itself, so USD lands on exactly 0.0.
            qty_usd = cash if quantity_usd == -1 else quantity_usd

            if qty_usd > cash:
                # A fully-invested rebalance routinely lands here by a few bps,
                # because sells now raise slightly less than their notional. Only
                # a materially short buy is worth a warning.
                shortfall = qty_usd - cash
                level = logging.INFO if shortfall <= max(1.0, 0.01 * qty_usd) else logging.WARNING
                logger.log(
                    level,
                    "Trimming buy of %s to available cash: have $%.2f, wanted $%.2f",
                    symbol,
                    cash,
                    qty_usd,
                )
                qty_usd = cash

            if qty_usd <= 0:
                logger.warning(f"Insufficient cash to buy {symbol}")
                return

            price = self.data_service.get_latest_price(symbol, cached_data)
            if price <= 0:
                # Guard the division: a ZeroDivisionError raised inside
                # rebalance_portfolio aborts the locked transaction after the
                # sells have run but before the buys, leaving the book in cash.
                logger.warning("Non-positive price %s for %s; skipping buy", price, symbol)
                return

            commission_cost = qty_usd * cfg.commission_pct
            available = qty_usd - commission_cost
            execution_price = cfg.buy_execution_price(price)
            quantity = available / execution_price

            if quantity <= 0:
                logger.warning(f"Calculated quantity for {symbol} is <= 0")
                return

            portfolio = self.bot.portfolio.copy()
            portfolio["USD"] = cash - qty_usd  # full gross budget debited
            portfolio[symbol] = portfolio.get(symbol, 0) + quantity

            self.bot.portfolio = portfolio
            self.bot_repository.update_bot(self.bot, session=sess)
            self.bot_repository.log_trade(
                bot_name=self.bot_name,
                symbol=symbol,
                quantity=quantity,
                # Execution price, not the reference price: quantity * price must
                # explain the cash movement. The reference price stays recoverable
                # from historic_data; the execution price is recorded nowhere else.
                price=execution_price,
                is_buy=True,
                session=sess,
            )
            logger.info(
                "BOUGHT %.6f of %s at %.4f (ref %.4f, commission %.4f) for gross %.2f",
                quantity,
                symbol,
                execution_price,
                price,
                commission_cost,
                qty_usd,
            )

        if session:
            _execute_buy(session)
        else:
            with get_db_session() as sess:
                _execute_buy(sess)

    def sell(
        self,
        symbol: str,
        quantity_usd: float = -1,
        cached_data: pd.DataFrame | None = None,
        refresh: bool = True,
        session: Session | None = None,
    ) -> None:
        """
        Sell a quantity of the specified symbol.

        Args:
            symbol: Trading symbol to sell
            quantity_usd: Amount in USD to sell (-1 means sell all holdings)
            cached_data: Optional cached DataFrame for price lookup
            refresh: Whether to refresh the bot from DB before executing
            session: Optional existing database session
        """

        def _execute_sell(sess: Session):
            if sess:
                # Lock row if in transaction
                self.bot = self.bot_repository.get_bot_locked(sess, self.bot_name)
            elif refresh:
                self._refresh_bot()

            cfg = self.execution_config
            holding = self.bot.portfolio.get(symbol, 0)
            if holding <= 0:
                logger.warning(f"No holdings of {symbol} to sell")
                return

            price = self.data_service.get_latest_price(symbol, cached_data)
            if price <= 0:
                logger.warning("Non-positive price %s for %s; skipping sell", price, symbol)
                return

            # On a sell, `quantity_usd` is the POSITION NOTIONAL to shed valued at
            # the reference price — not the cash to raise. rebalance_portfolio
            # computes it as (current_value - target_value) from a mid-price
            # snapshot, so sizing the share count off that same price is what lands
            # the post-trade position exactly on target and converges in one step.
            # Sizing off the execution price instead would shed ~5bps too many
            # shares, undershoot, and hand the next rebalance a fresh diff to
            # correct — generating precisely the churn this model exists to remove.
            quantity = holding if quantity_usd == -1 else quantity_usd / price

            if quantity > holding:
                logger.warning(f"Insufficient holdings of {symbol} to sell requested amount. Selling all.")
                quantity = holding

            if quantity <= 0:
                return

            # Proceeds are derived AFTER the clamp, so the clamp stays a pure
            # share-count comparison that slippage cannot influence.
            execution_price = cfg.sell_execution_price(price)
            gross_proceeds = quantity * execution_price
            commission_cost = gross_proceeds * cfg.commission_pct
            net_proceeds = gross_proceeds - commission_cost

            portfolio = self.bot.portfolio.copy()
            portfolio["USD"] = portfolio.get("USD", 0) + net_proceeds
            portfolio[symbol] = holding - quantity

            # Remove zero holdings
            if portfolio[symbol] <= 0.000001:
                del portfolio[symbol]

            self.bot.portfolio = portfolio
            self.bot_repository.update_bot(self.bot, session=sess)
            self.bot_repository.log_trade(
                bot_name=self.bot_name,
                symbol=symbol,
                quantity=quantity,
                price=execution_price,
                is_buy=False,
                profit=net_proceeds,  # net cash credited, NOT realized P&L
                session=sess,
            )
            logger.info(
                "SOLD %.6f of %s at %.4f (ref %.4f, commission %.4f) for net proceeds %.2f",
                quantity,
                symbol,
                execution_price,
                price,
                commission_cost,
                net_proceeds,
            )

        if session:
            _execute_sell(session)
        else:
            with get_db_session() as sess:
                _execute_sell(sess)

    def rebalance_portfolio(self, target_portfolio: dict[str, float], only_over_50_usd: bool = False) -> None:
        """
        Rebalance portfolio to match target weights in a single transaction with row locking.

        Args:
            target_portfolio: Dictionary mapping symbols to target weights (e.g., {"VWCE": 0.8, "GLD": 0.1, "USD": 0.1})
                           Weights must sum to 1.0 (100%)
            only_over_50_usd: If True, filter out assets with target value <= $50
        """
        # Step 1: Validate weights sum to 1.0
        total_weight = sum(target_portfolio.values())
        if abs(total_weight - 1.0) > 0.01:
            raise ValueError(f"Target portfolio weights must sum to 1.0, got {total_weight}")

        with get_db_session() as session:
            # Lock bot row for the entire duration of rebalance
            self.bot = self.bot_repository.get_bot_locked(session, self.bot_name)

            # Step 2: Calculate current portfolio value
            current_usd = self.bot.portfolio.get("USD", 0)

            # Get all symbols involved. Sorted, not set-ordered: buys are sized
            # against the pre-trade snapshot, so whichever symbol comes last
            # absorbs any cash shortfall. Hash-order would make that symbol vary
            # between processes — invisible before costs existed, visible now.
            all_involved_symbols = sorted(set(list(target_portfolio.keys()) + list(self.bot.portfolio.keys())))
            all_involved_symbols = [s for s in all_involved_symbols if s != "USD"]

            # Batch fetch prices
            prices = self.data_service.get_latest_prices_batch(all_involved_symbols)

            # Calculate total portfolio value
            total_portfolio_value = current_usd
            current_values = {"USD": current_usd}

            for symbol in all_involved_symbols:
                qty = self.bot.portfolio.get(symbol, 0)
                if qty > 0:
                    price = prices.get(symbol)
                    if price:
                        val = qty * price
                        current_values[symbol] = val
                        total_portfolio_value += val
                    else:
                        logger.warning(f"Could not get price for {symbol}, assuming zero value")
                        current_values[symbol] = 0

            if total_portfolio_value <= 0:
                logger.warning("Portfolio worth is zero, cannot rebalance")
                return

            # Step 3: Apply $50 threshold if requested
            actual_targets = target_portfolio.copy()
            if only_over_50_usd:
                filtered_weights = {}
                excluded_weight = 0.0

                for sym, weight in actual_targets.items():
                    if sym == "USD" or (weight * total_portfolio_value) > PORTFOLIO_CONFIG.min_asset_value_usd:
                        filtered_weights[sym] = weight
                    else:
                        excluded_weight += weight

                if excluded_weight > 0:
                    # Redistribute to remaining non-USD assets
                    non_usd_remaining = [s for s in filtered_weights if s != "USD"]
                    if non_usd_remaining:
                        redist_per_asset = excluded_weight / len(non_usd_remaining)
                        for s in non_usd_remaining:
                            filtered_weights[s] += redist_per_asset
                        actual_targets = filtered_weights
                    else:
                        # Put all in USD if no assets left
                        actual_targets = {"USD": 1.0}

            # Step 4: Calculate target values and differences
            target_values = {s: total_portfolio_value * w for s, w in actual_targets.items()}

            trades_to_sell = {}  # symbol -> USD amount
            trades_to_buy = {}

            cfg = self.execution_config
            skipped = 0
            skipped_usd = 0.0

            for symbol in all_involved_symbols:
                target_val = target_values.get(symbol, 0)
                current_val = current_values.get(symbol, 0)
                diff = target_val - current_val

                # No target but a live position == full liquidation, never banded.
                # This is also how the min_asset_value_usd filter above expresses
                # "close this position": it drops the symbol from actual_targets.
                is_full_exit = target_val <= 0 < current_val
                reference_val = max(target_val, current_val)

                if not should_trade(diff, reference_val, cfg, is_full_exit=is_full_exit):
                    if abs(diff) > 0:
                        skipped += 1
                        skipped_usd += abs(diff)
                    continue

                if diff < 0:
                    trades_to_sell[symbol] = abs(diff)
                else:
                    trades_to_buy[symbol] = diff

            logger.info(
                "Rebalancing %s: Total Value $%.2f, %d sells, %d buys, "
                "%d skipped inside no-trade band ($%.2f notional; band = max($%.2f, %.1f%% of position))",
                self.bot_name,
                total_portfolio_value,
                len(trades_to_sell),
                len(trades_to_buy),
                skipped,
                skipped_usd,
                cfg.min_trade_usd,
                cfg.rebalance_band_pct * 100,
            )

            # Step 5: Execute trades (Sells first)
            for symbol, usd_amt in trades_to_sell.items():
                self.sell(symbol, quantity_usd=usd_amt, refresh=False, session=session)

            # Re-read cash after sells
            for symbol, usd_amt in trades_to_buy.items():
                self.buy(symbol, quantity_usd=usd_amt, refresh=False, session=session)

            logger.info("Rebalance complete")
