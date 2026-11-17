"""TradingView daily trend ensemble bot (Step S2 of PLAN-tvdaily.md).

Source:
    Ensemble voting strategy across the four TradingView trend-following indicators:
    - Supertrend (ATR trailing ratcheting stop)
    - Ultimate Moving Average (multi-timeframe slope confirmation)
    - AlphaTrend (momentum-biased ATR trailing band crossover)
    - Slingshot EMA Channel + Chandelier Exit (pullback into EMA zone with trailing stop)

Strategy:
    Operates on daily bars across UNIVERSE_WIDE (19 liquid cross-asset ETFs).
    Applies the winning hyperparameters and hysteresis minimum holding periods
    determined in S1b walk-forward optimization (docs/backtests/tv-daily-tune-2026-11.md).
    A ticker is held long if it receives at least `min_votes` active long signals.
    Equal-weighted among active qualifying tickers, capped at 0.20 per ticker, max gross 1.0.
    Unallocated weight remains in cash.

Execution & Judging:
    - Long-only via targetWeights (framework does not short; idle cash).
    - Schedule: 5 21 * * 1-5 (after US close at 21:05 UTC, before IBKR copier).
    - Paper only (paper portfolio tracking; no live broker copier).
    - Judged on alpha vs QQQ and its t-statistic.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.tv_core import to_ohlcv
from tradingbot.utils.tv_signals import (
    SUPPORTED_KINDS,
    UNIVERSE_WIDE,
    apply_min_hold,
    signal_flags,
    vote_weights,
)

logger = logging.getLogger(__name__)

# Winning hyperparameters from S1b walk-forward tuning (docs/backtests/tv-daily-tune-2026-11.md)
WINNING_SIGNAL_PARAMS: dict[str, dict[str, Any]] = {
    "supertrend": {"period": 7, "mult": 3.0},
    "ultimate_ma": {"kind": "sma", "length": 50, "slope_bars": 1},
    "alphatrend": {"period": 10, "mult": 2.0},
    "slingshot_chandelier": {"fast": 40, "slow": 80, "ch_period": 14, "ch_mult": 3.0},
}

WINNING_MIN_HOLD: dict[str, int] = {
    "supertrend": 10,
    "ultimate_ma": 5,
    "alphatrend": 0,
    "slingshot_chandelier": 10,
}

DEFAULT_MIN_VOTES: int = 2
DEFAULT_GROSS_CAP: float = 1.0
DEFAULT_MIN_ROWS: int = 60


def compute_weights(
    frames: dict[str, pd.DataFrame],
    signal_params: dict[str, dict[str, Any]] | None = None,
    min_holds: dict[str, int] | None = None,
    min_votes: int = DEFAULT_MIN_VOTES,
    gross_cap: float = DEFAULT_GROSS_CAP,
    min_rows: int = DEFAULT_MIN_ROWS,
) -> dict[str, float]:
    """Pure portfolio target weight calculation for daily TV trend ensemble.

    Parameters
    ----------
    frames : dict[str, pd.DataFrame]
        Mapping of ticker -> OHLCV DataFrame.
    signal_params : dict, optional
        Per-signal hyperparameters. Defaults to WINNING_SIGNAL_PARAMS.
    min_holds : dict, optional
        Per-signal hysteresis minimum hold bars. Defaults to WINNING_MIN_HOLD.
    min_votes : int, default 2
        Minimum number of signals voting long for a ticker to qualify.
    gross_cap : float, default 1.0
        Maximum total gross portfolio exposure.
    min_rows : int, default 60
        Minimum historical rows required for each ticker frame.

    Returns
    -------
    dict[str, float]
        Target weights dictionary (ticker -> weight). Empty if no qualifying tickers.
        Never includes a "USD" key.
    """
    if not frames:
        return {}

    params = WINNING_SIGNAL_PARAMS if signal_params is None else signal_params
    holds = WINNING_MIN_HOLD if min_holds is None else min_holds

    flags_dict: dict[str, dict[str, bool]] = {}

    for ticker, raw_df in frames.items():
        if raw_df is None or raw_df.empty:
            continue
        clean_df = to_ohlcv(raw_df) if not (isinstance(raw_df.index, pd.DatetimeIndex) and "close" in raw_df.columns) else raw_df
        if len(clean_df) < min_rows:
            continue

        ticker_votes: dict[str, bool] = {}
        for kind in SUPPORTED_KINDS:
            p = params.get(kind)
            mh = holds.get(kind, 0)
            fl_series = signal_flags(clean_df, kind=kind, params=p)
            if mh > 1:
                fl_series = apply_min_hold(fl_series, bars=mh)

            is_long = bool(fl_series.iloc[-1]) if len(fl_series) > 0 else False
            ticker_votes[kind] = is_long

        flags_dict[ticker] = ticker_votes

    return vote_weights(flags_dict, min_votes=min_votes, gross_cap=gross_cap)


class TvTrendDailyBot(Bot):
    """Daily TradingView trend ensemble bot across UNIVERSE_WIDE (19 ETFs).

    Long-only ensemble voting strategy allocating equal weights (capped at 0.20 per ticker)
    to assets supported by at least `min_votes` trend signals.
    """

    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "min_votes": [2, 3, 4],
        "gross_cap": [0.8, 1.0],
    }

    def __init__(
        self,
        min_votes: int = DEFAULT_MIN_VOTES,
        gross_cap: float = DEFAULT_GROSS_CAP,
        signal_params: dict[str, dict[str, Any]] | None = None,
        min_holds: dict[str, int] | None = None,
        min_rows: int = DEFAULT_MIN_ROWS,
        **kwargs,
    ):
        super().__init__(
            "TvTrendDailyBot",
            tickers=list(UNIVERSE_WIDE),
            interval="1d",
            period="5y",
            min_votes=min_votes,
            gross_cap=gross_cap,
            min_rows=min_rows,
            **kwargs,
        )
        self.min_votes = min_votes
        self.gross_cap = gross_cap
        self.signal_params = signal_params or WINNING_SIGNAL_PARAMS
        self.min_holds = min_holds or WINNING_MIN_HOLD
        self.min_rows = min_rows

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return target portfolio weights for the current daily bar."""
        frames: dict[str, pd.DataFrame] = {}
        for t in self.tradeable_tickers:
            df = self.datas.get(t)
            if df is not None and not df.empty:
                frames[t] = df

        if len(frames) < len(self.tradeable_tickers):
            return {}

        return compute_weights(
            frames,
            signal_params=self.signal_params,
            min_holds=self.min_holds,
            min_votes=self.min_votes,
            gross_cap=self.gross_cap,
            min_rows=self.min_rows,
        )


if __name__ == "__main__":
    run_bot(TvTrendDailyBot)
