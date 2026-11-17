"""TradingView Ultimate Moving Average Trend Strategy (CM_Ultimate_MA_MTF).

Source:
    TradingView CM_Ultimate_MA_MTF by ChrisMoody:
    https://www.tradingview.com/script/v7e0eZ8K-CM-Ultimate-MA-MTF-V2/

Strategy:
    Trend-following strategy on an ETF universe (QQQ, SPY, IWM, GLD, TLT) using
    ChrisMoody's Ultimate Moving Average with multi-timeframe confirmation.
    A position is held long while the moving average slope direction is positive (+1)
    on BOTH the base timeframe (1h) and the higher timeframe (HTF, default 4h).
    Held tickers are equal-weighted at 1/5 (20%) each; unallocated weight is held in cash.

Departures from source:
    - Long-only: framework does not short; bearish or falling signals mean flat (cash).
    - Multi-asset equal weighting: universe is QQQ, SPY, IWM, GLD, TLT; each active ticker
      gets 1/5 (20%) to cap beta by construction instead of cloning QQQ.
    - Judged on alpha vs QQQ and its t-stat, not raw return.
"""

from __future__ import annotations

import math
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.tv_core import active_weights, to_ohlcv
from tradingbot.utils.tv_ultimate_ma import SUPPORTED_KINDS, ultimate_ma

UNIVERSE: list[str] = ["QQQ", "SPY", "IWM", "GLD", "TLT"]
DEFAULT_KIND: str = "hma"
DEFAULT_LENGTH: int = 20
DEFAULT_SLOPE_BARS: int = 1
DEFAULT_HTF: str = "4h"


def min_warmup_bars(
    kind: str,
    length: int,
    slope_bars: int,
    htf: str | None = None,
) -> int:
    """Calculate minimum base bars required for Ultimate MA to warm up."""
    kind_clean = str(kind).lower().strip()
    if kind_clean == "hma":
        base_need = length + int(math.isqrt(length)) + slope_bars
    elif kind_clean in ("tema",):
        base_need = length * 3 + slope_bars
    elif kind_clean in ("t3",):
        base_need = length * 6 + slope_bars
    else:
        base_need = length + slope_bars

    mult = 1
    if htf:
        s = htf.strip().lower()
        if s.endswith("h"):
            try:
                mult = max(1, int(s[:-1]))
            except ValueError:
                mult = 1
        elif s.endswith("d"):
            try:
                mult = max(1, int(s[:-1]) * 24)
            except ValueError:
                mult = 24

    return (base_need + 1) * mult


def compute_weights(
    frames: dict[str, pd.DataFrame],
    kind: str = DEFAULT_KIND,
    length: int = DEFAULT_LENGTH,
    slope_bars: int = DEFAULT_SLOPE_BARS,
    htf: str = DEFAULT_HTF,
    universe: list[str] | None = None,
) -> dict[str, float]:
    """Compute target portfolio weights for Ultimate MA strategy.

    Long while ultimate_ma.direction == +1 on both base bar and HTF MA.
    Returns target weights where each active ticker receives 1 / len(universe)
    (default 1/5 = 0.20), and inactive / unallocated weight remains cash.
    Fewer rows than warmup returns {}.
    """
    kind_clean = str(kind).lower().strip()
    if kind_clean not in SUPPORTED_KINDS:
        raise ValueError(f"Unknown moving average kind '{kind}'. Expected one of: {', '.join(SUPPORTED_KINDS)}")
    if length <= 0:
        raise ValueError(f"length must be positive, got {length}")
    if kind_clean == "hma" and length < 2:
        raise ValueError(f"period length must be at least 2 for hma, got {length}")
    if slope_bars <= 0:
        raise ValueError(f"slope_bars must be positive, got {slope_bars}")

    if not frames:
        return {}

    if universe is not None:
        target_universe = list(universe)
    elif any(t in frames for t in UNIVERSE):
        target_universe = list(UNIVERSE)
    else:
        target_universe = list(frames.keys())

    n_universe = len(target_universe)
    if n_universe == 0:
        return {}

    warmup = min_warmup_bars(kind_clean, length, slope_bars, htf)

    flags: dict[str, bool] = {}
    for ticker in target_universe:
        raw = frames.get(ticker)
        if raw is None or raw.empty:
            continue

        if not (isinstance(raw.index, pd.DatetimeIndex) and "close" in raw.columns):
            cols = [c for c in raw.columns if str(c).lower() != "volume"]
            clean = to_ohlcv(raw[cols])
        else:
            clean = raw
        if len(clean) < warmup:
            continue

        try:
            base_res = ultimate_ma(clean, kind=kind_clean, length=length, slope_bars=slope_bars)
            htf_res = ultimate_ma(clean, kind=kind_clean, length=length, slope_bars=slope_bars, htf_rule=htf)
        except Exception:
            continue

        if base_res.empty or htf_res.empty:
            continue

        base_dir = base_res["direction"].iloc[-1]
        htf_dir = htf_res["direction"].iloc[-1]

        # Long while direction == +1 on base bar and HTF MA
        if pd.notna(base_dir) and base_dir == 1.0 and pd.notna(htf_dir) and htf_dir == 1.0:
            flags[ticker] = True
        else:
            flags[ticker] = False

    if not flags:
        return {}

    return active_weights(flags, n_universe=n_universe)


class UltimateMABot(Bot):
    """TradingView Ultimate Moving Average MTF Bot on QQQ, SPY, IWM, GLD, TLT.

    Holds active ETFs when both base-bar MA and HTF MA slope upwards (direction == +1).
    Equal weights active ETFs at 1/5 each; cash absorbs the remainder.
    """

    # Longest 1h intraday history accepted by yfinance data layer (730 days)
    BACKTEST_PERIOD: ClassVar[str | None] = "730d"

    param_grid: ClassVar[dict] = {
        "kind": ["hma", "ema", "sma"],
        "length": [10, 20, 50],
        "slope_bars": [1, 2],
        "htf": ["4h", "1D"],
    }

    def __init__(
        self,
        kind: str = DEFAULT_KIND,
        length: int = DEFAULT_LENGTH,
        slope_bars: int = DEFAULT_SLOPE_BARS,
        htf: str = DEFAULT_HTF,
        tickers: list[str] | None = None,
        interval: str = "1h",
        period: str = "60d",
        **kwargs,
    ):
        kind_clean = str(kind).lower().strip()
        if kind_clean not in SUPPORTED_KINDS:
            raise ValueError(f"Unknown moving average kind '{kind}'. Expected one of: {', '.join(SUPPORTED_KINDS)}")
        if length <= 0:
            raise ValueError(f"length must be positive, got {length}")
        if kind_clean == "hma" and length < 2:
            raise ValueError(f"period length must be at least 2 for hma, got {length}")
        if slope_bars <= 0:
            raise ValueError(f"slope_bars must be positive, got {slope_bars}")

        resolved_tickers = list(tickers) if tickers is not None else list(UNIVERSE)

        super().__init__(
            "UltimateMABot",
            tickers=resolved_tickers,
            interval=interval,
            period=period,
            kind=kind_clean,
            length=length,
            slope_bars=slope_bars,
            htf=htf,
            **kwargs,
        )
        self.kind = kind_clean
        self.length = length
        self.slope_bars = slope_bars
        self.htf = htf

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return target weights for the current hourly bar."""
        frames: dict[str, pd.DataFrame] = {}
        tickers = getattr(self, "tradeable_tickers", self.tickers)
        for t in tickers:
            df = getattr(self, "datas", {}).get(t)
            if df is not None and not df.empty:
                cols = [c for c in df.columns if str(c).lower() != "volume"]
                frames[t] = to_ohlcv(df[cols])
        return compute_weights(
            frames,
            kind=self.kind,
            length=self.length,
            slope_bars=self.slope_bars,
            htf=self.htf,
            universe=tickers,
        )


if __name__ == "__main__":
    run_bot(UltimateMABot)
