"""TradingView Slingshot EMA Channel + Chandelier Exit Trend Bot.

Source:
- Slingshot EMA Channel pullback / trend system (fast EMA 34, slow EMA 50, ATR 14).
- Chandelier Exit trailing stop (Everget / Alex Orekhov, period 22, mult 3.0).

Departures from source:
- Long-only: the framework cannot short. Pullback short signals and short Chandelier
  positions are ignored; a bearish signal or a stop-out flips the asset to flat (cash).
- Weight-based allocation: universe QQQ, SPY, IWM, GLD, TLT. Each active ticker receives
  1/5 (20%) weight; idle weight stays in cash, capping beta by construction.
- State is rebuilt on every evaluation by replaying the signal series over the loaded
  window (no persisted position state), ensuring idempotent execution across restarts.

Judging rule:
- Evaluated by alpha vs QQQ and its t-stat, not raw return.

Warmup guard: fewer rows than warmup (max(slow, ch_period)) returns {}.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.tv_core import active_weights
from tradingbot.utils.tv_slingshot_chandelier import _clean_ohlcv, chandelier_exit, slingshot

UNIVERSE: list[str] = ["QQQ", "SPY", "IWM", "GLD", "TLT"]
FAST: int = 34
SLOW: int = 50
CH_PERIOD: int = 22
CH_MULT: float = 3.0
MAX_EXTENSION_ATR: float = 2.0


def _replay_ticker(
    df: pd.DataFrame,
    fast: int = FAST,
    slow: int = SLOW,
    ch_period: int = CH_PERIOD,
    ch_mult: float = CH_MULT,
    max_extension_atr: float = MAX_EXTENSION_ATR,
) -> bool:
    """Replay Slingshot and Chandelier Exit state over the window to determine position on the last bar.

    Entry: bull_trend and pullback_long and not overextended.
    Exit: close below Chandelier long_stop or bear_trend.
    """
    ss = slingshot(
        df,
        fast=fast,
        slow=slow,
        max_extension_atr=max_extension_atr,
    )
    ce = chandelier_exit(
        df,
        period=ch_period,
        mult=ch_mult,
    )

    closes = df["close"].to_numpy(dtype=float)
    long_stops = ce["long_stop"].to_numpy(dtype=float)
    bull_trends = ss["bull_trend"].to_numpy(dtype=bool)
    bear_trends = ss["bear_trend"].to_numpy(dtype=bool)
    pullback_longs = ss["pullback_long"].to_numpy(dtype=bool)
    overextendeds = ss["overextended"].to_numpy(dtype=bool)

    in_position = False
    for i in range(len(df)):
        close_i = closes[i]
        long_stop_i = long_stops[i]
        bull_i = bull_trends[i]
        bear_i = bear_trends[i]
        pb_i = pullback_longs[i]
        oe_i = overextendeds[i]

        exit_cond = bear_i or (not np.isnan(long_stop_i) and close_i < long_stop_i)
        entry_cond = bull_i and pb_i and (not oe_i)

        if in_position:
            if exit_cond:
                in_position = False
        else:
            if entry_cond and not exit_cond:
                in_position = True

    return in_position


def compute_weights(
    frames: dict[str, pd.DataFrame],
    fast: int = FAST,
    slow: int = SLOW,
    ch_period: int = CH_PERIOD,
    ch_mult: float = CH_MULT,
    universe: list[str] | None = None,
    max_extension_atr: float = MAX_EXTENSION_ATR,
) -> dict[str, float]:
    """Compute target weights across the universe from OHLCV bar frames.

    Fewer rows than the warmup (max(slow, ch_period)) returns {}.
    Active tickers are allocated 1/n_universe each.
    """
    warmup = max(slow, ch_period)
    if not frames:
        return {}

    clean_frames: dict[str, pd.DataFrame] = {}
    for ticker, frame in frames.items():
        if frame is None or frame.empty:
            return {}
        clean_df = _clean_ohlcv(frame)
        if len(clean_df) < warmup:
            return {}
        clean_frames[ticker] = clean_df

    if not clean_frames:
        return {}

    flags: dict[str, bool] = {}
    for ticker, df in clean_frames.items():
        flags[ticker] = _replay_ticker(
            df,
            fast=fast,
            slow=slow,
            ch_period=ch_period,
            ch_mult=ch_mult,
            max_extension_atr=max_extension_atr,
        )

    n_universe = len(universe) if universe is not None else len(UNIVERSE)
    return active_weights(flags, n_universe)


class SlingshotChandelierBot(Bot):
    """Hourly Slingshot EMA Channel + Chandelier Exit trend bot.

    Allocates 1/5 of the portfolio to each active ticker in UNIVERSE
    (QQQ, SPY, IWM, GLD, TLT). A ticker is held when entered on a pullback
    into the EMA channel during a bull trend (not overextended), and exited
    when price closes below the Chandelier Exit long_stop or trend flips to bear.
    """

    BACKTEST_PERIOD: ClassVar[str | None] = "730d"

    param_grid: ClassVar[dict] = {
        "fast": [20, 34],
        "slow": [50, 75],
        "ch_period": [14, 22],
        "ch_mult": [2.0, 3.0],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        ch_period: int = CH_PERIOD,
        ch_mult: float = CH_MULT,
        tickers: list[str] | None = None,
        **kwargs,
    ):
        chosen_tickers = list(tickers) if tickers is not None else list(UNIVERSE)
        super().__init__(
            "SlingshotChandelierBot",
            tickers=chosen_tickers,
            interval="1h",
            period="60d",
            fast=fast,
            slow=slow,
            ch_period=ch_period,
            ch_mult=ch_mult,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.ch_period = ch_period
        self.ch_mult = ch_mult

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the ETF book for the current hourly bar."""
        frames = {
            t: _clean_ohlcv(self.datas[t])
            for t in self.tradeable_tickers
            if t in self.datas and self.datas[t] is not None and not self.datas[t].empty
        }
        return compute_weights(
            frames,
            fast=self.fast,
            slow=self.slow,
            ch_period=self.ch_period,
            ch_mult=self.ch_mult,
            universe=self.tradeable_tickers,
        )


if __name__ == "__main__":
    run_bot(SlingshotChandelierBot)
