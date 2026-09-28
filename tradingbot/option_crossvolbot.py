"""
option_CrossVolBot — sell iron condors on the single names whose implied vol
sits furthest above a forecast of the vol they will deliver.

The AAPL mispricing bot found the gap between implied and forecast vol real,
but a short structure on ONE stock gives it back in that stock's jumps. This
spreads the same trade over the 50 captured names (utils/universes
.OPTION_CAPTURE_UNIVERSE), a few at a time, so one name's jump is a fraction
of the book instead of all of it.

Rules (utils/option_rules.CrossVolRules):
  * Fair vol: Yang-Zhang HAR forecast over the sessions to expiry, earnings
    reaction days excluded (utils/mispricing_scan.live_name_vol).
  * Shortlist: the `shortlist` names with the largest |z| in yesterday's scan
    (option_mispricing_scan); before the first scan, the universe in order.
  * Sell only names with no earnings before expiry, gap = ATM IV - fair
    >= min_gap, VIX < max_vix; richest gap first, up to max_positions names.
  * Each: 10-delta short put and call, wings width_pct of spot further out,
    worst-case loss <= risk_per_name_pct of the book.
  * Close at take_profit of the credit, stop_loss x the credit, exit_dte left,
    or before an ex-dividend date threatens early assignment of the short call.

Evidence: none yet. It cannot be backtested until per-name option history
exists (utils/option_replay.py over option_quotes). Paper, unproven,
live-only; it is the plain-gap baseline the mispricing scanner is compared to.

Schedule: 50 15 * * 1-5 (the chain must be live to open).
"""

import logging
from typing import ClassVar

from tradingbot.utils import mispricing_scan as ms
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import CrossVolRules, cross_vol_candidates, cross_vol_exit_reason
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

UNIVERSE = OPTION_CAPTURE_UNIVERSE[2:]  # the single names; SPY/QQQ are the index bot's


class OptionCrossVolBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[CrossVolRules] = CrossVolRules()

    def __init__(self, **kwargs):
        super().__init__("option_CrossVolBot", symbol="SPY", interval="1d", period="1y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        closed = self._manage(rules)
        held = options.option_underlyings(self.dbBot.portfolio)
        if len(held) >= rules.max_positions:
            logger.info("Holding %d condors (%s): no free slot", len(held), sorted(held))
            return -1 if closed else 0
        vix = self.getLatestPrice("^VIX")
        if vix >= rules.max_vix:
            logger.info("VIX %.1f >= %.0f: no new short vol", vix, rules.max_vix)
            return -1 if closed else 0

        names = ms.shortlist(UNIVERSE, ms.latest_scan_scores(), rules.shortlist)
        ohlc = ms.load_ohlc(names)
        scanned, views = [], {}
        for u in names:
            if u in held:
                continue
            nv, view = ms.live_name_vol(u, rules.target_dte, ohlc.get(u))
            if view is None or not view.live:
                logger.info("%s: chain not live", u)
                continue
            scanned.append(nv)
            views[u] = view
            logger.info(
                "%s %s: IV %s fair %s gap %s, earnings clear %s",
                u,
                view.expiry,
                f"{nv.iv:.1%}" if nv.iv else "n/a",
                f"{nv.fair:.1%}" if nv.fair else "n/a",
                f"{nv.gap:+.1%}" if nv.gap is not None else "n/a",
                nv.earnings_clear,
            )
        if not views:
            logger.info("No live chain among the shortlist; not opening off-hours")
            return -1 if closed else 0

        opened = 0
        for nv in cross_vol_candidates(scanned, held, rules):
            view = views[nv.underlying]
            if self.open_iron_condor(
                nv.underlying,
                short_delta=rules.short_delta,
                width=rules.width_pct * view.spot,
                dte=rules.target_dte,
                max_risk_usd=rules.risk_per_name_pct * self.portfolio_value(),
                view=view,
            ):
                logger.info("Sold a condor on %s: gap %+.1f pts", nv.underlying, nv.gap * 100)
                opened += 1
        return 1 if opened else (-1 if closed else 0)

    def _manage(self, rules: CrossVolRules) -> int:
        closed = 0
        for u in sorted(options.option_underlyings(self.dbBot.portfolio)):
            book = self.option_book(u)
            if book.empty:
                continue
            reason = cross_vol_exit_reason(book.credit, book.pnl, book.dte, rules)
            if not reason and self.dividend_threatened_calls(book):
                reason = "short call at risk of early assignment before the ex-dividend date"
            logger.info("%s condor: credit %.2f, P&L %.2f, %s DTE", u, book.credit, book.pnl, book.dte)
            if reason:
                logger.info("Closing %s: %s", u, reason)
                self.close_options(u)
                closed += 1
        return closed


if __name__ == "__main__":
    run_bot(OptionCrossVolBot)
