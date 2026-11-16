"""TradingView Supertrend (MTF) trend-following bot.

Indicator source:
    TradingView Supertrend (Pine Script v5 ta.supertrend semantics: hl2 +/- mult * ATR).
    A Higher Timeframe (HTF, default 1D) Supertrend is aligned onto the base (1h)
    timeframe using completed HTF bars shifted by 1 bar via align_htf (causal,
    lookahead_off, no repaint).

Departures from source:
    Long-only, as the framework's targetWeights does not short. An asset is held
    (weight 1/5) only when both its base 1h Supertrend and its HTF Supertrend
    directions are +1 (bullish). A bearish signal (-1 on either timeframe) or
    unformed warmup history leaves that allocation flat in cash.

Judging rule:
    Judged by alpha vs QQQ and its t-statistic, not raw return.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.tv_core import active_weights, to_ohlcv
from tradingbot.utils.tv_supertrend import htf_supertrend_direction, supertrend

UNIVERSE = ["QQQ", "SPY", "IWM", "GLD", "TLT"]
PERIOD = 10
MULT = 3.0
HTF = "1D"
MIN_ROWS = 10


def _safe_to_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize price frame using tv_core.to_ohlcv without volume-related attribute errors."""
    if df is None or not hasattr(df, "columns") or df.empty:
        return to_ohlcv(df)
    cols = [c for c in df.columns if str(c).lower() != "volume"]
    return to_ohlcv(df[cols])


def compute_weights(
    frames: dict[str, pd.DataFrame],
    period: int = PERIOD,
    mult: float = MULT,
    htf: str = HTF,
    min_rows: int = MIN_ROWS,
    n_universe: int = len(UNIVERSE),
) -> dict[str, float]:
    """Compute target weights for MTF Supertrend strategy.

    An asset is allocated a weight of 1/n_universe when both its base-timeframe
    Supertrend and its higher-timeframe (HTF) Supertrend directions are +1 (bullish).
    If either direction is -1 (bearish), or if data is missing/NaN, the asset
    allocation is flat (0.0).

    Returns an empty dict if frames is empty or if any asset's history has fewer
    rows than max(min_rows, period).
    """
    if not frames:
        return {}

    warmup = max(min_rows, period)
    flags: dict[str, bool] = {}

    for ticker, df in frames.items():
        if df is None:
            return {}
        ohlcv = _safe_to_ohlcv(df)
        if len(ohlcv) < warmup:
            return {}

        base_st = supertrend(ohlcv, period=period, mult=mult)
        htf_dir_series = htf_supertrend_direction(ohlcv, rule=htf, period=period, mult=mult)

        if base_st.empty or htf_dir_series.empty:
            flags[ticker] = False
            continue

        base_dir = base_st["direction"].iloc[-1]
        htf_dir = htf_dir_series.iloc[-1]

        flags[ticker] = bool(base_dir == 1.0 and htf_dir == 1.0)

    return active_weights(flags, n_universe)


class SupertrendMTFBot(Bot):
    """
    Hourly MTF Supertrend trend-following bot on QQQ, SPY, IWM, GLD, TLT.

    Each ETF is held at equal weight (1/5) when both its hourly Supertrend and
    its daily Supertrend directions are +1. If either timeframe is bearish (-1)
    or unformed, the allocation stays in cash.

    Args:
        period: ATR period for Supertrend on base and HTF bars.
        mult: Band multiplier for Supertrend on base and HTF bars.
        htf: Higher timeframe rule for trend filter (default "1D").
        min_rows: Minimum hourly rows required before computing signals.
    """

    BACKTEST_PERIOD: ClassVar[str | None] = "730d"

    param_grid: ClassVar[dict] = {
        "period": [7, 10, 14],
        "mult": [2.0, 3.0, 4.0],
    }

    def __init__(
        self,
        period: int = PERIOD,
        mult: float = MULT,
        htf: str = HTF,
        min_rows: int = MIN_ROWS,
        **kwargs,
    ):
        super().__init__(
            "SupertrendMTFBot",
            tickers=list(UNIVERSE),
            interval="1h",
            period="60d",
            indicator_period=period,
            mult=mult,
            htf=htf,
            min_rows=min_rows,
            **kwargs,
        )
        self.indicator_period = period
        self.st_period = period
        self.mult = mult
        self.htf = htf
        self.min_rows = min_rows

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the target weights for the current hourly bar."""
        frames: dict[str, pd.DataFrame] = {}
        for t in self.tradeable_tickers:
            df = self.datas.get(t)
            if df is not None and not df.empty:
                frames[t] = _safe_to_ohlcv(df)

        if len(frames) < len(self.tradeable_tickers):
            return {}

        return compute_weights(
            frames,
            period=self.indicator_period,
            mult=self.mult,
            htf=self.htf,
            min_rows=self.min_rows,
            n_universe=len(self.tickers),
        )


if __name__ == "__main__":
    run_bot(SupertrendMTFBot)
