"""
InstitutionalFlowBot — hold the S&P 100 names that look like they are being
accumulated by institutions, rebalanced weekly.

Each Friday after the close:

1. **Market gate.** If QQQ is below its 200-day MA, hold cash (the live copier
   parks it in SHV). Many trend mandates cannot buy below the 200-day, and
   neither does this bot.
2. **Mandate filter.** Keep only stocks an institution could and would buy:
   20-day average dollar volume >= $50M, price >= $10, and a confirmed uptrend
   (close > SMA50 > SMA200, SMA200 rising). See utils/institutional_ta.py.
3. **Accumulation score.** Rank the survivors on a z-scored composite of
   relative strength, up/down volume, Chaikin Money Flow, OBV slope, IBD-style
   accumulation days, distance above the quarter-anchored VWAP, and OBV-vs-price
   divergence.
4. **Book.** Hold the top `top_n` at 1/top_n each. When fewer names pass the
   filter the remainder stays in cash, so exposure falls with market breadth —
   the main thing keeping this from being a beta-1 large-cap basket.

Weekly, not daily: every input is a 20- to 252-bar window, so the ranking barely
moves day to day, and re-ranking daily mostly churns the names at the cut-off
through slippage and the no-trade band. The backtest compares both
(rebalance_weekday=None rebalances every bar); see
docs/backtests/institutionalflowbot.md.

Daily bars carry no MOC, dark-pool, ETF-flow, 13F or options-flow data, so
those institutional footprints are not used. Valuation fundamentals are
captured daily into `stock_fundamentals` (tradingbot/fundamentalssnapshot.py)
but are not an input yet: they have no history before capture started, so
nothing using them could be backtested.

Schedule: 15 21 * * 5 — Friday after the US close. On a holiday Friday the
newest bar is Thursday's, the weekday gate returns None, and the week is skipped
identically live and in the backtest.
"""

import logging
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.institutional_ta import accumulation_score, add_institutional_features, passes_mandate
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import SP100

logger = logging.getLogger(__name__)

BENCHMARK = "QQQ"


def _bar_timestamp(row: pd.Series) -> pd.Timestamp:
    """The bar's time: a `timestamp` column live, the index label in the backtest."""
    if "timestamp" in row.index:
        return pd.Timestamp(row["timestamp"])
    return pd.Timestamp(row.name)


class InstitutionalFlowBot(Bot):
    """
    Weekly top-N of S&P 100 stocks passing institutional mandate filters,
    ranked by volume-based accumulation.

    Args:
        top_n: Number of names held; each gets 1/top_n of the book.
        rebalance_weekday: Weekday (Mon=0) of the bar on which to re-rank.
            None re-ranks on every bar (the daily variant).
        market_gate: Hold cash while QQQ is below its 200-day MA.
        min_adv_usd: Minimum 20-day average dollar volume.
        min_price: Minimum share price.
    """

    param_grid: ClassVar[dict] = {
        "top_n": [5, 10, 20],
        "rebalance_weekday": [4, None],
        "market_gate": [True, False],
    }

    # 252-bar relative strength plus SMA200 warmup: "1y" would leave nothing.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    # ~100 stocks: one yfinance failure must not cancel the week. Missing names
    # sit out and any holding in them is pinned (see Bot.MIN_UNIVERSE_COVERAGE).
    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.95

    def __init__(
        self,
        top_n: int = 10,
        rebalance_weekday: int | None = 4,
        market_gate: bool = True,
        min_adv_usd: float = 50e6,
        min_price: float = 10.0,
        **kwargs,
    ):
        super().__init__(
            "InstitutionalFlowBot",
            tickers=[*SP100, BENCHMARK],
            benchmark_tickers=[BENCHMARK],
            interval="1d",
            period="2y",
            top_n=top_n,
            rebalance_weekday=rebalance_weekday,
            market_gate=market_gate,
            min_adv_usd=min_adv_usd,
            min_price=min_price,
            **kwargs,
        )
        self.top_n = top_n
        self.rebalance_weekday = rebalance_weekday
        self.market_gate = market_gate
        self.min_adv_usd = min_adv_usd
        self.min_price = min_price

    def getYFDataWithTA(
        self,
        symbol: str | None = None,
        interval: str = "1m",
        period: str = "1d",
        saveToDB: bool = False,
        features: list[str] | None = None,
    ) -> pd.DataFrame:
        """Standard TA frame plus the inst_* columns; used by live and backtest alike."""
        data = super().getYFDataWithTA(
            symbol=symbol, interval=interval, period=period, saveToDB=saveToDB, features=features
        )
        return add_institutional_features(data)

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        bench = rows.get(BENCHMARK)
        if bench is None:
            return None

        if self.rebalance_weekday is not None:
            bar_time = _bar_timestamp(bench)
            if bar_time.weekday() != self.rebalance_weekday:
                logger.info("%s: bar %s is not a rebalance day — holding", self.bot_name, bar_time.date())
                return None

        if self.market_gate:
            close, sma200 = bench.get("close"), bench.get("inst_sma200")
            if pd.isna(sma200) or pd.isna(close) or float(close) < float(sma200):
                logger.info("%s: %s below its 200-day MA (or warming up) — all cash", self.bot_name, BENCHMARK)
                return {}

        eligible = {
            t: rows[t]
            for t in self.tradeable_tickers
            if t in rows and passes_mandate(rows[t], min_adv_usd=self.min_adv_usd, min_price=self.min_price)
        }
        if not eligible:
            logger.info("%s: no stock passes the mandate filter — all cash", self.bot_name)
            return {}

        scores = accumulation_score(eligible)
        picks = sorted(scores, key=lambda t: scores[t], reverse=True)[: self.top_n]
        logger.info(
            "%s: %d/%d pass the mandate; holding %s",
            self.bot_name,
            len(eligible),
            len(self.tradeable_tickers),
            {t: round(scores[t], 2) for t in picks},
        )
        return dict.fromkeys(picks, 1.0 / self.top_n)


if __name__ == "__main__":
    run_bot(InstitutionalFlowBot)
