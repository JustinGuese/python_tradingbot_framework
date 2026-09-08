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
     construction, so the cap binds far more often than the vol target does.
  3. A PERMANENT hedge. Whenever the short leg is on, `hedge_ratio` of the
     sleeve is in VIXY. Funded out of the sleeve, never conditional, never
     skipped because the premium looks good. This is the line item that makes
     the strategy defensible, and it is deliberately not a parameter anyone is
     invited to set to zero.
  4. A hard kill. ^VIX above `vix_kill`, or SVXY more than `dd_kill` below its
     20-day high, takes both legs to cash — and re-entry requires the contango
     gate to hold for `reentry_days` consecutive days, so the bot cannot
     oscillate back into a position mid-crisis.

**The instrument changed underneath this strategy, and any backtest must say so.**
SVXY tracked -1x the short-term VIX futures index until 28 February 2018. After
the 5 February 2018 event — in which the -1x fund XIV terminated and SVXY lost
roughly 90% in a day — ProShares cut the target to -0.5x. Pre- and post-2018
SVXY are different instruments and their series must not be treated as one:

  * Headline any backtest on 2018-03 onward. That window contains March 2020 and
    August 2024, both real tests of the hedge and the kill.
  * Report the pre-2018 regime separately, INCLUDING February 2018, and state
    what these rules would have done into it. An allocator will ask about that
    date; answering first is the difference between a track record and a story.

Universe: SVXY (short vol) and VIXY (the hedge), with ^VIX, ^VIX3M and SPY as
non-tradeable inputs.

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
HEDGE = "VIXY"
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
        target_vol / max_short: Vol target and hard cap on the SVXY weight. The
            cap is what actually binds most days.
        hedge_ratio: Fraction of the sleeve held in VIXY whenever the short leg
            is on. Not optional — see the module docstring.
        vix_kill: ^VIX level above which everything goes to cash.
        dd_kill: SVXY drawdown from its 20-day high that forces the same exit.
        reentry_days: Consecutive days the contango gate must hold before the
            bot may re-enter after a kill.
    """

    param_grid: ClassVar[dict] = {
        "contango_min": [1.0, 1.03, 1.05],
        "target_vol": [0.10, 0.15, 0.20],
        "hedge_ratio": [0.10, 0.15, 0.20],
    }

    # SVXY and VIXY both start in late 2011, so "max" is the full life of the
    # instruments. The regime split at 2018-02-28 is a reporting obligation, not
    # something the fetch can express — see the module docstring.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        contango_min: float = 1.03,
        target_vol: float = 0.15,
        max_short: float = 0.25,
        hedge_ratio: float = 0.15,
        vix_kill: float = 30.0,
        dd_kill: float = 0.15,
        dd_window: int = 20,
        reentry_days: int = 5,
        vol_window: int = 30,
        **kwargs,
    ):
        super().__init__(
            "ShortVolCarryBot",
            tickers=[SHORT_VOL, HEDGE, VIX, VIX3M, BENCHMARK],
            # The two VIX indices are inputs and SPY is a reference; none is
            # tradeable, and benchmark_tickers is what guarantees that.
            benchmark_tickers=[VIX, VIX3M, BENCHMARK],
            interval="1d",
            period="6mo",
            contango_min=contango_min,
            target_vol=target_vol,
            max_short=max_short,
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

        # --- Rule 4: hard kill, checked before anything else ---
        vix_now = float(vix.iloc[-1])
        if vix_now > self.vix_kill:
            logger.warning("%s: KILL — VIX %.1f > %.1f, going to cash", self.bot_name, vix_now, self.vix_kill)
            return {}

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
            "%s: contango ok, SVXY %.1f%% + VIXY hedge %.1f%% (sleeve %.1f%%, SVXY vol %.0f%%)",
            self.bot_name,
            short * 100,
            hedge * 100,
            sleeve * 100,
            vol * 100,
        )
        return {SHORT_VOL: short, HEDGE: hedge}


if __name__ == "__main__":
    # bot = ShortVolCarryBot()
    # bot.local_development(objective="sharpe_ratio", param_sample_ratio=0.5)
    run_bot(ShortVolCarryBot)
