"""
Golden Butterfly Momentum Rotation Bot (Pattern B)

Manages a five-asset "Golden Butterfly" portfolio with a Relative Rotation
Graph (RRG) momentum overlay. Capital is tilted toward assets showing
positive relative strength vs the SPY benchmark, and redirected to
short-term bonds (SHY) or cash (USD) when assets enter the Lagging quadrant.

Universe : VTI (total market), IJS (small-cap value),
           TLT (long-term bonds), SHY (short-term bonds), IAU (gold)
Benchmark: SPY

RRG Classification:
  RS-Ratio  = 12-month log return relative to SPY (z-scored across universe)
  RS-Mom    = rate-of-change of relative return (12-month minus 1-month, z-scored)

  Leading   (RS-Ratio > 0, RS-Mom > 0) → buy (1); hold if CMF < 0 (0)
  Weakening (RS-Ratio > 0, RS-Mom < 0) → hold (0)
  Improving (RS-Ratio < 0, RS-Mom > 0) → buy (1) if OBV rising, else hold (0)
  Lagging   (RS-Ratio < 0, RS-Mom < 0) → sell (-1)

Signal mapping (decisionFunction return values):
  1  = buy / target full weight
  0  = hold (keep existing position, don't add)
  -1 = sell / exclude

SPY (benchmark) is included in self.tickers so its history is available in
self.datas for RRG computation, but decisionFunction always returns 0 for it.

Schedule: weekly 0 14 * * 1 (Monday 2 PM UTC, before US market open)

Research:
  docs/examples/Strategic-Synthesis-of-Adaptive-Mean-Reversion-and-Multi-Asset-Rotation.md
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from utils.botclass import Bot

logger = logging.getLogger(__name__)

UNIVERSE = ["VTI", "IJS", "TLT", "SHY", "IAU"]
BENCHMARK = "SPY"

LOOKBACK_12M = 252   # ~trading days in 12 months
LOOKBACK_1M = 21     # ~trading days in 1 month
OBV_WINDOW = 10      # OBV trend lookback (days)
CMF_PERIOD = 20      # Chaikin Money Flow rolling window


# ------------------------------------------------------------------
# Volume / money-flow helpers  (computed from raw OHLCV)
# ------------------------------------------------------------------

def _compute_obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume: cumulative signed volume."""
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def _compute_cmf(df: pd.DataFrame, period: int = CMF_PERIOD) -> float:
    """Latest Chaikin Money Flow value (positive = accumulation)."""
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    mfm = mfm.fillna(0.0)
    mfv = mfm * df["volume"]
    vol_sum = df["volume"].rolling(period).sum()
    mfv_sum = mfv.rolling(period).sum()
    cmf_series = (mfv_sum / vol_sum).fillna(0.0)
    return float(cmf_series.iloc[-1]) if len(cmf_series) >= period else 0.0


def _safe_log_return(df: pd.DataFrame, lookback: int) -> Optional[float]:
    """Log return over the last `lookback` bars; None if insufficient data."""
    if len(df) < lookback + 1:
        return None
    p_now = float(df["close"].iloc[-1])
    p_past = float(df["close"].iloc[-(lookback + 1)])
    if p_past <= 0 or p_now <= 0:
        return None
    return float(np.log(p_now / p_past))


def _zscore(arr: np.ndarray) -> np.ndarray:
    std = float(arr.std())
    return (arr - arr.mean()) / (std if std > 1e-12 else 1.0)


# ------------------------------------------------------------------
# Bot
# ------------------------------------------------------------------

class GoldenButterflyMomBot(Bot):
    """
    Golden Butterfly five-asset portfolio with RRG momentum overlay (Pattern B).

    Rebalances weekly. Assets in the Lagging quadrant are sold; OBV confirmation
    upgrades Improving assets to full weight; CMF distribution warning downgrades
    Leading assets to hold.

    Uses decisionFunction() for full backtestability via local_backtest().
    self.tickers includes SPY so its history is loaded into self.datas for RRG
    computation, but SPY is never traded (decisionFunction returns 0 for it).
    """

    def __init__(self):
        super().__init__(
            "GoldenButterflyMomBot",
            tickers=UNIVERSE + [BENCHMARK],
            interval="1d",
            period="2y",        # ~504 bars — enough for LOOKBACK_12M=252 + buffer
        )

    # ------------------------------------------------------------------
    # RRG signal computation
    # ------------------------------------------------------------------

    def _rrg_cache_key(self) -> tuple:
        """
        Cheap proxy for 'has self.datas advanced to a new bar?'
        Returns the latest timestamp (or row count) for each loaded ticker.
        """
        keys = []
        for sym, df in self.datas.items():
            if df is not None and not df.empty:
                if hasattr(df, "index") and not isinstance(df.index, pd.RangeIndex):
                    ts = df.index[-1]
                else:
                    ts = len(df)
                keys.append((sym, ts))
        return tuple(sorted(keys))

    def _compute_rrg_signals(self) -> dict:
        """
        Compute RRG quadrant signals for all UNIVERSE tickers from self.datas.

        Returns {ticker: signal} for each UNIVERSE ticker where signal is:
          1  = Leading, or Improving with OBV confirmation → buy
          0  = Weakening, or Improving without OBV, or Leading with CMF warning → hold
          -1 = Lagging → sell
        """
        spy_df = self.datas.get(BENCHMARK)
        if spy_df is None or spy_df.empty:
            logger.warning("Benchmark %s missing from self.datas — no signals", BENCHMARK)
            return {}

        spy_12m = _safe_log_return(spy_df, LOOKBACK_12M)
        spy_1m = _safe_log_return(spy_df, LOOKBACK_1M)
        if spy_12m is None or spy_1m is None:
            logger.warning("Insufficient %s history for RRG", BENCHMARK)
            return {}

        rs_ratio_raw: list = []
        rs_mom_raw: list = []
        valid_symbols: list = []

        for sym in UNIVERSE:
            df = self.datas.get(sym)
            if df is None or len(df) < LOOKBACK_12M + 1:
                logger.warning("%s: insufficient history — marking Lagging", sym)
                rs_ratio_raw.append(-1.0)
                rs_mom_raw.append(-1.0)
                valid_symbols.append(sym)
                continue

            r12 = _safe_log_return(df, LOOKBACK_12M)
            r1 = _safe_log_return(df, LOOKBACK_1M)
            if r12 is None or r1 is None:
                rs_ratio_raw.append(-1.0)
                rs_mom_raw.append(-1.0)
            else:
                # RS-Ratio: 12m return relative to benchmark
                rs_ratio_raw.append(r12 - spy_12m)
                # RS-Momentum: rate-of-change of relative outperformance
                rs_mom_raw.append((r12 - r1) - (spy_12m - spy_1m))

            valid_symbols.append(sym)

        if not valid_symbols:
            return {}

        rr_z = _zscore(np.array(rs_ratio_raw))
        rm_z = _zscore(np.array(rs_mom_raw))

        logger.info(f"{'SYM':4s}  {'QUADRANT':10s}  {'RR-Z':>6s}  {'RM-Z':>6s}  {'CMF':>6s}  {'OBV↑':>4s}  {'SIG':>3s}")
        logger.info("-" * 58)

        signals: dict = {}
        for i, sym in enumerate(valid_symbols):
            df = self.datas.get(sym)
            rr = rr_z[i]
            rm = rm_z[i]

            if df is not None and len(df) > OBV_WINDOW + 1:
                obv = _compute_obv(df)
                obv_rising = bool(obv.iloc[-1] > obv.iloc[-(OBV_WINDOW + 1)])
                cmf_val = _compute_cmf(df)
            else:
                obv_rising = False
                cmf_val = 0.0

            if rr >= 0 and rm >= 0:
                quadrant = "Leading"
                sig = 0 if cmf_val < 0 else 1
            elif rr >= 0 and rm < 0:
                quadrant = "Weakening"
                sig = 0
            elif rr < 0 and rm >= 0:
                quadrant = "Improving"
                sig = 1 if obv_rising else 0
            else:
                quadrant = "Lagging"
                sig = -1

            signals[sym] = sig
            logger.info(f"{sym:4s}  {quadrant:10s}  {rr:+6.2f}  {rm:+6.2f}  {cmf_val:+6.2f}  {str(obv_rising):>4s}  {sig:>3d}")

        return signals

    # ------------------------------------------------------------------
    # decisionFunction — called per ticker per bar by backtest and live loop
    # ------------------------------------------------------------------

    def decisionFunction(self, row: pd.Series) -> int:
        """
        Return -1 (sell), 0 (hold), or 1 (buy) for self._current_ticker.

        The backtest loop and _run_multi_ticker_iteration both set
        bot._current_ticker = ticker before calling this method, so the bot
        knows which asset it is deciding for.

        RRG signals are computed once per bar (cache keyed on self.datas state)
        and reused across all tickers at the same timestamp.
        """
        ticker = getattr(self, "_current_ticker", None)
        if ticker is None or ticker == BENCHMARK:
            return 0

        # Recompute RRG signals only when data snapshot advances to a new bar
        cache_key = self._rrg_cache_key()
        if getattr(self, "_rrg_cache_key_val", None) != cache_key:
            self._rrg_signals = self._compute_rrg_signals()
            self._rrg_cache_key_val = cache_key

        return self._rrg_signals.get(ticker, 0)


bot = GoldenButterflyMomBot()
bot.run()
# bot.local_backtest()
