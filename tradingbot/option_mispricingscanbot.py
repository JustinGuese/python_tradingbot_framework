"""
option_MispricingScanBot — price every name in vol terms against a forward
forecast, and trade only what is unusually rich or cheap for THAT name.

The raw gap between implied and forecast vol is not a signal on its own:
implied sits above realized most of the time (the variance risk premium), so
"IV > forecast" says sell nearly every day. This bot z-scores the gap against
the name's own history and acts only at the tails, in both directions, with
risk measured across the whole book.

Rules (utils/option_rules.MispricingScanRules, scan in utils/mispricing_scan):
  * Universe: SPY, QQQ and the 50 captured names. Shortlist: SPY, QQQ, then the
    largest |z| in yesterday's scan; live chains are loaded for those only.
  * Forecast: Yang-Zhang HAR over the sessions to expiry, earnings days out.
  * z: today's ATM IV minus a HAR forecast, against the name's vol_surface
    history of the same (SPY/QQQ: a ^VIX/^VXN proxy back to 2001). Single
    names need 60 days of their own history first and are logged "n/60".
  * Rich (z >= +2, no earnings before expiry, VIX < 35): an iron condor, short
    strikes at 16 delta, wings 8% of spot. Cheap (z <= -2): a long ATM
    straddle, delta-hedged inside a Whalley-Wilmott band.
  * Surface filters: strike pairs outside the American put-call band are
    dropped as bad quotes; the SVI fit's outliers are logged for the record.
  * Sizing by vega: each position aims at 1/max_positions of a book-wide vega
    budget (vega_budget_pct x equity per vol point), and is skipped unless the
    whole book stays inside that budget and a crash (every underlying -20%,
    vol +30 points) costs at most stress_cap_pct of equity.
  * Unwind every short-vol position when ^VIX / ^VIX3M inverts past
    unwind_term_ratio. Exits otherwise at take-profit, stop, DTE, max hold,
    or |z| back under exit_z; short calls are closed before an ex-dividend
    date threatens early assignment.

Evidence: the SPY/QQQ z-score leg is the only part a backtest can reach
(scripts/onetime_index_vol_backtest.py --gates, grid B). The single-name and
surface parts are live-only until utils/option_replay.py has history.
Paper, unproven.

Schedule: 0 16 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import mispricing_scan as ms
from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import MispricingScanRules, business_days, scan_exit_reason, scan_side
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE
from tradingbot.utils.vol_indices import VIX, VIX3M, term_ratio

logger = logging.getLogger(__name__)

UNIVERSE = OPTION_CAPTURE_UNIVERSE
ALWAYS = ("SPY", "QQQ")


class OptionMispricingScanBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[MispricingScanRules] = MispricingScanRules()

    def __init__(self, **kwargs):
        super().__init__("option_MispricingScanBot", symbol="SPY", interval="1d", period="1y", **kwargs)

    # -- main loop ---------------------------------------------------------

    def makeOneIteration(self) -> int:
        rules = self.RULES
        vix = self.getLatestPrice(VIX)
        term = term_ratio(vix, self.getLatestPrice(VIX3M))
        closed = self._manage(rules, term)

        held = options.option_underlyings(self.dbBot.portfolio)
        if len(held) >= rules.max_positions:
            logger.info("Holding %d positions: no free slot", len(held))
            return -1 if closed else 0
        names = ms.shortlist(UNIVERSE, ms.latest_scan_scores(), rules.shortlist, always=ALWAYS)
        ohlc = ms.load_ohlc(names)
        ranked = []
        for u in names:
            if u in held:
                continue
            nv, view = ms.live_name_vol(u, rules.target_dte, ohlc.get(u), min_obs=rules.min_obs)
            side, why = scan_side(nv, rules, vix)
            logger.info(
                "%s: IV %s, fair %s, z %s -> %s (%s)",
                u,
                f"{nv.iv:.1%}" if nv.iv else "n/a",
                f"{nv.fair:.1%}" if nv.fair else "n/a",
                f"{nv.z:+.2f}" if nv.z is not None else "n/a",
                side or "no trade",
                why,
            )
            if side and view is not None and view.live:
                if side == "rich" and term is not None and term > rules.unwind_term_ratio:
                    logger.info("%s: rich, but VIX/VIX3M %.2f is inverted: no new short vol", u, term)
                    continue
                ranked.append((abs(nv.z), u, side, view))
        if not ranked:
            return -1 if closed else 0

        opened = 0
        for _, u, side, view in sorted(ranked, key=lambda x: -x[0]):
            if len(held) + opened >= rules.max_positions:
                break
            opened += self._open(u, side, view, rules)
        return 1 if opened else (-1 if closed else 0)

    # -- opening -----------------------------------------------------------

    def _pick(self, u: str, side: str, view: options.ChainView, rules: MispricingScanRules):
        if side == "cheap":
            return options.select_straddle(view)
        fit = options.svi_surface_fit(view)
        if fit.outliers:
            logger.info(
                "%s SVI outliers: %s",
                u,
                ", ".join(f"{o.right}{o.strike:g} {o.resid:+.1%}" for o in fit.outliers[:4]),
            )
        return options.select_iron_condor(
            u, rules.short_delta, rules.width_pct * view.spot, rules.target_dte, view=view
        )

    def _book_risk(self) -> tuple[float, float]:
        """(sum of |vega| per vol point, crash P&L) of every open position."""
        vega = crash = 0.0
        for u in options.option_underlyings(self.dbBot.portfolio):
            book = self.option_book(u)
            vega += abs(book.greeks.vega)
            crash += om.worst_stress(options.stress_legs(book), book.spot, options.risk_free_rate())
        return vega, crash

    def _open(self, u: str, side: str, view: options.ChainView, rules: MispricingScanRules) -> int:
        try:
            pick = self._pick(u, side, view, rules)
            m = options.pick_metrics(view, pick)
        except Exception as exc:
            logger.warning("%s: no %s structure (%s)", u, side, exc)
            return 0
        equity = self.portfolio_value()
        budget = rules.vega_budget_pct * equity
        book_vega, book_crash = self._book_risk()
        unit_vega = abs(m.vega)
        if unit_vega <= 0:
            return 0
        units = max(int((budget / rules.max_positions) // unit_vega), 1)
        while units > 0:
            legs = options.pick_stress_legs(view, pick, units)
            crash = book_crash + om.worst_stress(legs, view.spot, options.risk_free_rate())
            if book_vega + units * unit_vega <= budget and -crash <= rules.stress_cap_pct * equity:
                break
            units -= 1
        if units == 0:
            logger.info(
                "%s %s skipped: vega %.0f/pt per unit on a book at %.0f of %.0f, or the crash cap binds",
                u,
                side,
                unit_vega,
                book_vega,
                budget,
            )
            return 0
        risk = units * (m.max_loss if side == "rich" else m.price) * 1.05
        opened = self.open_structure(pick, risk)
        logger.info("%s: opened %d x %s (%s), vega %+.0f/pt", u, opened, side, pick.legs, opened * m.vega)
        if opened and side == "cheap":
            self.delta_hedge(u, 0.0, ww=(rules.hedge_cost_frac, rules.hedge_ww_risk_aversion))
        return 1 if opened else 0

    # -- managing ----------------------------------------------------------

    def _manage(self, rules: MispricingScanRules, term: float | None) -> int:
        closed = 0
        held = sorted(options.option_underlyings(self.dbBot.portfolio))
        if not held:
            return 0
        ohlc = ms.load_ohlc(held)
        for u in held:
            book = self.option_book(u)
            if book.empty:
                continue
            side = "rich" if book.entry_value < 0 else "cheap"
            opened = options.opened_on(self.bot_name, book.positions[0].key)
            held_days = business_days(opened, book.today) if opened else 0
            pnl, stake = book.pnl, abs(book.entry_value)
            if side == "cheap" and opened:
                pnl = options.structure_flows(self.bot_name, u, opened) + book.value + book.shares * book.spot
            reason = None
            if side == "rich" and term is not None and term > rules.unwind_term_ratio:
                reason = f"VIX/VIX3M {term:.2f} inverted: unwinding short vol"
            elif side == "rich" and self.dividend_threatened_calls(book):
                reason = "short call at risk of early assignment before the ex-dividend date"
            else:
                nv, _ = ms.live_name_vol(u, book.dte or rules.target_dte, ohlc.get(u), min_obs=rules.min_obs)
                reason = scan_exit_reason(side, nv.z, pnl, stake, book.dte, held_days, rules)
            logger.info("%s %s: P&L %.0f on %.0f, %s DTE, held %d days", u, side, pnl, stake, book.dte, held_days)
            if reason:
                logger.info("Closing %s: %s", u, reason)
                self.close_options(u, include_stock=True)
                closed += 1
            elif side == "cheap":
                self.delta_hedge(u, 0.0, ww=(rules.hedge_cost_frac, rules.hedge_ww_risk_aversion))
        return closed


if __name__ == "__main__":
    run_bot(OptionMispricingScanBot)
