"""
Hourly AlphaTrend trend-following bot on QQQ, SPY, IWM, GLD, and TLT.

Indicator source:
    AlphaTrend indicator by Kıvanç Özbilgiç (TradingView Pine Script).
    Computes trailing support/resistance bands using ATR and MFI/RSI momentum.
    Buy signal fires on strict crossover(AlphaTrend, lagged_2_bars).
    Sell signal fires on strict crossunder(AlphaTrend, lagged_2_bars).

Long-only departure:
    The framework's targetWeights execution is long-only (no shorting).
    A buy signal opens / maintains a long position; a sell signal exits to flat
    (cash). Tickers without an active buy signal remain in cash.
    Each active ticker is equal-weighted at 1/len(UNIVERSE) (20% for 5 assets),
    capping beta and portfolio exposure by construction.

Replay logic:
    Enter on an AlphaTrend buy and stay long until the matching sell.
    State is rebuilt every run by replaying the signal series over the loaded
    window (no persisted position), ensuring crashes or repeated runs converge
    to the identical book. Stale entries older than the loaded window are not
    invented.

Performance judging rule:
    Judged by alpha vs QQQ and its t-stat, not total return.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.tv_alphatrend import alphatrend
from tradingbot.utils.tv_core import active_weights, to_ohlcv

UNIVERSE: list[str] = ["QQQ", "SPY", "IWM", "GLD", "TLT"]
DEFAULT_PERIOD: int = 14
DEFAULT_MULT: float = 1.0


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a price frame to OHLCV format safely.

    tv_core.to_ohlcv contains a known issue when volume is present due to calling
    fillna on a numpy ndarray. We normalize volume safely around to_ohlcv.
    """
    if frame is None or frame.empty:
        return to_ohlcv(frame)

    cols_map = {str(c).lower(): c for c in frame.columns}
    vol_col = cols_map.get("volume")

    if vol_col is not None:
        if "timestamp" in cols_map:
            axis = frame[cols_map["timestamp"]]
        else:
            axis = frame.index

        try:
            idx = pd.DatetimeIndex(pd.to_datetime(axis))
        except Exception:
            idx = pd.DatetimeIndex(pd.to_datetime(axis, utc=True))

        if idx.tz is not None:
            idx = idx.tz_convert(None)

        vol_series = pd.Series(
            pd.to_numeric(frame[vol_col], errors="coerce").fillna(0.0).to_numpy(),
            index=idx,
        )
        vol_series = vol_series[~vol_series.index.duplicated(keep="last")]

        df_no_vol = frame.drop(columns=[vol_col])
        ohlcv = to_ohlcv(df_no_vol)
        if not ohlcv.empty:
            ohlcv["volume"] = vol_series.reindex(ohlcv.index).fillna(0.0).astype(float)
        return ohlcv

    return to_ohlcv(frame)


def _is_held(ohlcv: pd.DataFrame, period: int, mult: float) -> bool:
    """Determine if a ticker is held on the last bar by replaying AlphaTrend signals."""
    if len(ohlcv) < period + 2:
        return False
    signals = alphatrend(ohlcv, period=period, mult=mult)
    buys = signals["buy"].to_numpy(dtype=bool)
    sells = signals["sell"].to_numpy(dtype=bool)

    held = False
    for b, s in zip(buys, sells, strict=True):
        if b:
            held = True
        elif s:
            held = False
    return held


def compute_weights(
    frames: dict[str, pd.DataFrame],
    period: int = DEFAULT_PERIOD,
    mult: float = DEFAULT_MULT,
    n_universe: int = len(UNIVERSE),
) -> dict[str, float]:
    """Compute active portfolio weights for tickers based on AlphaTrend buy/sell replay.

    Replays AlphaTrend buy/sell signals over the loaded window for each ticker.
    Active tickers receive 1/n_universe weight; idle weight stays in cash.
    Returns ``{}`` if no tickers are held or if all frames have fewer rows than
    the indicator warmup.
    """
    if not frames:
        return {}

    warmup = period + 2
    normalized: dict[str, pd.DataFrame] = {}
    for ticker, frame in frames.items():
        if frame is not None and not frame.empty:
            df = _normalize_frame(frame)
            if not df.empty:
                normalized[ticker] = df

    if not normalized or all(len(df) < warmup for df in normalized.values()):
        return {}

    flags = {ticker: _is_held(df, period, mult) for ticker, df in normalized.items()}
    return active_weights(flags, n_universe)


class AlphaTrendBot(Bot):
    """
    Hourly AlphaTrend trend-following bot on QQQ, SPY, IWM, GLD, and TLT.

    Args:
        period: Lookback period for ATR and momentum (MFI / RSI).
        mult: ATR multiplier coefficient for trailing bands.
    """

    BACKTEST_PERIOD: ClassVar[str | None] = "730d"

    param_grid: ClassVar[dict] = {
        "period": [10, 14, 20],
        "mult": [0.5, 1.0, 1.5, 2.0],
    }

    def __init__(
        self,
        period: int = DEFAULT_PERIOD,
        mult: float = DEFAULT_MULT,
        **kwargs,
    ):
        data_period = kwargs.pop("period", "60d")
        if isinstance(period, str) and not period.isdigit():
            data_period = period
            indicator_period = kwargs.pop("indicator_period", DEFAULT_PERIOD)
        else:
            indicator_period = int(period)

        tickers = kwargs.pop("tickers", list(UNIVERSE))
        interval = kwargs.pop("interval", "1h")

        super().__init__(
            "AlphaTrendBot",
            tickers=tickers,
            interval=interval,
            period=data_period,
            indicator_period=indicator_period,
            mult=mult,
            **kwargs,
        )
        self.at_period = indicator_period
        self.mult = mult

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return target portfolio weights for the current hourly bar."""
        frames = {
            t: _normalize_frame(self.datas[t])
            for t in self.tradeable_tickers
            if t in self.datas and self.datas[t] is not None
        }
        return compute_weights(
            frames,
            period=self.at_period,
            mult=self.mult,
            n_universe=len(self.tickers) if self.tickers else len(UNIVERSE),
        )


if __name__ == "__main__":
    run_bot(AlphaTrendBot)
