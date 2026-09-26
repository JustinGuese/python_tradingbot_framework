"""
option_MispricingBot — trade AAPL options when their price disagrees with a
fair-value model, after trying to explain the disagreement away.

"What it should cost": pricing with the market's own implied vol reproduces
the market price exactly, so the fair price needs an independent vol:
  * diffusion vol: a HAR-RV forecast (option_math.har_rv_forecast) of AAPL's
    realized vol over the trading days to expiry, fitted with earnings-reaction
    days removed;
  * plus AAPL's RMS earnings-day move when a report falls inside the expiry.
Black-Scholes-Merton at that fair vol (with AAPL's dividend yield as q) is
the fair price; ATM IV minus fair vol is the mispricing. The ATM put's IV is
solved on a binomial tree, since US puts are American.

Explaining the gap (option_rules.explain_gap), mechanical causes first:
  1. earnings inside the expiry: the chain is pricing a jump -> no trade (the
     implied vs historical earnings move is logged);
  2. market-wide vs AAPL-specific: AAPL IV / ^VXN against AAPL's usual
     relative realized vol;
  3. for an AAPL-specific rich gap, the last 3 days of AAPL headlines in
     stock_news (refreshed first): one free LLM call asks whether they describe
     a pending event (ruling, merger, tariff...), with a keyword fallback. A
     pending event means the option is priced right, not rich -> no sale;
  4. ^VIX >= 40: no new short vol;
  5. per-contract outliers against the fitted smile, logged only: on AAPL they
     sit inside the bid/ask spread.
What is left is traded (walk-forward re-tuned 2026-09-26):
  * rich (IV >= fair + 10 pts, well above the usual ~4-pt variance premium):
    an iron butterfly at the money, wings 1.5 expected moves out, max loss 20%
    of the book; out at 25% of the credit, a loss of 1x the credit, the gap
    closing, or 5 DTE;
  * cheap (IV <= fair - 2 pts): a long ATM straddle for 10% of the book,
    delta-hedged with AAPL shares every run (gamma scalping: it earns when the
    stock moves more than the IV paid for); out at +30%, the gap closing, 15
    trading days, or 5 DTE.

Backtest (synthetic, level signal only): the gap does predict the variance
premium (IV beat the next 21 days' realized vol by +8 to +14 pts on 83-87% of
days with a 10+ pt gap), but a short ATM structure on one stock pays for it in
jumps. The defaults (6-pt gap, 1-sigma wings) lost -14%/yr in 2012-2019; the
re-tune made +4%/yr at t 0.82 out of sample, beta 0.1. Unproven. The news step,
the smile and AAPL's own IV are live-only. See
docs/backtests/option-bots-round2-2026-09.md. Paper only.

Schedule: 20 15 * * 1-5.
"""

import logging
from typing import ClassVar

import numpy as np

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import (
    GapExplanation,
    MispricingRules,
    business_days,
    butterfly_width,
    classify_news,
    daily_close,
    earnings_clear,
    explain_gap,
    fair_volatility_for,
    mispricing_exit_reason,
    mispricing_side,
)
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"
BENCH = "QQQ"


class OptionMispricingBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    # Walk-forward re-tune 2026-09-26 (chosen on 2012-2019, judged on 2019-2026):
    # only a 10-point gap is worth selling, with wider wings. See the doc.
    RULES: ClassVar[MispricingRules] = MispricingRules(rich_gap=0.10, cheap_gap=0.02, wing_sigmas=1.5, exit_dte=5)

    def __init__(self, **kwargs):
        super().__init__("option_MispricingBot", symbol=UNDERLYING, interval="1d", period="5y", **kwargs)

    # -- inputs ----------------------------------------------------------

    def _closes(self):
        close = daily_close(self.getYFData(interval="1d", period="5y", saveToDB=True))
        bench = daily_close(self.getYFData(symbol=BENCH, interval="1d", period="2y", saveToDB=True))
        return close, bench

    def _relative_iv(self, iv: float, close, bench) -> float | None:
        """AAPL IV / ^VXN over AAPL's usual realized-vol ratio to QQQ (1.0 = where the market puts it)."""
        try:
            vxn = self.getLatestPrice("^VXN") / 100.0
        except Exception as e:
            logger.warning("^VXN unavailable (%s); skipping the market-wide check", e)
            return None
        ratio = (om.rolling_historical_volatility(close, 30) / om.rolling_historical_volatility(bench, 30)).dropna()
        usual = float(ratio.tail(252).median()) if len(ratio) else float("nan")
        if not vxn > 0 or not usual > 0 or np.isnan(usual):
            return None
        return iv / vxn / usual

    def _explain(self, view: options.ChainView, close, bench) -> GapExplanation | None:
        rules = self.RULES
        iv = options.atm_iv(view, american=True)
        if iv is None:
            logger.warning("No solvable ATM IV on %s", view.expiry)
            return None
        reports = options.earnings_history(UNDERLYING, today=view.today)
        next_report = options.next_earnings_date(UNDERLYING, view.today)
        inside = not earnings_clear(next_report, view.expiry, view.today)
        fv = fair_volatility_for(close, reports, view.today, view.expiry, inside)
        implied_move = None
        if inside:
            back = options.load_chain(UNDERLYING, (view.expiry - view.today).days + 21, spot=view.spot)
            back_iv = options.atm_iv(back)
            if back_iv:
                implied_move = om.implied_earnings_move(iv, view.T, back_iv, back.T)
        gap_side = mispricing_side(iv, fv.fair, rules)
        relative = self._relative_iv(iv, close, bench) if gap_side else None
        news_event = None
        if gap_side and not inside:
            headlines = options.recent_news(UNDERLYING, days=3)
            news_event = classify_news(headlines, UNDERLYING, view.expiry, self._ai)
        vix = self.getLatestPrice("^VIX") if gap_side == "rich" else None
        return explain_gap(
            iv,
            fv.fair,
            rules,
            earnings_inside=inside,
            implied_move=implied_move,
            hist_move=fv.jump if fv.jump == fv.jump else None,
            relative_iv=relative,
            news_event=news_event,
            vix=vix,
            smile_notes=options.smile_outliers(view),
        )

    def _ai(self, system: str, user: str) -> str:
        # The free model only: one call on the rare day a gap needs explaining.
        return self.run_ai_simple(system, user)

    # -- the iteration ---------------------------------------------------

    def makeOneIteration(self) -> int:
        rules = self.RULES
        close, bench = self._closes()
        book = self.option_book(UNDERLYING)
        if not book.empty:
            return self._manage(book, close, bench)

        view = options.load_chain(UNDERLYING, rules.target_dte)
        explanation = self._explain(view, close, bench)
        if explanation is None:
            return 0
        logger.info("%s %s: %s", UNDERLYING, view.expiry, explanation.summary())
        if not explanation.tradeable:
            return 0
        if not view.live:
            logger.info("Chain not live; not opening off-hours")
            return 0

        book_value = self.portfolio_value()
        if explanation.side == "rich":
            width = butterfly_width(view.spot, explanation.iv, view.T, rules)
            pick = options.select_iron_butterfly(view, width)
            opened = self.open_structure(pick, rules.max_risk_pct * book_value)
        else:
            pick = options.select_straddle(view)
            opened = self.open_structure(pick, rules.premium_pct * book_value)
            if opened:
                self.delta_hedge(UNDERLYING, rules.hedge_band_pct * book_value)
        return 1 if opened else 0

    def _manage(self, book: options.OptionBook, close, bench) -> int:
        rules = self.RULES
        side = "rich" if any(p.qty < 0 for p in book.positions) else "cheap"
        view = options.load_chain(UNDERLYING, book.dte or 0)
        iv = options.atm_iv(view)
        reports = options.earnings_history(UNDERLYING, today=view.today)
        fv = fair_volatility_for(close, reports, view.today, view.expiry, earnings_inside=False)
        gap = iv - fv.fair if iv is not None else None
        opened = options.opened_on(self.bot_name, book.positions[0].key)
        held = business_days(opened, view.today) if opened else 0
        # A hedged straddle's P&L includes what the share hedge made along the
        # way (realized into cash), so measure it from every cash flow since
        # the open rather than from the option legs alone.
        pnl = book.pnl
        if side == "cheap" and opened:
            pnl = options.structure_flows(self.bot_name, UNDERLYING, opened) + book.value + book.shares * book.spot
        logger.info(
            "Holding %s %s: P&L %.2f on entry %.2f, %s DTE, held %d days, gap %s, net delta %.1f, vega %.2f",
            side,
            [p.key for p in book.positions],
            pnl,
            book.entry_value,
            book.dte,
            held,
            f"{gap:+.3f}" if gap is not None else "n/a",
            book.net_delta,
            book.greeks.vega,
        )
        reason = mispricing_exit_reason(side, gap, pnl, book.entry_value, book.dte, held, rules)
        if reason:
            logger.info("Closing: %s", reason)
            self.close_options(UNDERLYING, include_stock=True)
            return -1
        if side == "cheap":
            self.delta_hedge(UNDERLYING, rules.hedge_band_pct * self.portfolio_value())
        return 0


if __name__ == "__main__":
    run_bot(OptionMispricingBot)
