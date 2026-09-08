"""
ShortVolCarryBot — short variance on the VIX curve, with the tail hedge in the rules.

Selling volatility earns a persistent premium and has an ugly left tail. The
strategy is only worth running — and only fundable — if the hedge is mechanical
from day one. A discretionary hedge is the same thing as no hedge: it is removed
in exactly the calm stretch before it is needed, because that is when it looks
most like a waste of money. So every risk control here is a rule with a number:

  1. Contango gate. Hold the short-vol leg only while ^VIX3M / ^VIX exceeds
     `contango_min`. In backwardation the roll works against the position, which
     is precisely the regime that produced every historical blow-up.
  2. Vol-targeted sizing, hard-capped. SVXY is a levered instrument by
     construction, so the cap is a backstop for the vol target rather than the
     usual binding constraint — see the note on `max_short` below, which is the
     one number the 2018 instrument change invalidated.
  3. A PERMANENT hedge. Whenever the short leg is on, `hedge_ratio` of the
     sleeve is in long volatility. Funded out of the sleeve, never conditional, never
     skipped because the premium looks good. This is the line item that makes
     the strategy defensible, and it is deliberately not a parameter anyone is
     invited to set to zero.
  4. NO trailing stop. `vix_kill` and `dd_kill` exist and default to None,
     because measuring them showed a stop-loss is the wrong instrument here —
     see "the stops were the bug" below. The gate in rule 1 is a condition on
     the curve that has to hold to be in the trade at all, which is a different
     thing from a stop and is the rule that actually protects.

**The instrument changed underneath this strategy, and any backtest must say so.**
SVXY tracked -1x the short-term VIX futures index until 28 February 2018. After
the 5 February 2018 event — in which the -1x fund XIV terminated and SVXY lost
roughly 90% in a day — ProShares cut the target to -0.5x. Pre- and post-2018
SVXY are different instruments and their series must not be treated as one:

  * Headline any backtest on 2018-03 onward. That window contains March 2020 and
    August 2024, both real tests of the hedge.
  * Report the pre-2018 regime separately, INCLUDING February 2018, and state
    what these rules would have done into it. An allocator will ask about that
    date; answering first is the difference between a track record and a story.

**The instrument change silently halved the strategy, through `max_short`.**
Vol-targeted sizing adapts to the change on its own — SVXY's realized vol fell
from 71.7% to 36.8% across the split, so `target_vol / vol` asks for 0.21 of the
book before 2018 and 0.41 after, correctly keeping risk constant. The *cap* does
not adapt. At its original 0.25 it sat just above what the vol target wanted
under -1x and well below it under -0.5x, so from 2018 it silently overrode the
sizing rule on nearly every bar and cut the position by ~40% — against a
premium already halved by the instrument, and a hedge whose cost did not halve.
Restoring the cap to the exposure it was written to permit is a correction, not
a tuning: at 0.50 it is once again a backstop rather than the binding rule.

**The stops were the bug.** Both kill rules are trailing triggers on a
mean-reverting instrument, and that is a category error: ^VIX above 30 and a 15%
drawdown from the 20-day high can only fire *after* the spike has happened, so
they sell at the bottom, and the re-entry wait then holds the bot out through
the normalization — which is exactly where short vol earns its money. Ablating
them improves every metric in every window (VIXM hedge, cap 0.50):

    window        rules            CAGR    Sharpe   maxDD
    full 14.7y    with stops      +1.84%    0.24    26.6%
                  stops removed   +3.49%    0.40    21.5%
    pre-2018      with stops      +4.05%    0.42    19.2%
                  stops removed   +6.98%    0.65    17.8%
    post-2018     with stops      +0.08%    0.05    20.0%
                  stops removed   +0.80%    0.14    15.1%

Note the drawdown column: the stops did not even buy protection. They cannot,
because the loss is already taken by the time the trigger is true.

**February 2018 is the proof, not the exception.** With both stops removed, the
bot still goes flat on Friday 2 February 2018 — the session before the crash —
and takes -9.1% through the event, against -9.7% with the stops on. The exit is
the contango gate, which inverted before the spike; the stops added nothing to
the one event they were written for. Turn the contango gate off as well and the
same window costs -24.7%, with the equity falling through 5-8 February. So the
gate is load-bearing and the stops were decoration. In every VIX event in this
sample a spike above 30 coincided with an inverted curve, which is why the kill
was redundant with the gate rather than additive to it.

Universe: SVXY (short vol) and a long-vol hedge, with ^VIX, ^VIX3M and SPY as
non-tradeable inputs. The hedge instrument is a parameter because the obvious
choice is not obviously right: VIXY (short-term futures) decays -51.5%/yr and
VIXM (mid-term) -19.3%/yr, but their tail responses differ by almost exactly the
same factor, so protection per unit of carrying cost is close to a wash on
paper. VIXM is the default because it measured better net (post-2018, +0.08%
against VIXY's -0.27%), not because the argument for it was obvious.

Schedule: 15 21 * * 1-5 — after the US close, before the 21:20 IBKR copier.
"""

import logging
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.vol_target import periods_per_year, realized_vol

logger = logging.getLogger(__name__)

SHORT_VOL = "SVXY"
HEDGE_SHORT_TERM = "VIXY"  # -51.5%/yr roll decay, ~3x the tail response of VIXM
HEDGE_MID_TERM = "VIXM"  # -19.3%/yr, a flatter part of the curve than SVXY shorts
VIX = "^VIX"
VIX3M = "^VIX3M"
BENCHMARK = "SPY"


class ShortVolCarryBot(Bot):
    """
    Short variance while the curve is in contango, always carrying a long-vol hedge.

    Args:
        contango_min: Minimum ^VIX3M / ^VIX ratio for the short leg to be on.
            1.03 keeps the bot out of the flat-curve regime that precedes
            backwardation, at the cost of missing some benign days.
        target_vol / max_short: Vol target and hard cap on the sleeve. The cap is
            a backstop against a collapsing vol estimate, NOT the intended
            day-to-day sizing rule — it was doing the latter from 2018 to 2026
            and cost the strategy most of its return. See the module docstring.
        hedge_ticker: Which long-vol ETF carries the hedge. Both cover the whole
            SVXY history. Not a free choice to leave at a default without
            checking: it changes both the drag and the tail response.
        hedge_ratio: Fraction of the sleeve held in the hedge whenever the short
            leg is on. Not optional — see the module docstring.
        vix_kill: ^VIX level above which everything goes to cash. None (the
            default) disables it. Kept as a knob only so the ablation in the
            module docstring stays reproducible — switching it on costs return
            and does not reduce drawdown.
        dd_kill: SVXY drawdown from its 20-day high that forces the same exit.
            None disables it, for the same measured reason.
        reentry_days: Consecutive days the contango gate must hold before the
            bot may hold the position. Applied on every bar, not only after an
            exit, which is what makes the gate stateless.
    """

    param_grid: ClassVar[dict] = {
        "contango_min": [1.0, 1.03, 1.05],
        "target_vol": [0.10, 0.15, 0.20],
        "hedge_ratio": [0.10, 0.15, 0.25],
    }

    # SVXY and VIXY both start in late 2011, so "max" is the full life of the
    # instruments. The regime split at 2018-02-28 is a reporting obligation, not
    # something the fetch can express — see the module docstring.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        contango_min: float = 1.03,
        target_vol: float = 0.15,
        max_short: float = 0.50,
        hedge_ticker: str = HEDGE_MID_TERM,
        hedge_ratio: float = 0.15,
        vix_kill: float | None = None,
        dd_kill: float | None = None,
        dd_window: int = 20,
        reentry_days: int = 5,
        vol_window: int = 30,
        **kwargs,
    ):
        super().__init__(
            "ShortVolCarryBot",
            tickers=[SHORT_VOL, hedge_ticker, VIX, VIX3M, BENCHMARK],
            # The two VIX indices are inputs and SPY is a reference; none is
            # tradeable, and benchmark_tickers is what guarantees that.
            benchmark_tickers=[VIX, VIX3M, BENCHMARK],
            interval="1d",
            period="6mo",
            contango_min=contango_min,
            target_vol=target_vol,
            max_short=max_short,
            hedge_ticker=hedge_ticker,
            hedge_ratio=hedge_ratio,
            vix_kill=vix_kill,
            dd_kill=dd_kill,
            dd_window=dd_window,
            reentry_days=reentry_days,
            vol_window=vol_window,
            **kwargs,
        )
        self.contango_min = contango_min
        self.target_vol = target_vol
        self.max_short = max_short
        self.hedge_ticker = hedge_ticker
        self.hedge_ratio = hedge_ratio
        self.vix_kill = vix_kill
        self.dd_kill = dd_kill
        self.dd_window = dd_window
        self.reentry_days = reentry_days
        self.vol_window = vol_window

    def _closes(self, ticker: str) -> pd.Series | None:
        frame = self.datas.get(ticker)
        if frame is None or frame.empty or "close" not in frame:
            return None
        closes = frame["close"].dropna()
        return closes if len(closes) else None

    def _contango_ratio(self, offset: int = 0) -> float | None:
        """^VIX3M / ^VIX `offset` bars back, or None if unavailable."""
        vix = self._closes(VIX)
        vix3m = self._closes(VIX3M)
        if vix is None or vix3m is None:
            return None
        idx = -1 - offset
        if len(vix) < offset + 1 or len(vix3m) < offset + 1:
            return None
        spot = float(vix.iloc[idx])
        if spot <= 0:
            return None
        return float(vix3m.iloc[idx]) / spot

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        vix = self._closes(VIX)
        svxy = self._closes(SHORT_VOL)
        if vix is None or svxy is None:
            logger.warning("%s: missing VIX or SVXY history — holding cash", self.bot_name)
            return {}

        # --- Rule 4: the optional stops, both disabled by default ---
        # Off unless a caller explicitly asks for them. They lose money and do
        # not lower drawdown; the module docstring carries the ablation.
        if self.vix_kill is not None:
            vix_now = float(vix.iloc[-1])
            if vix_now > self.vix_kill:
                logger.warning("%s: KILL — VIX %.1f > %.1f, going to cash", self.bot_name, vix_now, self.vix_kill)
                return {}

        if self.dd_kill is not None:
            recent = svxy.iloc[-self.dd_window :]
            peak = float(recent.max())
            drawdown = 1.0 - float(svxy.iloc[-1]) / peak if peak > 0 else 0.0
            if drawdown > self.dd_kill:
                logger.warning(
                    "%s: KILL — SVXY %.1f%% below its %dd high (limit %.1f%%), going to cash",
                    self.bot_name,
                    drawdown * 100,
                    self.dd_window,
                    self.dd_kill * 100,
                )
                return {}

        # --- Rule 1: contango gate, held for reentry_days consecutive days ---
        # Requiring the full run every day (not only after a kill) is what makes
        # this stateless. The bot keeps no memory between runs — a CronJob that
        # misses a day, or a backtest bar, must reach the same decision from the
        # price history alone, and anything remembered in self would not survive
        # either. The cost is a slightly later re-entry than a stateful version.
        ratios = [self._contango_ratio(offset) for offset in range(self.reentry_days)]
        if any(r is None for r in ratios):
            logger.info("%s: insufficient VIX curve history — holding cash", self.bot_name)
            return {}
        if not all(r > self.contango_min for r in ratios if r is not None):
            logger.info(
                "%s: curve not in sustained contango (last %d ratios %s, need > %.2f) — holding cash",
                self.bot_name,
                self.reentry_days,
                [round(r, 3) for r in ratios if r is not None],
                self.contango_min,
            )
            return {}

        # --- Rule 2: vol-targeted sizing, hard-capped ---
        ppy = periods_per_year(self.interval)
        vol = realized_vol(svxy, window=self.vol_window, periods_per_year=ppy)
        if vol <= 0:
            logger.info("%s: no usable SVXY vol estimate — holding cash", self.bot_name)
            return {}
        sleeve = min(self.target_vol / vol, self.max_short)

        # --- Rule 3: the permanent hedge, funded out of the sleeve ---
        hedge = sleeve * self.hedge_ratio
        short = sleeve - hedge

        logger.info(
            "%s: contango ok, SVXY %.1f%% + %s hedge %.1f%% (sleeve %.1f%%, SVXY vol %.0f%%)",
            self.bot_name,
            short * 100,
            self.hedge_ticker,
            hedge * 100,
            sleeve * 100,
            vol * 100,
        )
        return {SHORT_VOL: short, self.hedge_ticker: hedge}


if __name__ == "__main__":
    # bot = ShortVolCarryBot()
    # bot.local_development(objective="sharpe_ratio", param_sample_ratio=0.5)
    run_bot(ShortVolCarryBot)
