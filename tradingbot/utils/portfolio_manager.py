import logging
import math

import pandas as pd
from sqlalchemy.orm import Session

from . import option_math as om
from . import options
from .bot_repository import BotRepository
from .config import EXECUTION_CONFIG, PORTFOLIO_CONFIG, ExecutionConfig
from .data_service import DataService
from .db import Bot as BotModel
from .db import get_db_session
from .weights import require_normalized

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

    def _option_fill(self, contract: str, ref_price: float, *, is_buy: bool) -> float:
        """Buys fill at the ask, sells at the bid; ref_price +/- option slippage without a market."""
        if options.parse_occ(contract).expiry < options.utc_today():
            return ref_price  # expired: ref_price is already the settlement value
        quote = options.latest_quote(contract, max_age=None)
        side = (quote.ask if is_buy else quote.bid) if quote else 0.0
        if side > 0:
            return side
        slip = self.execution_config.option_slippage_pct
        return ref_price * (1.0 + slip) if is_buy else ref_price * (1.0 - slip)

    def buy(
        self,
        symbol: str,
        quantity_usd: float = -1,
        cached_data: pd.DataFrame | None = None,
        refresh: bool = True,
        session: Session | None = None,
        option: bool | str | None = None,
        target_dte: int = 30,
        target_delta: float | None = None,
    ) -> None:
        """
        Buy a quantity of the specified symbol.

        Args:
            symbol: Trading symbol to buy — for options, the UNDERLYING
            quantity_usd: Amount in USD to spend (-1 means use all available cash)
            cached_data: Optional cached DataFrame for price lookup
            refresh: Whether to refresh the bot from DB before executing
            session: Optional existing database session
            option: None/False buys the symbol itself. True / "call" / "put"
                buys a contract on it instead, chosen by options.select_contract
                (first expiry >= target_dte days out, strike nearest spot).
            target_dte: Minimum days to expiry for the selected contract.
            target_delta: Pick the strike nearest this delta (e.g. 0.70)
                instead of the one nearest spot.

        Cash reserved as margin for short option legs (see
        options.margin_requirement) is never spendable here. That is 0 for any
        bot without short options.
        """
        right = options.normalize_right(option)
        if right is None and options.is_option_symbol(symbol):
            # Contract strings are an internal key, not the bot-facing API: a bot
            # that has one in hand got it from its own portfolio, and buying it
            # back directly would skip contract selection and liquidity checks.
            raise ValueError(f"Buy options by underlying: buy({options.parse_occ(symbol).underlying!r}, option=...)")
        if right is not None:
            # Resolved BEFORE any row lock: selection is several yfinance calls.
            try:
                spot = self.data_service.get_latest_price(symbol)
            except Exception:
                spot = None  # select_contract falls back to the chain's own spot
            symbol = options.select_contract(symbol, right, target_dte, spot=spot, delta=target_delta)

        def _execute_buy(sess: Session):
            if sess:
                # Lock row if in transaction
                self.bot = self.bot_repository.get_bot_locked(sess, self.bot_name)
            elif refresh:
                self._refresh_bot()

            cfg = self.execution_config
            cash = self.bot.portfolio.get("USD", 0)
            spendable = cash - options.margin_requirement(self.bot.portfolio)

            # `quantity_usd` is the GROSS cash budget; commission comes out of it
            # rather than on top. That is what makes the spend-all-cash case
            # incapable of overdrawing at any commission rate: the debit is the
            # budget itself, so USD lands on exactly 0.0.
            qty_usd = spendable if quantity_usd == -1 else quantity_usd

            if qty_usd > spendable:
                # A fully-invested rebalance routinely lands here by a few bps,
                # because sells now raise slightly less than their notional. Only
                # a materially short buy is worth a warning.
                shortfall = qty_usd - spendable
                level = logging.INFO if shortfall <= max(1.0, 0.01 * qty_usd) else logging.WARNING
                logger.log(
                    level,
                    "Trimming buy of %s to available cash: have $%.2f, wanted $%.2f",
                    symbol,
                    spendable,
                    qty_usd,
                )
                qty_usd = spendable

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
            debit = qty_usd
            if options.is_option_symbol(symbol):
                execution_price = self._option_fill(symbol, price, is_buy=True)
                quantity = options.whole_contract_qty(available / execution_price)
                if quantity <= 0:
                    logger.warning(
                        "$%.2f buys less than one %s contract (%.2f per contract); skipping",
                        qty_usd,
                        symbol,
                        execution_price * options.CONTRACT_MULTIPLIER,
                    )
                    return
                # Whole contracts leave change: debit only what was spent. Still
                # <= qty_usd, since quantity was floored from the post-commission budget.
                commission_cost = quantity * execution_price * cfg.commission_pct
                debit = quantity * execution_price + commission_cost
            else:
                execution_price = cfg.buy_execution_price(price)
                quantity = available / execution_price

            if quantity <= 0:
                logger.warning(f"Calculated quantity for {symbol} is <= 0")
                return

            portfolio = self.bot.portfolio.copy()
            portfolio["USD"] = cash - debit  # stocks: the full gross budget; options: exact spend
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
                debit,
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
        option: bool | str | None = None,
    ) -> float:
        """
        Sell a quantity of the specified symbol.

        Args:
            symbol: Trading symbol to sell — for options, the UNDERLYING
            quantity_usd: Amount in USD to sell (-1 means sell all holdings)
            cached_data: Optional cached DataFrame for price lookup
            refresh: Whether to refresh the bot from DB before executing
            session: Optional existing database session
            option: None/False sells the symbol itself. True sells every option
                held on it; "call" / "put" only that side. Earliest expiry first.

        Returns:
            Net cash credited (0.0 if nothing was sold).
        """
        if option is not None and option is not False:
            right = None if option is True else options.normalize_right(option)
            return self._sell_options(symbol, right, quantity_usd, session)

        def _execute_sell(sess: Session) -> float:
            if sess:
                # Lock row if in transaction
                self.bot = self.bot_repository.get_bot_locked(sess, self.bot_name)
            elif refresh:
                self._refresh_bot()

            cfg = self.execution_config
            holding = self.bot.portfolio.get(symbol, 0)
            if holding <= 0:
                logger.warning(f"No holdings of {symbol} to sell")
                return 0.0

            price = self.data_service.get_latest_price(symbol, cached_data)
            if price <= 0:
                logger.warning("Non-positive price %s for %s; skipping sell", price, symbol)
                return 0.0
            is_option = options.is_option_symbol(symbol)

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
            if is_option and quantity < holding:
                quantity = options.whole_contract_qty(quantity)

            if quantity <= 0:
                return 0.0

            # Proceeds are derived AFTER the clamp, so the clamp stays a pure
            # share-count comparison that slippage cannot influence.
            if is_option:
                execution_price = self._option_fill(symbol, price, is_buy=False)
            else:
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
            return net_proceeds

        if session:
            return _execute_sell(session)
        with get_db_session() as sess:
            return _execute_sell(sess)

    def _sell_options(self, underlying: str, right: str | None, quantity_usd: float, session: Session | None) -> float:
        """Sell option holdings on `underlying`, earliest expiry first, up to quantity_usd."""
        self._refresh_bot(session)
        keys = options.held_option_keys(self.bot.portfolio, underlying, right)
        if not keys:
            logger.warning("No %s option holdings to sell", underlying)
            return 0.0
        proceeds = 0.0
        remaining = quantity_usd
        for key in keys:
            if quantity_usd == -1:
                proceeds += self.sell(key, session=session)
                continue
            if remaining <= 0:
                break
            value = self.bot.portfolio.get(key, 0) * self.data_service.get_latest_price(key)
            part = min(remaining, value)
            proceeds += self.sell(key, quantity_usd=part, session=session)
            remaining -= part
        return proceeds

    def roll_and_settle_options(self, roll_dte: int | None, target_dte: int, target_delta: float | None = None) -> None:
        """
        Keep option positions alive without the bot having to think about expiry.

        - Contracts already past expiry (the bot did not run in time) are
          cash-settled at intrinsic value off the underlying's close on the
          expiry date, long and short alike. Real equity options settle into
          shares; cash settlement at the same value is the deliberate
          simplification.
        - Long contracts with <= roll_dte days left are sold and the proceeds
          rebought in a fresh contract on the same underlying and side (at
          target_delta if given), so "I hold an AAPL call" stays true across
          expiries. roll_dte=None disables rolling.
        - An underlying with any SHORT leg is never rolled: rolling one wing of
          a spread alone would leave the short side uncovered. Strategies that
          sell options manage their own exits.

        A no-op for a bot holding no options.
        """
        self._refresh_bot()
        today = options.utc_today()
        for key in options.option_legs(self.bot.portfolio):
            contract = options.parse_occ(key)
            if contract.expiry < today:
                self._settle_expired(key, contract)
        if roll_dte is None:
            return

        self._refresh_bot()
        shorted = {options.parse_occ(k).underlying for k, q in options.option_legs(self.bot.portfolio).items() if q < 0}
        for key in options.held_option_keys(self.bot.portfolio):
            contract = options.parse_occ(key)
            if contract.underlying in shorted or (contract.expiry - today).days > roll_dte:
                continue
            proceeds = self.sell(key)
            if proceeds <= 0:
                continue
            try:
                self.buy(
                    contract.underlying,
                    proceeds,
                    option=contract.right,
                    target_dte=target_dte,
                    target_delta=target_delta,
                )
            except Exception as e:
                # The sell already committed; the proceeds simply stay cash.
                logger.warning("Rolled out of %s but could not roll into a new contract: %s", key, e)

    def _settle_expired(self, key: str, contract: options.OptionContract) -> None:
        settle_price = options.intrinsic_value(
            contract, options.underlying_close_on(contract.underlying, contract.expiry)
        )
        with get_db_session() as sess:
            self.bot = self.bot_repository.get_bot_locked(sess, self.bot_name)
            qty = self.bot.portfolio.get(key, 0)
            if abs(qty) < 1e-6:
                return
            # Signed: a long ITM leg is credited, a short ITM leg debited (the
            # cash for that was reserved as margin when it was opened).
            proceeds = qty * settle_price
            portfolio = self.bot.portfolio.copy()
            portfolio["USD"] = portfolio.get("USD", 0) + proceeds
            del portfolio[key]
            self.bot.portfolio = portfolio
            self.bot_repository.update_bot(self.bot, session=sess)
            self.bot_repository.log_trade(
                bot_name=self.bot_name,
                symbol=key,
                quantity=abs(qty),
                price=settle_price,
                is_buy=qty < 0,  # a short is closed by buying it back
                profit=proceeds if qty > 0 else None,
                session=sess,
            )
        logger.info("SETTLED expired %s: %.0f units at %.4f -> $%.2f", key, qty, settle_price, proceeds)

    # ------------------------------------------------------------------
    # Multi-leg options: spreads, condors, closing a whole book
    # ------------------------------------------------------------------

    def trade_option_legs(self, legs: list[tuple[str, float]], session: Session | None = None) -> float:
        """
        Change several option positions in ONE locked transaction.

        Args:
            legs: (OCC contract, signed share-equivalent change). +200 buys two
                contracts (to open or to close a short), -200 sells two (to
                close a long or to open a short). Whole contracts only.

        Returns:
            Net cash flow: positive for a credit, negative for a debit.

        This is the only path that can make a holding negative. Each leg fills
        at the ask when buying and the bid when selling (last price +/- option
        slippage without a market). If the trade opens or grows any position,
        it is refused as a whole — nothing is written — when the cash left
        would not cover options.margin_requirement of the resulting book. That
        also refuses anything with unbounded risk, such as a naked short call.
        Pure reductions always go through, so a position can always be closed.
        """
        legs = [(k, float(q)) for k, q in legs if abs(q) > 1e-9]
        if not legs:
            return 0.0
        for key, qty in legs:
            if not options.is_option_symbol(key):
                raise ValueError(f"trade_option_legs takes option contracts only, got {key!r}")
            if options.whole_contract_qty(abs(qty)) != round(abs(qty)):
                raise ValueError(f"{key}: {qty} is not a whole number of contracts")
        # Reference prices before the row lock: pricing may refetch a chain.
        refs = {key: self.data_service.get_latest_price(key) for key, _ in legs}

        def _execute(sess: Session) -> float:
            self.bot = self.bot_repository.get_bot_locked(sess, self.bot_name)
            cfg = self.execution_config
            portfolio = self.bot.portfolio.copy()
            cash = portfolio.get("USD", 0)
            fills = []
            grows = False
            for key, qty in legs:
                is_buy = qty > 0
                price = self._option_fill(key, refs[key], is_buy=is_buy)
                notional = abs(qty) * price
                flow = (-notional if is_buy else notional) - notional * cfg.commission_pct
                old = portfolio.get(key, 0.0)
                new = old + qty
                grows = grows or abs(new) > abs(old) + 1e-9
                if abs(new) < 1e-6:
                    portfolio.pop(key, None)
                else:
                    portfolio[key] = new
                cash += flow
                fills.append((key, qty, price, flow))
            portfolio["USD"] = cash

            required = options.margin_requirement(portfolio)
            if grows and cash + 1e-6 < required:
                raise ValueError(
                    f"Refused option trade {[(k, q) for k, q in legs]}: cash after ${cash:,.2f} "
                    f"< margin required ${required:,.2f}"
                )

            self.bot.portfolio = portfolio
            self.bot_repository.update_bot(self.bot, session=sess)
            for key, qty, price, flow in fills:
                self.bot_repository.log_trade(
                    bot_name=self.bot_name,
                    symbol=key,
                    quantity=abs(qty),
                    price=price,
                    is_buy=qty > 0,
                    profit=flow if qty < 0 else None,
                    session=sess,
                )
                logger.info(
                    "%s %.0f of %s at %.4f (cash %+.2f)", "BOUGHT" if qty > 0 else "SOLD", abs(qty), key, price, flow
                )
            return sum(f[3] for f in fills)

        if session:
            return _execute(session)
        with get_db_session() as sess:
            return _execute(sess)

    def open_structure(self, pick: options.StructurePick, max_risk_usd: float) -> int:
        """
        Open as many units of `pick` as `max_risk_usd` of worst-case loss allows.

        One unit is one contract per leg (signs from the pick). Max loss per unit
        is taken from the prices it would fill at (ask on the long legs, bid on
        the short), so for a credit spread it is width - credit. Refuses when
        the chain was not live: off-hours last prices on different legs are from
        different moments, and a "credit" built from them is fiction.

        Returns:
            Units opened (0 when refused or unaffordable).
        """
        if not pick.live:
            logger.warning("Not opening %s structure off-hours (no live chain)", pick.underlying)
            return 0
        unit_legs = []
        for key, unit in pick.legs:
            c = options.parse_occ(key)
            fill = self._option_fill(key, self.data_service.get_latest_price(key), is_buy=unit > 0)
            unit_legs.append(om.Leg(c.right, c.strike, unit, fill))
        per_unit = om.max_loss(unit_legs) * options.CONTRACT_MULTIPLIER
        if math.isinf(per_unit):
            raise ValueError(f"Refusing unbounded-risk structure {pick.legs}")
        if per_unit <= 0:
            logger.warning("Structure %s shows no risk at fill prices (bad quotes?); skipping", pick.legs)
            return 0

        self._refresh_bot()
        free = self.bot.portfolio.get("USD", 0) - options.margin_requirement(self.bot.portfolio)
        units = int(min(max_risk_usd, free) // per_unit)
        if units < 1:
            logger.warning(
                "One %s unit risks $%.2f; budget $%.2f, free cash $%.2f — skipping",
                pick.underlying,
                per_unit,
                max_risk_usd,
                free,
            )
            return 0
        self.trade_option_legs([(k, u * units * options.CONTRACT_MULTIPLIER) for k, u in pick.legs])
        logger.info("Opened %d x %s (max loss $%.2f each)", units, [k for k, _ in pick.legs], per_unit)
        return units

    def close_options(self, underlying: str, session: Session | None = None) -> float:
        """Flatten every option leg on `underlying`, long and short, in one transaction."""
        self._refresh_bot(session)
        legs = options.option_legs(self.bot.portfolio, underlying)
        if not legs:
            return 0.0
        return self.trade_option_legs([(k, -q) for k, q in legs.items()], session=session)

    def option_book(self, underlying: str) -> options.OptionBook:
        """Positions, marks, entry value, P&L and net greeks of the options on `underlying`."""
        self._refresh_bot()
        legs = options.option_legs(self.bot.portfolio, underlying)
        spot = self.data_service.get_latest_price(underlying)
        prices = {k: self.data_service.get_latest_price(k) for k in legs}
        entries = {k: options.entry_value(self.bot_name, k) for k in legs}
        return options.build_book(underlying, legs, prices, spot, entries)

    def total_value(self) -> float:
        """Cash plus every holding (short option legs negative) at the latest price."""
        self._refresh_bot()
        portfolio = self.bot.portfolio
        held = [s for s, q in portfolio.items() if s != "USD" and abs(q) > 1e-6]
        prices = self.data_service.get_latest_prices_batch(held) if held else {}
        return portfolio.get("USD", 0) + sum(portfolio[s] * prices.get(s, 0.0) for s in held)

    def rebalance_portfolio(self, target_portfolio: dict[str, float], only_over_50_usd: bool = False) -> None:
        """
        Rebalance portfolio to match target weights in a single transaction with row locking.

        Args:
            target_portfolio: Dictionary mapping symbols to target weights (e.g., {"VWCE": 0.8, "GLD": 0.1, "USD": 0.1})
                           Weights must sum to 1.0 (100%)
            only_over_50_usd: If True, filter out assets with target value <= $50
        """
        # Step 1: Validate weights sum to 1.0. Caller-supplied input, so reject
        # rather than silently rescale — a target that does not sum to 1 means the
        # caller's own maths is wrong and rescaling would hide it.
        require_normalized(target_portfolio)
        contracts = [s for s in target_portfolio if options.is_option_symbol(s)]
        if contracts:
            # Weights are per symbol; an option target would need contract
            # selection and rolling inside the rebalance. Not supported — held
            # contracts absent from the target are sold like any other exit.
            raise ValueError(f"rebalancePortfolio cannot target option contracts {contracts}; use buy/sell(option=...)")
        self._refresh_bot()
        shorts = [k for k, q in options.option_legs(self.bot.portfolio).items() if q < 0]
        if shorts:
            # A weight-based rebalance values and exits long holdings; a short leg
            # would be ignored in the total and "exited" via buy() of a raw
            # contract. Spread books are managed with close_options instead.
            raise ValueError(f"rebalancePortfolio cannot run on a book with short option legs {shorts}")

        # Step 2: Resolve prices BEFORE taking the row lock.
        #
        # get_latest_prices_batch opens its own session and, on a cache miss, calls
        # yfinance over the network. Doing that inside the locked block held
        # SELECT ... FOR UPDATE on this bot's row across an external HTTP request,
        # so every other writer for the same bot — the copier, the worth
        # calculator, a concurrent run — blocked for as long as a third party took
        # to respond, with the statement timeout as the only ceiling.
        with get_db_session() as preview_session:
            snapshot = preview_session.query(BotModel).filter_by(name=self.bot_name).one()
            preview_symbols = sorted(set(target_portfolio) | set(snapshot.portfolio))
        prices = self.data_service.get_latest_prices_batch([s for s in preview_symbols if s != "USD"])

        with get_db_session() as session:
            # Lock bot row for the entire duration of rebalance
            self.bot = self.bot_repository.get_bot_locked(session, self.bot_name)

            # Step 3: Calculate current portfolio value
            current_usd = self.bot.portfolio.get("USD", 0)

            # Get all symbols involved. Sorted, not set-ordered: buys are sized
            # against the pre-trade snapshot, so whichever symbol comes last
            # absorbs any cash shortfall. Hash-order would make that symbol vary
            # between processes — invisible before costs existed, visible now.
            all_involved_symbols = sorted(set(list(target_portfolio.keys()) + list(self.bot.portfolio.keys())))
            all_involved_symbols = [s for s in all_involved_symbols if s != "USD"]

            # The unlocked snapshot above can be stale: another writer may have
            # added a holding between the two reads. Fetch only what that missed,
            # so the common case still costs zero network time under the lock.
            missing = [s for s in all_involved_symbols if s not in prices]
            if missing:
                logger.debug(f"Prices for {missing} appeared after the pre-lock snapshot; fetching under lock")
                prices.update(self.data_service.get_latest_prices_batch(missing))

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
