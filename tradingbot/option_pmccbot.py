"""
option_PMCCBot — poor man's covered call on AAPL: a deep ITM LEAP call in
place of the shares, with near-dated calls sold against it every month.

It is option_LeapCallBot plus call writing, so the pair answers one question:
does selling the calls' volatility premium beat keeping the upside?

Rules (utils/option_rules.PMCCRules):
  * The LEAP half is the LEAP bot's (LeapRules via pmcc_leap_rules): hold
    while AAPL > SMA200, enter only when ATM IV / 60-day HV <= 1.2, a
    0.70-delta call on the first expiry >= 540 days out, sized to ~1x the
    book in delta-dollars, trimmed back to 1x above 1.5x.
  * Against it, one 0.30-delta call per LEAP on the first expiry >= 35 days
    out, never struck below the LEAP strike + the extrinsic value paid for it
    (assigned below that, the diagonal locks in a loss).
  * The short call is bought back at 50% of its credit, at 7 DTE, or once its
    delta reaches 0.60 (roll up and out); the next one is sold on the
    following run.
  * With 180 days left on the LEAP, the whole diagonal is closed and rebuilt
    on the next run (auto-roll never touches an underlying with short legs).

Synthetic backtest vs option_LeapCallBot (full window): alpha +13.5% vs
+18.9%/yr, but beta 0.22 vs 0.24, max DD -19% vs -24% and alpha t 3.53 vs 3.17
(out of sample 2.37 vs 2.10), so the calls pay in risk-adjusted terms. The
LEAP delta was re-tuned walk-forward from 0.80 to 0.70 (out-of-sample t 1.46 ->
2.37). Still long AAPL, chosen with hindsight. See
docs/backtests/option-bots-round2-2026-09.md. Paper only.

Schedule: 30 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import (
    PMCCRules,
    leap_contracts,
    leap_signal,
    leap_trim_contracts,
    pmcc_leap_rules,
    pmcc_min_short_strike,
    pmcc_short_exit_reason,
    sma,
)
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"


class OptionPMCCBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    # Walk-forward re-tune 2026-09-26: a 0.70-delta LEAP beat 0.80 out of sample.
    RULES: ClassVar[PMCCRules] = PMCCRules(long_delta=0.70)

    def __init__(self, **kwargs):
        super().__init__("option_PMCCBot", symbol=UNDERLYING, interval="1d", period="2y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        leap = pmcc_leap_rules(rules)
        data = self.getYFDataWithTA(interval="1d", period="2y", saveToDB=True)
        close = data["close"]
        last = float(close.iloc[-1])
        signal = leap_signal(last, sma(close, 200), leap)
        book = self.option_book(UNDERLYING)
        longs = [p for p in book.positions if p.qty > 0]
        shorts = [p for p in book.positions if p.qty < 0]

        if not longs:
            if shorts:  # a short left without its LEAP can only be a repair case
                self.close_options(UNDERLYING)
                return -1
            return self._enter(close, signal)

        if signal == -1:
            logger.info("Close %.2f below SMA200; closing the diagonal", last)
            self.close_options(UNDERLYING)
            return -1
        long_dte = min((p.contract.expiry - book.today).days for p in longs)
        if long_dte <= rules.long_roll_dte:
            logger.info("LEAP has %d days left; closing to rebuild on a fresh expiry", long_dte)
            self.close_options(UNDERLYING)
            return -1

        if shorts:
            return self._manage_shorts(shorts)
        if self._trim(longs, book):
            return -1
        return self._sell_calls(longs, book)

    def _enter(self, close, signal: int) -> int:
        leap = pmcc_leap_rules(self.RULES)
        if signal != 1:
            return 0
        view = options.load_chain(UNDERLYING, leap.target_dte)
        if not view.live:
            logger.info("Chain not live; not buying off-hours")
            return 0
        ratio = om.iv_hv_ratio(options.atm_iv(view), om.historical_volatility(close, leap.hv_window))
        if ratio is None or ratio > leap.max_iv_hv:
            logger.info("IV/HV %s above %.2f: time is expensive, waiting", ratio, leap.max_iv_hv)
            return 0
        contract = options.select_contract(UNDERLYING, "C", leap.target_dte, delta=leap.delta, view=view)
        quote = options.latest_quote(contract, max_age=None)
        ask = quote.ask if quote and quote.ask > 0 else self.getLatestPrice(contract) * 1.02
        n = leap_contracts(self.portfolio_value(), view.spot, leap.delta, leap.leverage)
        if n < 1:
            return 0
        self.buy(
            UNDERLYING,
            quantity_usd=n * ask * options.CONTRACT_MULTIPLIER * 1.005,
            option="call",
            delta=leap.delta,
            dte=leap.target_dte,
        )
        return 1

    def _manage_shorts(self, shorts: list[options.OptionPosition]) -> int:
        legs = []
        for p in shorts:
            entry = options.entry_value(self.bot_name, p.key)
            credit, pnl = max(0.0, -entry), p.qty * p.price - entry
            delta = p.greeks.delta / p.qty if p.qty else None
            reason = pmcc_short_exit_reason(
                credit, pnl, (p.contract.expiry - options.utc_today()).days, delta, self.RULES
            )
            logger.info("Short %s: credit %.2f, P&L %.2f, delta %s", p.key, credit, pnl, delta)
            if reason:
                logger.info("Buying back %s: %s", p.key, reason)
                legs.append((p.key, -p.qty))
        if legs:
            self.trade_option_legs(legs)
            return -1
        return 0

    def _trim(self, longs: list[options.OptionPosition], book: options.OptionBook) -> bool:
        leap = pmcc_leap_rules(self.RULES)
        contracts = sum(p.contracts for p in longs)
        delta_dollars = sum(p.greeks.delta for p in longs) * book.spot
        if contracts <= 0 or delta_dollars <= 0:
            return False
        n = min(
            leap_trim_contracts(delta_dollars, self.portfolio_value(), delta_dollars / contracts, leap), int(contracts)
        )
        if n <= 0:
            return False
        logger.info("Exposure %.0f > %.1fx book; trimming %d LEAPs", delta_dollars, leap.max_leverage, n)
        self.trade_option_legs([(longs[0].key, -n * options.CONTRACT_MULTIPLIER)])
        return True

    def _sell_calls(self, longs: list[options.OptionPosition], book: options.OptionBook) -> int:
        rules = self.RULES
        view = options.load_chain(UNDERLYING, rules.short_dte)
        if not view.live:
            logger.info("Chain not live; not selling calls off-hours")
            return 0
        long = max(longs, key=lambda p: p.qty)
        cost = options.entry_value(self.bot_name, long.key) / long.qty
        floor = pmcc_min_short_strike(long.contract.strike, cost, book.spot)
        try:
            pick = options.select_short_leg(view, "C", rules.short_delta, min_strike=floor)
        except ValueError as e:
            logger.info("No call to sell above %.2f: %s", floor, e)
            return 0
        n = int(sum(p.contracts for p in longs))
        logger.info("Selling %d calls %s against the LEAP (strike floor %.2f)", n, pick.legs, floor)
        self.trade_option_legs([(k, u * n * options.CONTRACT_MULTIPLIER) for k, u in pick.legs])
        return 1


if __name__ == "__main__":
    run_bot(OptionPMCCBot)
