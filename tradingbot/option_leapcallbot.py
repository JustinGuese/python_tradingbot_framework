"""
option_LeapCallBot — AAPL stock replacement with a deep-ish LEAP call.

From the options course: "LEAP — 1-2 years in future, 70 delta", for a
long-term bullish view, with a lower cost basis than the shares; and "buy
options when implied volatility is low".

Rules (utils/option_rules.LeapRules, walk-forward re-tuned 2026-09-25):
  * Hold while AAPL closes above its 200-day SMA; exit on a close below it.
  * Enter only when ATM IV / 60-day HV <= 1.2: don't buy expensive time.
  * 0.60-delta call, first expiry >= 540 days out, sized so its delta-dollars
    are ~1.0x the book. That is ~20% of the book in premium, the rest cash:
    the "lower cost basis" part — the same stock exposure for a fifth of the
    capital, and the most that can be lost is the premium.
  * Trimmed back to 1.0x whenever gains push exposure past 1.5x. Without the
    trim, exposure drifted to ~1.6x on average and 2.9x at worst.
  * The framework rolls the LEAP into a fresh 540-day, 0.60-delta call when
    180 days are left, before theta decay accelerates.

Re-tune vs the original (0.70 delta, 3% exit buffer, no trim), picked on
2012-2019 and judged on 2019-2026: out-of-sample alpha t 2.10 vs 1.78, max
drawdown -24% vs -35%, and all 72 grid variants stayed positive out of sample.

Still a long-AAPL strategy, and AAPL was picked knowing it compounded 23%/yr,
so its "alpha" vs QQQ is largely AAPL's own outperformance plus the trend
filter; the synthetic IV proxy also makes the LEAP's beta look lower than it
is. See docs/backtests/option-bots-2026-09.md. Paper only.

Schedule: 0 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import LeapRules, leap_contracts, leap_signal, leap_trim_contracts, sma
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"
_RULES = LeapRules(delta=0.60, exit_buffer=0.0, max_leverage=1.5)


class OptionLeapCallBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_TARGET_DTE: ClassVar[int] = _RULES.target_dte
    OPTION_ROLL_DTE: ClassVar[int | None] = _RULES.roll_dte
    OPTION_TARGET_DELTA: ClassVar[float | None] = _RULES.delta
    RULES: ClassVar[LeapRules] = _RULES

    def __init__(self, **kwargs):
        super().__init__("option_LeapCallBot", symbol=UNDERLYING, interval="1d", period="2y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        data = self.getYFDataWithTA(interval="1d", period="2y", saveToDB=True)
        close = data["close"]
        last = float(close.iloc[-1])
        signal = leap_signal(last, sma(close, 200), rules)
        book = self.option_book(UNDERLYING)

        if not book.empty:
            logger.info(
                "Holding %.0f contracts: P&L %.2f (%.0f%%), %s DTE, delta-dollars %.0f",
                sum(p.contracts for p in book.positions),
                book.pnl,
                100 * book.pnl_pct,
                book.dte,
                book.greeks.delta * book.spot,
            )
            if signal == -1:
                logger.info("Close %.2f fell below SMA200 band; exiting", last)
                self.close_options(UNDERLYING)
                return -1
            return self._trim(book)
        if signal != 1:
            return 0

        view = options.load_chain(UNDERLYING, rules.target_dte)
        if not view.live:
            logger.info("Chain not live; not buying off-hours")
            return 0
        ratio = om.iv_hv_ratio(options.atm_iv(view), om.historical_volatility(close, rules.hv_window))
        if ratio is None or ratio > rules.max_iv_hv:
            logger.info("IV/HV %s above %.2f: time is expensive, waiting", ratio, rules.max_iv_hv)
            return 0

        contract = options.select_contract(UNDERLYING, "C", rules.target_dte, delta=rules.delta, view=view)
        quote = options.latest_quote(contract, max_age=None)
        ask = quote.ask if quote and quote.ask > 0 else self.getLatestPrice(contract) * 1.02
        n = leap_contracts(self.portfolio_value(), view.spot, rules.delta, rules.leverage)
        if n < 1:
            return 0
        # A hair over n contracts at the ask: buy() floors to whole contracts
        # and keeps the change as cash.
        self.buy(UNDERLYING, quantity_usd=n * ask * options.CONTRACT_MULTIPLIER * 1.005, option="call")
        return 1

    def _trim(self, book: options.OptionBook) -> int:
        """Sell contracts back to rules.leverage x book once exposure exceeds max_leverage."""
        contracts = sum(p.contracts for p in book.positions)
        delta_dollars = book.greeks.delta * book.spot
        if contracts <= 0 or delta_dollars <= 0:
            return 0
        n = leap_trim_contracts(delta_dollars, self.portfolio_value(), delta_dollars / contracts, self.RULES)
        if n <= 0:
            return 0
        n = min(n, int(contracts))
        # Value-based partial sell at the mark: sell() floors this back to n whole contracts.
        mark = max(p.price for p in book.positions)
        logger.info("Exposure %.0f > %.1fx book; trimming %d contracts", delta_dollars, self.RULES.max_leverage, n)
        self.sell(UNDERLYING, quantity_usd=n * mark * options.CONTRACT_MULTIPLIER, option="call")
        return -1


if __name__ == "__main__":
    run_bot(OptionLeapCallBot)
