"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-17-vol-target-trend
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_17_vol_target_trend.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly trend filter on the ETF8 universe: hold every name whose 50-day SMA is
above its 200-day SMA, equal-weighted and capped at 34% each. The resulting
book is then scaled so its trailing 63-day realised vol is about 10%
annualised.

Departures from source:
  * Vol-target leverage is capped at 1.0x (scale down only). The source allows
    up to 1.5x; this framework is long-only with gross <= 1.0.
  * Names with a 50-day SMA at or below the 200-day SMA are not held, and any
    capped or scaled-down excess stays in cash.

Schedule: none. Bots are not added to helm/values.yaml.
"""

import math
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame, equal_weight, period_rolled
from tradingbot.utils.runner import run_bot

FAST = 50
SLOW = 200
CAP = 0.34
TARGET_VOL = 0.10
VOL_LOOKBACK = 63
_TRADING_DAYS = 252


def compute_weights(
    closes: pd.DataFrame,
    fast: int = FAST,
    slow: int = SLOW,
    cap: float = CAP,
    target_vol: float = TARGET_VOL,
    vol_lookback: int = VOL_LOOKBACK,
) -> dict[str, float] | None:
    """Return vol-targeted trend weights on a month-first bar, else None.

    ``closes`` is a wide frame (index ascending, one column per ticker) holding
    history up to and including the current bar.

    Returns None when the last bar does not start a new calendar month, so the
    book is left untouched. Returns {} when there is not enough history or no
    ticker has its fast SMA above its slow SMA.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < slow:
        return {}

    sma_fast = closes.rolling(fast, min_periods=fast).mean().iloc[-1]
    sma_slow = closes.rolling(slow, min_periods=slow).mean().iloc[-1]
    active = [
        str(t) for t in closes.columns if pd.notna(sma_fast[t]) and pd.notna(sma_slow[t]) and sma_fast[t] > sma_slow[t]
    ]
    if not active:
        return {}

    base = equal_weight(active, cap)
    if not base:
        return {}

    returns = closes.pct_change()[active].iloc[-vol_lookback:]
    base_series = pd.Series(base, dtype=float)
    basket_returns = (returns * base_series).sum(axis=1)
    vol = float(basket_returns.std() * math.sqrt(_TRADING_DAYS))
    # Scale down only: the framework is long-only with gross <= 1.0.
    scale = min(1.0, target_vol / vol) if math.isfinite(vol) and vol > 0.0 else 1.0

    return {t: w * scale for t, w in base.items()}


class QuantsmdVolTargetTrendBot(Bot):
    """Monthly 50/200 SMA trend on ETF8, scaled down to a 10% vol target.

    Args:
        fast: Trading days in the fast SMA.
        slow: Trading days in the slow SMA; also the minimum history required.
        cap: Per-name weight cap, applied before vol scaling.
        target_vol: Annualised vol the basket is scaled down to when it runs hot.
        vol_lookback: Trading days of basket returns used for the vol estimate.
    """

    param_grid: ClassVar[dict] = {
        "fast": [20, 50, 100],
        "slow": [150, 200, 250],
        "target_vol": [0.08, 0.10, 0.12],
    }
    # A 200-day trend filter plus the vol window needs more than the framework's
    # default 1y backtest period to produce a meaningful number.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        cap: float = CAP,
        target_vol: float = TARGET_VOL,
        vol_lookback: int = VOL_LOOKBACK,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdVolTargetTrendBot",
            tickers=list(ETF8),
            interval="1d",
            # Live fetch must exceed the slow SMA window in param_grid (250).
            period="2y",
            fast=fast,
            slow=slow,
            cap=cap,
            target_vol=target_vol,
            vol_lookback=vol_lookback,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.cap = cap
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the vol-targeted trend book, or None off a month-first bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(
            closes,
            self.fast,
            self.slow,
            self.cap,
            self.target_vol,
            self.vol_lookback,
        )


if __name__ == "__main__":
    run_bot(QuantsmdVolTargetTrendBot)
