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

# S1b ship rule: tuned params for ultimate_ma / slingshot_chandelier; defaults kept for
# supertrend / alphatrend (tuned winners lost to defaults out of sample).
WINNING_SIGNAL_PARAMS: dict[str, dict[str, Any]] = {
    "supertrend": {"period": 10, "mult": 3.0},
    "ultimate_ma": {"kind": "sma", "length": 50, "slope_bars": 1},
    "alphatrend": {"period": 14, "mult": 1.0},
    "slingshot_chandelier": {"fast": 40, "slow": 80, "ch_period": 14, "ch_mult": 3.0},
}

WINNING_MIN_HOLD: dict[str, int] = {
    "supertrend": 0,
    "ultimate_ma": 5,
    "alphatrend": 0,
    "slingshot_chandelier": 10,
}

DEFAULT_MIN_VOTES: int = 2
DEFAULT_GROSS_CAP: float = 1.0
DEFAULT_MIN_ROWS: int = 60
DEFAULT_USE_DEFENSIVE: bool = True
DEFAULT_DEFENSIVE_ASSETS: tuple[str, ...] = ("TLT", "IEF", "GLD", "SHV")
DEFAULT_PER_TICKER_CAP: float = 0.20


def compute_weights(
    frames: dict[str, pd.DataFrame],
    signal_params: dict[str, dict[str, Any]] | None = None,
    min_holds: dict[str, int] | None = None,
    min_votes: int = DEFAULT_MIN_VOTES,
    gross_cap: float = DEFAULT_GROSS_CAP,
    min_rows: int = DEFAULT_MIN_ROWS,
    kinds: tuple[str, ...] | None = None,
    use_defensive: bool = DEFAULT_USE_DEFENSIVE,
    defensive_assets: tuple[str, ...] = DEFAULT_DEFENSIVE_ASSETS,
    per_ticker_cap: float = DEFAULT_PER_TICKER_CAP,
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
    kinds : tuple[str, ...], optional
        Signals to evaluate. Defaults to SUPPORTED_KINDS.
    use_defensive : bool, default True
        If True, rotates unallocated risk budget into active defensive assets
        (winning variant: ensemble_stat+defensive from S2 walk-forward study).
    defensive_assets : tuple[str, ...], default ("TLT", "IEF", "GLD", "SHV")
        Basket of defensive assets for rotation.
    per_ticker_cap : float, default 0.20
        Maximum target weight allowed for any individual ticker.

    Returns
    -------
    dict[str, float]
        Target weights dictionary (ticker -> weight). Empty if no qualifying tickers.
        Never includes a "USD" key.
    """
    if not frames or gross_cap <= 0.0 or min_votes < 1:
        return {}

    params = WINNING_SIGNAL_PARAMS if signal_params is None else signal_params
    holds = WINNING_MIN_HOLD if min_holds is None else min_holds

    flags_dict: dict[str, dict[str, bool]] = {}

    for ticker, raw_df in frames.items():
        if ticker == "USD" or raw_df is None or raw_df.empty:
            continue
        clean_df = (
            to_ohlcv(raw_df)
            if not (isinstance(raw_df.index, pd.DatetimeIndex) and "close" in raw_df.columns)
            else raw_df
        )
        if len(clean_df) < min_rows:
            continue

        ticker_votes: dict[str, bool] = {}
        for kind in kinds or SUPPORTED_KINDS:
            p = params.get(kind)
            mh = holds.get(kind, 0)
            fl_series = signal_flags(clean_df, kind=kind, params=p)
            if mh > 1:
                fl_series = apply_min_hold(fl_series, bars=mh)

            is_long = bool(fl_series.iloc[-1]) if len(fl_series) > 0 else False
            ticker_votes[kind] = is_long

        flags_dict[ticker] = ticker_votes

    if not flags_dict:
        return {}

    if not use_defensive:
        return vote_weights(flags_dict, min_votes=min_votes, gross_cap=gross_cap)

    # Defensive rotation: separate risk assets and defensive assets
    def_set = set(defensive_assets)
    risk_flags = {t: v for t, v in flags_dict.items() if t not in def_set}
    def_flags = {t: v for t, v in flags_dict.items() if t in def_set}

    # Allocate risk assets first
    risk_weights = vote_weights(risk_flags, min_votes=min_votes, gross_cap=gross_cap)
    risk_alloc = sum(risk_weights.values())
    unallocated = max(0.0, gross_cap - risk_alloc)

    if unallocated <= 1e-7 or not def_flags:
        return risk_weights

    # Identify qualifying defensive assets whose trend is active
    req_votes = min_votes if kinds is None else min(min_votes, len(kinds))
    active_defs = [t for t, v in def_flags.items() if sum(1 for is_active in v.values() if is_active) >= req_votes]

    if not active_defs:
        return risk_weights

    # Allocate unallocated budget among active defensives
    w_def = min(per_ticker_cap, unallocated / float(len(active_defs)))
    out = dict(risk_weights)
    for d in active_defs:
        out[d] = w_def

    return out


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
        use_defensive: bool = DEFAULT_USE_DEFENSIVE,
        defensive_assets: tuple[str, ...] = DEFAULT_DEFENSIVE_ASSETS,
        per_ticker_cap: float = DEFAULT_PER_TICKER_CAP,
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
        self.use_defensive = use_defensive
        self.defensive_assets = defensive_assets
        self.per_ticker_cap = per_ticker_cap

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
            use_defensive=self.use_defensive,
            defensive_assets=self.defensive_assets,
            per_ticker_cap=self.per_ticker_cap,
        )


if __name__ == "__main__":
    run_bot(TvTrendDailyBot)
