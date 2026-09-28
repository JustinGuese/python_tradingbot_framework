"""
PredictionMarketOverlayBot — a balanced SPY/QQQ/IEF/GLD book whose equity sleeve
shrinks when prediction markets price macro risk.

A risk overlay, not a directional bet on the crowd. Each Friday:

1. **Risk score** r in [0, 1] = the larger of
   - recession: Kalshi's recession probability between `rec_lo` (r=0) and `rec_hi` (r=1);
   - hawkish repricing: the rise over `hawk_lookback` bars in the rate path priced
     for ~6 months out (Kalshi KXFED ladders), with `hawk_bps` of repricing = 1.
2. **Book.** Equity (SPY/QQQ split evenly) = `equity_max` x (1 - r). The weight it
   gives up goes to IEF when recession drives r (bonds rally into recessions) and
   to cash when hawkishness does (duration loses when the path reprices up).
   GLD holds a fixed `gold` sleeve; IEF the rest of the base book.
3. `signal="sma200"` swaps the prediction-market score for "QQQ below its
   200-day" (r=1 below, 0 above), and `signal="static"` holds the base book
   with no overlay. These are the ablations the bot must beat: a
   prediction-market signal that only matches a moving average adds nothing.

Features come from utils/prediction_market_features.py and are point-in-time:
bar D sees prices observed by D-1's end. A bar where every feature is NaN
returns None (no rebalance), not an exit.

NOT SCHEDULED and in no copier's botWeights: out of sample (2024-26) it made
+3.9%/yr alpha at t 1.03, below both the t >= 2 bar and the SMA200 ablation
(+6.7%, t 1.41). See docs/backtests/prediction-markets-2026-09.md. If it is ever
turned on: Friday after the close, "20 21 * * 5".
"""

import logging
from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils import prediction_market_features as pmf
from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

TICKERS = ["SPY", "QQQ", "IEF", "GLD"]
FEATURES = ["recession_prob", "fed_path_bps"]


def _bar_timestamp(row: pd.Series) -> pd.Timestamp:
    """The bar's time: a `timestamp` column live, the index label in the backtest."""
    if "timestamp" in row.index:
        return pd.Timestamp(row["timestamp"])
    return pd.Timestamp(row.name)


class PredictionMarketOverlayBot(Bot):
    param_grid: ClassVar[dict] = {
        "rec_lo": [0.15, 0.25],
        "rec_hi": [0.4, 0.6],
        "hawk_bps": [25.0, 50.0],
        "hawk_lookback": [10, 20],
        "equity_max": [0.6, 0.8],
    }

    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        signal: str = "pm",
        rec_lo: float = 0.25,
        rec_hi: float = 0.6,
        hawk_bps: float = 50.0,
        hawk_lookback: int = 20,
        equity_max: float = 0.8,
        gold: float = 0.1,
        rebalance_weekday: int | None = 4,
        **kwargs,
    ):
        super().__init__(
            "PredictionMarketOverlayBot",
            tickers=TICKERS,
            interval="1d",
            period="2y",
            signal=signal,
            rec_lo=rec_lo,
            rec_hi=rec_hi,
            hawk_bps=hawk_bps,
            hawk_lookback=hawk_lookback,
            equity_max=equity_max,
            gold=gold,
            rebalance_weekday=rebalance_weekday,
            **kwargs,
        )
        self.signal = signal
        self.rec_lo = rec_lo
        self.rec_hi = rec_hi
        self.hawk_bps = hawk_bps
        self.hawk_lookback = int(hawk_lookback)
        self.equity_max = equity_max
        self.gold = gold
        self.rebalance_weekday = rebalance_weekday

    def getYFDataWithTA(
        self,
        symbol: str | None = None,
        interval: str = "1m",
        period: str = "1d",
        saveToDB: bool = False,
        features: list[str] | None = None,
    ) -> pd.DataFrame:
        """Standard TA frame plus pm_* feature columns and an SMA200; live and backtest alike."""
        data = super().getYFDataWithTA(
            symbol=symbol, interval=interval, period=period, saveToDB=saveToDB, features=features
        )
        if data.empty:
            return data
        data = data.copy()
        feats = pmf.feature_frame(data["timestamp"], series=["recession", "fed_rate"])
        for col in FEATURES:
            values = feats[col].to_numpy() if col in feats else np.full(len(data), np.nan)
            data[f"pm_{col}"] = values
        data["sma200"] = data["close"].rolling(200).mean()
        return data

    def _hawk_change(self) -> float:
        """Rise in the ~6-month rate path over `hawk_lookback` bars, from QQQ's history up to this bar."""
        history = self.datas.get("QQQ") if getattr(self, "datas", None) else None
        if history is None or "pm_fed_path_bps" not in history or len(history) <= self.hawk_lookback:
            return np.nan
        path = history["pm_fed_path_bps"].to_numpy()
        return float(path[-1] - path[-1 - self.hawk_lookback])

    def risk_score(self, row: pd.Series) -> tuple[float, str] | None:
        """(r, driver) with driver 'recession' / 'hawk' / 'trend'; None when nothing is known."""
        if self.signal == "static":
            return 0.0, "static"
        if self.signal == "sma200":
            close, sma = row.get("close"), row.get("sma200")
            if pd.isna(close) or pd.isna(sma):
                return None
            return (1.0 if float(close) < float(sma) else 0.0), "trend"
        parts = {}
        rec = row.get("pm_recession_prob")
        if not pd.isna(rec):
            parts["recession"] = float(np.clip((rec - self.rec_lo) / max(self.rec_hi - self.rec_lo, 1e-9), 0, 1))
        hawk = self._hawk_change()
        if not np.isnan(hawk):
            parts["hawk"] = float(np.clip(hawk / self.hawk_bps, 0, 1))
        if not parts:
            return None
        driver = max(parts, key=parts.get)
        return parts[driver], driver

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        qqq = rows.get("QQQ")
        if qqq is None:
            return None
        if self.rebalance_weekday is not None and _bar_timestamp(qqq).weekday() != self.rebalance_weekday:
            return None
        scored = self.risk_score(qqq)
        if scored is None:
            logger.info("%s: no prediction-market feature on this bar — holding", self.bot_name)
            return None
        r, driver = scored
        equity = self.equity_max * (1 - r)
        freed = self.equity_max - equity
        bonds = 1.0 - self.equity_max - self.gold
        if driver in ("recession", "trend"):
            bonds += freed
        weights = {"SPY": equity / 2, "QQQ": equity / 2, "IEF": bonds, "GLD": self.gold}
        logger.info("%s: risk %.2f (%s) -> %s", self.bot_name, r, driver, {k: round(v, 3) for k, v in weights.items()})
        return {k: v for k, v in weights.items() if v > 1e-6}


if __name__ == "__main__":
    run_bot(PredictionMarketOverlayBot)
