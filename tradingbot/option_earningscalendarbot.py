"""
option_EarningsCalendarBot — an AAPL call calendar into earnings: short the
front expiry that holds the inflated event IV, long the next one, closed on
the first run after the report.

The front expiry prices the whole earnings jump into a few days, so its IV
sits well above the back month's; after the report it collapses ("IV crush")
while the back month barely moves. The calendar is short that crush and
roughly flat the stock — it loses when the stock moves far from the strike.

Rules (utils/option_rules.CalendarRules):
  * Enter 3-8 trading days before the report.
  * Front: the first expiry after the report, at most 10 days after it. Back:
    the first expiry >= 21 days after the front. Strike: at the money.
  * Only when front IV / back IV >= 1.15, and the earnings move the term
    structure implies (option_math.implied_earnings_move) is at least AAPL's
    historical RMS earnings-day move: sell the crush only when it is rich.
  * Debit (= max loss: the back month is worth at least the front's intrinsic
    value when the front expires) <= 5% of the book.
  * Exit on the first run after the report, at +25%, at -50%, or 2 days
    before the front expiry.

Live-only: no data source can price historical earnings IV, so there is no
backtest; the option_quotes history this bot builds is the only evidence it
will ever have. Paper only.

Schedule: 35 15 * * 1-5.
"""

import logging
from datetime import timedelta
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import (
    CalendarRules,
    calendar_entry_window,
    calendar_exit_reason,
    calendar_signal_ok,
    daily_close,
)
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"


class OptionEarningsCalendarBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[CalendarRules] = CalendarRules()

    def __init__(self, **kwargs):
        super().__init__("option_EarningsCalendarBot", symbol=UNDERLYING, interval="1d", period="5y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        today = options.utc_today()
        book = self.option_book(UNDERLYING)
        if not book.empty:
            short = next((p for p in book.positions if p.qty < 0), book.positions[0])
            opened = options.opened_on(self.bot_name, short.key)
            reports = options.earnings_history(UNDERLYING, today=today)
            reason = calendar_exit_reason(
                today, opened, reports[-1] if reports else None, book.pnl_pct, book.dte, rules
            )
            logger.info(
                "Holding calendar %s: P&L %.2f (%.0f%%), front %s DTE, vega %.2f",
                [p.key for p in book.positions],
                book.pnl,
                100 * book.pnl_pct,
                book.dte,
                book.greeks.vega,
            )
            if reason:
                logger.info("Closing: %s", reason)
                self.close_options(UNDERLYING)
                return -1
            return 0

        earnings = options.next_earnings_date(UNDERLYING, today)
        if not calendar_entry_window(today, earnings, rules):
            return 0
        expiries = options.listed_expiries(UNDERLYING)
        front = next(
            (e for e in expiries if earnings < e <= earnings + timedelta(days=rules.front_max_days_after)), None
        )
        if front is None:
            logger.info("No expiry within %d days after earnings %s", rules.front_max_days_after, earnings)
            return 0
        back = next((e for e in expiries if e >= front + timedelta(days=rules.back_min_days_after_front)), None)
        if back is None:
            return 0

        front_view = options.load_chain(UNDERLYING, (front - today).days)
        back_view = options.load_chain(UNDERLYING, (back - today).days, spot=front_view.spot)
        front_iv, back_iv = options.atm_iv(front_view), options.atm_iv(back_view)
        implied = (
            om.implied_earnings_move(front_iv, front_view.T, back_iv, back_view.T) if front_iv and back_iv else None
        )
        close = daily_close(self.getYFData(interval="1d", period="5y", saveToDB=True))
        moves = om.earnings_reaction_returns(close, options.earnings_history(UNDERLYING, today=today))
        hist = om.earnings_jump(moves.tail(12).tolist()) if len(moves) else None
        logger.info(
            "Earnings %s: front %s IV %s, back %s IV %s, implied move %s vs historical %s",
            earnings,
            front,
            f"{front_iv:.1%}" if front_iv else "n/a",
            back,
            f"{back_iv:.1%}" if back_iv else "n/a",
            f"{implied:.1%}" if implied is not None else "n/a",
            f"{hist:.1%}" if hist else "n/a",
        )
        if not calendar_signal_ok(front_iv, back_iv, implied, hist, rules):
            return 0
        pick = options.select_calendar(front_view, back_view, rules.right)
        opened = self.open_structure(pick, rules.debit_pct * self.portfolio_value())
        return 1 if opened else 0


if __name__ == "__main__":
    run_bot(OptionEarningsCalendarBot)
