"""
TSMOMTrendBot — time-series momentum across sectors, long-or-cash, vol-targeted.

The signal is Moskowitz/Ooi/Pedersen (2012) verbatim: hold an instrument when
its trailing 12-month total return is positive, and size every held leg to a
common ex-ante volatility rather than equally. That is the trend sleeve every
CTA database screens for, and the one this book did not have.

**What departs from the paper, and it is not a detail.** MOP2012 goes SHORT the
negative-momentum legs and levers the book to a portfolio vol target. This
framework's portfolios are long-only and cannot exceed 1.0 gross (see
utils/weights.py, where a non-positive weight means "do not hold" rather than
"short"). So this bot is the LONG HALF of time-series momentum, unlevered:

  * A negative-momentum leg goes to cash, not short. The short book historically
    contributed a meaningful minority of TSMOM's return and most of its
    crisis-alpha convexity, so the equity curve here should be expected to look
    like a slower, less crisis-responsive version of the published series.
  * In calm markets the vol-targeted weights sum to well under 1 and the rest is
    cash; in stressed markets they are scaled back to fit. Absolute risk is
    therefore lower than the paper's and varies over time.

Never present a backtest of this bot as a replication of MOP2012. It is an
ETF-proxy long-only trend sleeve, and it should be labelled that way to anyone
who asks what it is.

Universe: liquid ETFs standing in for the futures sectors the paper trades —
equity, fixed income, commodities, and currencies versus the dollar. ETFs rather
than futures because the framework has no contract multipliers, no expiry rolls
and no futures data source; capacity is not the binding constraint at this size,
so the proxy costs little that matters here.

Schedule: 5 21 * * 1-5 — after the US close, before the 21:20 IBKR copier.
"""

import logging
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.vol_target import inverse_vol_weights, periods_per_year, realized_vol

logger = logging.getLogger(__name__)

# Sector buckets, kept explicit so the diversification claim is auditable rather
# than implied by a flat list. Every ETF here has daily history to at least 2007,
# which is what makes a backtest through 2008 possible.
EQUITY = ["SPY", "EFA", "EEM", "IWM"]
RATES = ["TLT", "IEF"]
COMMODITY = ["DBC", "GLD", "SLV", "USO", "DBA"]
CURRENCY = ["UUP", "FXE", "FXY", "FXB", "FXA", "FXF"]

UNIVERSE = [*EQUITY, *RATES, *COMMODITY, *CURRENCY]


class TSMOMTrendBot(Bot):
    """
    Long-or-cash time-series momentum, each leg sized to constant ex-ante vol.

    Args:
        lookback_days: Trading days in the momentum lookback. 252 is the paper's
            12 months. The signal is the sign of the total return over this
            window — `close` from yfinance is dividend- and split-adjusted, so it
            is a total-return series and the sign is the right one.
        vol_window: Trading days in the realized-vol estimate. Shorter reacts
            faster to a vol regime change but makes the book turn over on noise.
        target_vol: Annualized volatility each held leg contributes.
        max_leg: Per-leg weight cap. Binds on the quiet legs — short-dated rates
            and some currencies — which would otherwise dominate on inverse-vol
            sizing alone.
    """

    param_grid: ClassVar[dict] = {
        "lookback_days": [189, 252, 315],
        "vol_window": [40, 60, 90],
        "target_vol": [0.08, 0.10, 0.12],
    }

    # A 12-month lookback plus a vol window needs well over a year of history
    # before it can emit its first signal. The "1d" default of "1y" would leave
    # essentially no evaluable bars, and the backtest would report a confident
    # number computed from a handful of trades rather than failing outright.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        lookback_days: int = 252,
        vol_window: int = 60,
        target_vol: float = 0.10,
        max_leg: float = 0.15,
        **kwargs,
    ):
        super().__init__(
            "TSMOMTrendBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must comfortably exceed lookback + vol window, or the
            # bot silently holds nothing because no leg has enough history.
            period="2y",
            lookback_days=lookback_days,
            vol_window=vol_window,
            target_vol=target_vol,
            max_leg=max_leg,
            **kwargs,
        )
        self.lookback_days = lookback_days
        self.vol_window = vol_window
        self.target_vol = target_vol
        self.max_leg = max_leg

    def _momentum(self, closes: pd.Series) -> float | None:
        """
        Total return over the lookback window, or None if history is too short.

        None rather than 0.0: "no opinion yet" and "flat over 12 months" both
        mean don't hold, but only the first should be silent. Returning 0.0 here
        would make a warmup bar indistinguishable from a real signal.
        """
        if len(closes) < self.lookback_days + 1:
            return None
        past = float(closes.iloc[-1 - self.lookback_days])
        now = float(closes.iloc[-1])
        if past <= 0:
            return None
        return now / past - 1.0

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """
        Hold every positive-momentum leg, weighted inversely to its volatility.

        Legs still in warmup are absent from `vols` and therefore unfunded, which
        is the correct behaviour on both paths: early backtest bars trade nothing
        rather than trading on a truncated lookback.
        """
        ppy = periods_per_year(self.interval)

        vols: dict[str, float] = {}
        for ticker in self.tradeable_tickers:
            frame = self.datas.get(ticker)
            if frame is None or frame.empty or "close" not in frame:
                continue
            closes = frame["close"].dropna()
            momentum = self._momentum(closes)
            if momentum is None or momentum <= 0:
                continue
            vol = realized_vol(closes, window=self.vol_window, periods_per_year=ppy)
            if vol > 0:
                vols[ticker] = vol

        if not vols:
            logger.info("%s: no leg has positive 12m momentum — holding cash", self.bot_name)
            return {}

        weights = inverse_vol_weights(
            vols,
            target_vol=self.target_vol,
            max_leg=self.max_leg,
            max_gross=1.0,
        )
        logger.info(
            "%s: %d/%d legs long, gross %.1f%%",
            self.bot_name,
            len(weights),
            len(self.tradeable_tickers),
            sum(weights.values()) * 100,
        )
        return weights


if __name__ == "__main__":
    # bot = TSMOMTrendBot()
    # bot.local_development(objective="sharpe_ratio", param_sample_ratio=0.3)
    run_bot(TSMOMTrendBot)
