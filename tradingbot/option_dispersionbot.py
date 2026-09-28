"""
option_DispersionBot — short index vol against long single-name vol when the
options price the S&P's members as unusually correlated.

Index variance is the members' variances plus their covariances, so index
implied vol embeds an implied correlation. When that correlation is rich,
selling the index's vol and buying its members' is the classic dispersion
trade: it earns when the members move more independently than priced.

Rules (utils/option_rules.DispersionRules):
  * Signal: implied correlation of SPY against the captured names
    (implied_correlation table, written after each daily capture), as a
    percentile of its own history. Trades nothing until min_history (60)
    observations exist, and says so on every run.
  * Enter at >= entry_pct (80th): short SPY iron butterfly (wings at
    wing_sigmas x the expected move), long ATM straddles on the n_names
    largest names by market cap, lots per name N_i = |V_fly| x w_i / V_i so
    each carries its weight's share of the index leg's vega.
  * Size: fly worst-case loss plus straddle premiums <= max_risk_pct of book.
  * Exit at <= exit_pct, exit_dte left, or take_profit of the capital at stake.

Short dispersion only: the reverse trade needs naked short single-name vol.
It loses when correlation jumps toward 1, which is a crash: the tail every
short-index-vol book carries, here partly offset by the long straddles.

Evidence: none. Paper, unproven, live-only; the implied-correlation history
it needs starts with the capture (2026-09-28).

Schedule: 55 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import options
from tradingbot.utils import vol_surface as vs
from tradingbot.utils.botclass import Bot
from tradingbot.utils.fundamentals import get_fundamentals_batch
from tradingbot.utils.option_rules import DispersionRules, dispersion_signal, vega_weighted_lots
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

INDEX = "SPY"
UNIVERSE = OPTION_CAPTURE_UNIVERSE[2:]


class OptionDispersionBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[DispersionRules] = DispersionRules()

    def __init__(self, **kwargs):
        super().__init__("option_DispersionBot", symbol=INDEX, interval="1d", period="1y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        history = vs.implied_correlation_history(INDEX)
        past, current = (history.iloc[:-1].tolist(), float(history.iloc[-1])) if len(history) else ([], None)
        signal, why = dispersion_signal(past, current, rules)
        logger.info("Dispersion: %s", why)

        held = options.option_underlyings(self.dbBot.portfolio)
        if held:
            return self._manage(held, signal, rules)
        if signal != "enter":
            return 0
        return self._enter(rules)

    def _manage(self, held: set[str], signal: str | None, rules: DispersionRules) -> int:
        books = {u: self.option_book(u) for u in held}
        pnl = sum(b.pnl for b in books.values())
        stake = sum(abs(b.entry_value) for b in books.values())
        dte = min((b.dte for b in books.values() if b.dte is not None), default=None)
        reason = None
        if signal == "exit":
            reason = "implied correlation back to normal"
        elif dte is not None and dte <= rules.exit_dte:
            reason = f"{dte} DTE"
        elif stake > 0 and pnl >= rules.take_profit * stake:
            reason = f"take profit {pnl:.0f} on {stake:.0f} at stake"
        logger.info("Holding dispersion on %d underlyings: P&L %.0f, stake %.0f, %s DTE", len(held), pnl, stake, dte)
        if not reason:
            return 0
        logger.info("Closing the dispersion book: %s", reason)
        for u in sorted(held):
            self.close_options(u)
        return -1

    def _enter(self, rules: DispersionRules) -> int:
        caps = get_fundamentals_batch(list(UNIVERSE), options.utc_today(), max_age_days=10)
        weights = {u: (c or {}).get("market_cap") or 0.0 for u, c in caps.items()}
        names = sorted((u for u in weights if weights[u] > 0), key=lambda u: -weights[u])[: rules.n_names]
        if not names:
            logger.warning("No market caps in stock_fundamentals: cannot weight the members")
            return 0

        index_view = options.load_chain(INDEX, rules.target_dte)
        if not index_view.live:
            logger.info("Chain not live; not opening off-hours")
            return 0
        iv = options.atm_iv(index_view) or 0.0
        width = rules.wing_sigmas * iv * index_view.T**0.5 * index_view.spot
        fly = options.select_iron_butterfly(index_view, width)
        fly_m = options.pick_metrics(index_view, fly)

        straddles, metrics = {}, {}
        for u in names:
            try:
                view = options.load_chain(u, rules.target_dte)
                pick = options.select_straddle(view)
                straddles[u], metrics[u] = pick, options.pick_metrics(view, pick)
            except Exception as exc:
                logger.warning("%s: no straddle (%s)", u, exc)

        budget = rules.max_risk_pct * self.portfolio_value()
        best = None
        for n_fly in range(1, 50):
            lots = vega_weighted_lots(n_fly * fly_m.vega, {u: m.vega for u, m in metrics.items()}, weights)
            cost = n_fly * fly_m.max_loss + sum(lots[u] * metrics[u].price for u in lots)
            if cost > budget:
                break
            best = (n_fly, lots, cost)
        if best is None or not best[1]:
            logger.info("Budget %.0f too small for one fly plus vega-matched straddles", budget)
            return 0
        n_fly, lots, cost = best
        net_vega = n_fly * fly_m.vega + sum(lots[u] * metrics[u].vega for u in lots)
        logger.info(
            "Opening dispersion: %d SPY flies (vega %.0f), straddles %s, cost %.0f, net vega %+.0f",
            n_fly,
            n_fly * fly_m.vega,
            lots,
            cost,
            net_vega,
        )
        self.open_structure(fly, n_fly * fly_m.max_loss * 1.05)
        for u, n in lots.items():
            self.open_structure(straddles[u], n * metrics[u].price * 1.05)
        return 1


if __name__ == "__main__":
    run_bot(OptionDispersionBot)
