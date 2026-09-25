"""
option_CreditSpreadBot — sell AAPL vertical credit spreads when implied vol is rich.

From the options course: "short put spreads" (bullish) and "short call spreads"
(bearish), and the volatility rule "only short or write options if the implied
volatility is high ... compared to historical vol, as long as there's no news
event". Option writers are the casino: small, frequent, defined-risk bets that
win more often than they lose.

Rules (utils/option_rules.CreditRules):
  * Direction from the trend: close > SMA50 > SMA200 sells a bull put spread,
    the mirror image a bear call spread, anything mixed trades nothing.
  * Only when ATM implied vol / 20-day historical vol >= 1.10 (options are
    pricing more movement than the stock delivers), ^VIX < 35, and no AAPL
    earnings before expiry (the news event the course warns about).
  * Short leg at 0.30 delta, long wing $10 further out, first expiry >= 35 DTE.
  * Size: worst-case loss (width - credit) <= 25% of the book. That is far
    above the course's 1%-of-net-worth rule; this is a $100k paper sleeve.
  * Close at 50% of the credit, at a loss of 2x the credit, or at 21 DTE.

The worst-case loss is held back as cash margin by the framework, so the bot
cannot overcommit. Paper only: the live copier drops option holdings.

Schedule: 5 15 * * 1-5 — mid-morning New York, when the chain has a live
two-sided market. Off-hours the framework refuses to open spreads.
"""

import logging
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import (
    CreditRules,
    credit_exit_reason,
    earnings_clear,
    premium_selling_ok,
    sma,
    trend_side,
)
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"


class OptionCreditSpreadBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None  # exits are the strategy's own
    RULES: ClassVar[CreditRules] = CreditRules()

    def __init__(self, **kwargs):
        super().__init__("option_CreditSpreadBot", symbol=UNDERLYING, interval="1d", period="2y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        book = self.option_book(UNDERLYING)
        if not book.empty:
            reason = credit_exit_reason(book.credit, book.pnl, book.dte, rules)
            logger.info(
                "Holding %d legs: credit %.2f, P&L %.2f, %s DTE, delta %.1f, theta %.2f/day",
                len(book.positions),
                book.credit,
                book.pnl,
                book.dte,
                book.greeks.delta,
                book.greeks.theta,
            )
            if reason:
                logger.info("Closing: %s", reason)
                self.close_options(UNDERLYING)
                return -1
            return 0

        data = self.getYFDataWithTA(interval="1d", period="2y", saveToDB=True)
        close = data["close"]
        side = trend_side(float(close.iloc[-1]), sma(close, 50), sma(close, 200))
        if side is None:
            logger.info("No clear trend; no spread")
            return 0

        view = options.load_chain(UNDERLYING, rules.target_dte)
        if not view.live:
            logger.info("Chain not live; not opening a spread off-hours")
            return 0
        ratio = om.iv_hv_ratio(options.atm_iv(view), om.historical_volatility(close, rules.hv_window))
        vix = self.getLatestPrice("^VIX")
        logger.info("Trend %s, IV/HV %s, VIX %.1f", side, f"{ratio:.2f}" if ratio else "n/a", vix)
        if not premium_selling_ok(ratio, vix, None, rules):
            return 0
        if not earnings_clear(options.next_earnings_date(UNDERLYING, view.today), view.expiry, view.today):
            logger.info("Earnings before %s; not selling premium through it", view.expiry)
            return 0

        opened = self.open_credit_spread(
            UNDERLYING,
            side,
            short_delta=rules.short_delta,
            width=rules.width,
            dte=rules.target_dte,
            max_risk_usd=rules.max_risk_pct * self.portfolio_value(),
            view=view,
        )
        return 1 if opened else 0


if __name__ == "__main__":
    run_bot(OptionCreditSpreadBot)
