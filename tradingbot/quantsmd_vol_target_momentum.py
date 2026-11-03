"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-18-vol-target-momentum
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_18_vol_target_momentum.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly cross-sectional momentum on the ETF8 universe: hold the top 3 names by
126-day total return among those with positive momentum, equal-weighted and
capped at 40% each. The resulting book is then scaled so its trailing 42-day
realised vol is about 15% annualised.

Departures from source:
  * Vol-target leverage is capped at 1.0x (scale down only). The source allows
    up to 2.0x; this framework is long-only with gross <= 1.0.
  * Names with non-positive momentum are not held, and any capped or scaled-down
    excess stays in cash.

Schedule: none. Bots are not added to helm/values.yaml.
"""

import math
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame, equal_weight, period_rolled
from tradingbot.utils.runner import run_bot

LOOKBACK = 126
TOP_N = 3
CAP = 0.40
TARGET_VOL = 0.15
VOL_LOOKBACK = 42
_TRADING_DAYS = 252


def compute_weights(
    closes: pd.DataFrame,
    lookback: int = LOOKBACK,
    top_n: int = TOP_N,
    cap: float = CAP,
    target_vol: float = TARGET_VOL,
    vol_lookback: int = VOL_LOOKBACK,
) -> dict[str, float] | None:
    """Return vol-targeted momentum weights on a month-first bar, else None.

    ``closes`` is a wide frame (index ascending, one column per ticker) holding
    history up to and including the current bar.

    Returns None when the last bar does not start a new calendar month, so the
    book is left untouched. Returns {} when there is not enough history or no
    ticker has positive momentum.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < lookback + 1:
        return {}

    past = closes.iloc[-1 - lookback]
    now = closes.iloc[-1]
    valid = (past > 0) & now.notna() & past.notna()
    momentum = (now[valid] / past[valid] - 1.0).dropna()
    positive = momentum[momentum > 0.0]
    if positive.empty:
        return {}
    picks = [str(t) for t in positive.nlargest(top_n).index]

    base = equal_weight(picks, cap)
    if not base:
        return {}

    returns = closes.pct_change()[picks].iloc[-vol_lookback:]
    base_series = pd.Series(base, dtype=float)
    basket_returns = (returns * base_series).sum(axis=1)
    vol = float(basket_returns.std() * math.sqrt(_TRADING_DAYS))
    # Scale down only: the framework is long-only with gross <= 1.0.
    scale = min(1.0, target_vol / vol) if math.isfinite(vol) and vol > 0.0 else 1.0

    return {t: w * scale for t, w in base.items()}


class QuantsmdVolTargetMomentumBot(Bot):
    """Monthly top-N momentum on ETF8, scaled down to a 15% vol target.

    Args:
        lookback: Trading days in the momentum window (126 is about six months).
        top_n: Number of names to hold among those with positive momentum.
        cap: Per-name weight cap, applied before vol scaling.
        target_vol: Annualised vol the basket is scaled down to when it runs hot.
        vol_lookback: Trading days of basket returns used for the vol estimate.
    """

    param_grid: ClassVar[dict] = {
        "lookback": [63, 126, 189],
        "top_n": [2, 3, 4],
        "target_vol": [0.10, 0.15],
    }
    # A 126-day lookback plus the vol window needs more than the framework's
    # default 1y backtest period to produce a meaningful number.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        lookback: int = LOOKBACK,
        top_n: int = TOP_N,
        cap: float = CAP,
        target_vol: float = TARGET_VOL,
        vol_lookback: int = VOL_LOOKBACK,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdVolTargetMomentumBot",
            tickers=list(ETF8),
            interval="1d",
            # Live fetch must exceed the longest lookback in param_grid (189).
            period="2y",
            lookback=lookback,
            top_n=top_n,
            cap=cap,
            target_vol=target_vol,
            vol_lookback=vol_lookback,
            **kwargs,
        )
        self.lookback = lookback
        self.top_n = top_n
        self.cap = cap
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the vol-targeted momentum book, or None off a month-first bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(
            closes,
            self.lookback,
            self.top_n,
            self.cap,
            self.target_vol,
            self.vol_lookback,
        )


if __name__ == "__main__":
    run_bot(QuantsmdVolTargetMomentumBot)
