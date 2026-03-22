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

  Leading   (RS-Ratio > 0, RS-Mom > 0) → full weight; reduce if CMF < 0
  Weakening (RS-Ratio > 0, RS-Mom < 0) → half weight
  Improving (RS-Ratio < 0, RS-Mom > 0) → full weight if OBV rising, else half
  Lagging   (RS-Ratio < 0, RS-Mom < 0) → excluded (weight → SHY or cash)

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

FULL_WEIGHT = 1.0
HALF_WEIGHT = 0.5


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

    Rebalances weekly. Assets in the Lagging quadrant are replaced by SHY
    (or USD if SHY is also Lagging) to reduce drawdown. OBV confirmation
    upgrades Improving assets to full weight; CMF distribution warning
    downgrades Leading assets to half weight.
    """

    def __init__(self):
        super().__init__("GoldenButterflyMomBot", symbol=None)

    def makeOneIteration(self) -> int:
        symbols = UNIVERSE + [BENCHMARK]
        data_long = self.getYFDataMultiple(
            symbols=symbols,
            interval="1d",
            period="2y",   # ~504 bars — enough for LOOKBACK_12M=252 + buffer
            saveToDB=True,
        )

        if data_long.empty:
            logger.warning("No data returned — skipping rebalance")
            return 0

        # ---- Build per-symbol DataFrames --------------------------------
        by_symbol: dict[str, pd.DataFrame] = {
            sym: grp.sort_values("timestamp").reset_index(drop=True)
            for sym, grp in data_long.groupby("symbol")
        }

        if BENCHMARK not in by_symbol:
            logger.warning("Benchmark %s missing — skipping rebalance", BENCHMARK)
            return 0

        spy_df = by_symbol[BENCHMARK]
        spy_12m = _safe_log_return(spy_df, LOOKBACK_12M)
        spy_1m = _safe_log_return(spy_df, LOOKBACK_1M)

        if spy_12m is None or spy_1m is None:
            logger.warning("Insufficient %s history for RRG — skipping rebalance", BENCHMARK)
            return 0

        # ---- Compute relative metrics per asset -------------------------
        # raw_weights: 0.0 / HALF_WEIGHT / FULL_WEIGHT per asset
        raw_weights: dict[str, float] = {}
        rs_ratio_raw: list[float] = []
        rs_mom_raw: list[float] = []
        valid_symbols: list[str] = []

        for sym in UNIVERSE:
            df = by_symbol.get(sym)
            if df is None or len(df) < LOOKBACK_12M + 1:
                logger.warning("%s: insufficient history — will be marked Lagging", sym)
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
                # = (12m - 1m relative return) captures acceleration
                rs_mom_raw.append((r12 - r1) - (spy_12m - spy_1m))

            valid_symbols.append(sym)

        if len(valid_symbols) < len(UNIVERSE):
            logger.warning("Some universe symbols are missing data; proceeding with available data")

        # ---- Z-score across universe ------------------------------------
        rr_arr = np.array(rs_ratio_raw)
        rm_arr = np.array(rs_mom_raw)
        rr_z = _zscore(rr_arr)
        rm_z = _zscore(rm_arr)

        # ---- Classify quadrants and assign raw weights ------------------
        logger.info(f"{'SYM':4s}  {'QUADRANT':10s}  {'RR-Z':>6s}  {'RM-Z':>6s}  {'CMF':>6s}  {'OBV↑':>4s}  {'W':>4s}")
        logger.info("-" * 60)

        for i, sym in enumerate(valid_symbols):
            df = by_symbol.get(sym)
            rr = rr_z[i]
            rm = rm_z[i]

            # Volume indicators from raw OHLCV
            if df is not None and len(df) > OBV_WINDOW + 1:
                obv = _compute_obv(df)
                obv_rising = bool(obv.iloc[-1] > obv.iloc[-(OBV_WINDOW + 1)])
                cmf_val = _compute_cmf(df)
            else:
                obv_rising = False
                cmf_val = 0.0

            if rr >= 0 and rm >= 0:
                quadrant = "Leading"
                # Distribution warning: CMF negative → reduce to half weight
                w = HALF_WEIGHT if cmf_val < 0 else FULL_WEIGHT
            elif rr >= 0 and rm < 0:
                quadrant = "Weakening"
                w = HALF_WEIGHT
            elif rr < 0 and rm >= 0:
                quadrant = "Improving"
                # Volume confirmation: OBV rising → full weight (institutional accumulation)
                w = FULL_WEIGHT if obv_rising else HALF_WEIGHT
            else:
                quadrant = "Lagging"
                w = 0.0

            raw_weights[sym] = w
            logger.info(f"{sym:4s}  {quadrant:10s}  {rr:+6.2f}  {rm:+6.2f}  {cmf_val:+6.2f}  {str(obv_rising):>4s}  {w:.1f}")

        # ---- Build target allocation ------------------------------------
        base = 1.0 / len(UNIVERSE)   # 0.20 per asset

        # Each asset gets (raw_weight × base); reduces lagging assets to 0
        target: dict[str, float] = {sym: raw_weights[sym] * base for sym in UNIVERSE}

        # Redirect freed capital (from Lagging/partial Weakening) to safe haven
        remaining = 1.0 - sum(target.values())
        if remaining > 1e-6:
            if raw_weights.get("SHY", 0.0) > 0:
                # SHY is active — absorb freed capital into it
                target["SHY"] = target.get("SHY", 0.0) + remaining
            else:
                # SHY itself is Lagging — redirect to cash
                target["USD"] = target.get("USD", 0.0) + remaining

        # Remove zero entries; normalise to guard against float rounding
        target = {k: v for k, v in target.items() if v > 1e-6}
        total = sum(target.values())
        target = {k: v / total for k, v in target.items()}

        logger.info(f"Target: {', '.join(f'{k}={v:.1%}' for k, v in sorted(target.items()))}")


        self.rebalancePortfolio(target, onlyOver50USD=True)
        return 0


bot = GoldenButterflyMomBot()
bot.run()
# bot.local_backtest()