"""
XAUZenCarryBot — priority-stacked meta-bot (alpha leg preempts, carry leg owns idle days).

The rule, re-evaluated from scratch on every run:

    if XAUZenbotTreeBot is holding ^XAU:   100% -> ^XAU
    else:                                  mirror GoldenButterflyMomBot's weights

This is deliberately NOT a blend. A static 50/50 is already expressible through
liveTrade.botWeights, and it would halve the ^XAU exposure on exactly the rare
days that exposure is worth having. Here the two legs are mutually exclusive: the
scarce signal preempts, and the carry basket owns the ~94% of days
XAUZenbotTreeBot sits in cash. The point is not more alpha — it is that the same
alpha stops sitting next to idle cash, so the equity curve accrues a track record
every day instead of six days a month.

Why mirror the parents' portfolios instead of recomputing their signals:
both parents already persist their portfolio to `bots.portfolio` on every run, so
the signal is materialised. Re-deriving it here would duplicate two nontrivial
strategies (a decision tree on ^XAU indicators, and an RRG rotation over five
ETFs) and could silently drift from what the parents actually traded — the bug
would show up as a live-money mismatch, not a test failure.

Consequences of that choice, stated plainly:
  * This bot is event-driven, so it is NOT backtestable via local_backtest().
    Its history can only be reconstructed by joining the parents' recorded
    portfolio_worth holdings — see docs/guides/ for the join.
  * It inherits the parents' schedules. If a parent's CronJob dies, its portfolio
    row keeps returning the last state it traded into, which is indistinguishable
    from a live signal. Hence the staleness guards below: a stale parent aborts
    the run (loud failure in run_logs) rather than trading on a fossil.

Universe: ^XAU (alpha leg) + VTI / IJS / TLT / SHY / IAU (carry leg).
Schedule: 35 21 * * 1-5 — after the daily bots, before the 21:50 C2 copier.
"""

import logging
from datetime import UTC, datetime, timedelta

from tradingbot.utils.botclass import Bot, BotRepository
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

# --- Alpha leg: the rare-but-good signal ---------------------------------
ALPHA_BOT = "XAUZenbotTreeBot"
ALPHA_SYMBOL = "^XAU"
# XAUZenbotTreeBot runs every 5 minutes Mon-Fri, and this bot only runs Mon-Fri,
# so anything older than a day means its CronJob is broken.
ALPHA_MAX_AGE = timedelta(hours=24)

# --- Carry leg: the "don't be cash" basket -------------------------------
CARRY_BOT = "GoldenButterflyMomBot"
CARRY_UNIVERSE = ["VTI", "IJS", "TLT", "SHY", "IAU"]
# GoldenButterflyMomBot rebalances weekly (Mondays 14:00 UTC), so by Friday its
# last successful run is legitimately ~7.3 days old.
CARRY_MAX_AGE = timedelta(days=10)

# PortfolioManager.sell() already deletes holdings below 1e-6, so anything above
# that is a real position rather than rounding residue.
DUST_QTY = 1e-6


class XAUZenCarryBot(Bot):
    """
    Meta-bot: XAUZenbotTreeBot's signal when it has one, GoldenButterflyMomBot otherwise.

    Args:
        exclude_iau: Drop IAU from the mirrored carry basket, treating its weight
            as cash. The two legs never hold simultaneously, so there is no
            same-day overlap to remove — but the carry leg does sit in gold on
            precisely the days the alpha leg refuses to, and those are correlated
            assets. Default False mirrors GoldenButterflyMomBot exactly, so this
            bot's carry leg keeps the parent's measured drawdown profile.
    """

    def __init__(self, exclude_iau: bool = False):
        super().__init__(
            "XAUZenCarryBot",
            tickers=[ALPHA_SYMBOL, *CARRY_UNIVERSE],
            interval="1d",
            period="1mo",
        )
        self.exclude_iau = exclude_iau
        self.carry_universe = [t for t in CARRY_UNIVERSE if not (exclude_iau and t == "IAU")]

    # ------------------------------------------------------------------
    # Parent state
    # ------------------------------------------------------------------

    def _read_parent(self, bot_name: str, max_age: timedelta) -> dict:
        """
        Read a parent bot's portfolio, refusing to act on missing or stale state.

        Raises:
            RuntimeError: if the parent has no row, has never run successfully, or
                its last successful run is older than max_age. Raising (rather
                than falling back) makes Bot.run() write a failed RunLog and the
                CronJob go red, instead of silently trading a stale signal.
        """
        portfolio = BotRepository.read_portfolio(bot_name)
        if portfolio is None:
            raise RuntimeError(f"Parent bot {bot_name} has no row in `bots` — cannot derive a target.")

        last_run = BotRepository.last_successful_run(bot_name)
        if last_run is None:
            raise RuntimeError(f"Parent bot {bot_name} has never completed a run.")

        # last_run is naive UTC (BotRepository.last_successful_run's documented return
        # type), so this must stay naive too, or the subtraction raises TypeError.
        # utcnow() is deprecated in 3.12; now(UTC).replace(tzinfo=None) is the same value.
        age = datetime.now(UTC).replace(tzinfo=None) - last_run
        if age > max_age:
            raise RuntimeError(
                f"Parent bot {bot_name} is stale: last successful run {last_run} "
                f"({age.total_seconds() / 3600:.1f}h ago, limit "
                f"{max_age.total_seconds() / 3600:.0f}h). Refusing to mirror it."
            )

        logger.info(
            "%s: last run %s (%.1fh ago), portfolio %s",
            bot_name,
            last_run,
            age.total_seconds() / 3600,
            portfolio,
        )
        return portfolio

    def _carry_weights(self, portfolio: dict) -> dict[str, float]:
        """
        Convert GoldenButterflyMomBot's share quantities into portfolio weights.

        Weights (not quantities) are what transfers: the two bots have different
        equity, and this bot's own capital base drifts away from the parent's as
        soon as the alpha leg takes over for a stretch.

        Anything the parent holds outside self.carry_universe — a leftover from a
        universe change, or IAU when exclude_iau is set — is priced into the
        parent's total but left out of the weights, so its sleeve lands in cash.
        That is the whole point: dropping IAU must mean "hold cash instead of
        gold", not "put gold's share into VTI". Excluding it from the denominator
        instead would quietly lever up the equity legs.

        USD absorbs the remainder, which also guarantees the sum-to-1.0 check in
        rebalancePortfolio() passes.
        """
        held = {sym: qty for sym, qty in portfolio.items() if sym != "USD" and qty > DUST_QTY}
        cash = float(portfolio.get("USD", 0.0))

        if not held:
            logger.info("%s holds no asset — carry leg goes to cash", CARRY_BOT)
            return {"USD": 1.0}

        # Price everything the parent holds, mirrored or not: without the value of
        # the excluded sleeves we cannot know the parent's total, so every weight
        # would be wrong rather than merely incomplete.
        prices = self.getLatestPricesBatch(list(held))
        missing = [sym for sym in held if not prices.get(sym)]
        if missing:
            raise RuntimeError(f"No price for {missing} — cannot mirror {CARRY_BOT}.")

        values = {sym: qty * prices[sym] for sym, qty in held.items()}
        total = cash + sum(values.values())
        if total <= 0:
            raise RuntimeError(f"{CARRY_BOT} values at ${total:.2f} — cannot derive weights.")

        weights = {sym: val / total for sym, val in values.items() if sym in self.carry_universe}
        skipped = sorted(set(values) - set(weights))
        if skipped:
            logger.info("Not mirroring %s — their weight goes to cash", skipped)
        weights["USD"] = max(0.0, 1.0 - sum(weights.values()))
        return weights

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------

    def makeOneIteration(self) -> int:
        """
        Rebalance to the alpha leg if it has a signal, else to the carry leg.

        Returns:
            1: alpha leg engaged (100% ^XAU)
            0: carry leg engaged (mirroring GoldenButterflyMomBot)
        """
        alpha_portfolio = self._read_parent(ALPHA_BOT, ALPHA_MAX_AGE)
        alpha_qty = float(alpha_portfolio.get(ALPHA_SYMBOL, 0.0))

        if alpha_qty > DUST_QTY:
            logger.info(
                "ALPHA LEG: %s holds %.6f %s — going 100%% %s",
                ALPHA_BOT,
                alpha_qty,
                ALPHA_SYMBOL,
                ALPHA_SYMBOL,
            )
            self.rebalancePortfolio({ALPHA_SYMBOL: 1.0}, onlyOver50USD=True)
            return 1

        carry_portfolio = self._read_parent(CARRY_BOT, CARRY_MAX_AGE)
        weights = self._carry_weights(carry_portfolio)
        logger.info(
            "CARRY LEG: %s is flat — mirroring %s: %s",
            ALPHA_BOT,
            CARRY_BOT,
            {sym: round(w, 4) for sym, w in weights.items()},
        )
        self.rebalancePortfolio(weights, onlyOver50USD=True)
        return 0


# Guarded so tests can import the mirroring logic without opening a DB
# connection. The Helm CronJob invokes `python xauzencarrybot.py`, so
# __name__ == "__main__" and behaviour is unchanged.
if __name__ == "__main__":
    run_bot(XAUZenCarryBot)
