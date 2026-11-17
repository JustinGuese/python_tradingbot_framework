"""
Unit and regression tests for short selling parity in backtest_bot (PLAN.md S1c).
"""

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.backtest import backtest_bot
from tradingbot.utils.botclass import Bot

BARS = 40


def _frame(prices):
    """OHLCV DataFrame with timestamp index and TA indicators."""
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="D")
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1_000.0] * len(prices),
            "trend_adx": [25.0] * len(prices),
        },
        index=idx,
    )


class _ShortWeightsBot(Bot):
    """Bot for testing short selling with targetWeights."""

    ALLOW_SHORT = True
    MAX_GROSS = 1.0
    SHORTABLE = None

    def __init__(self, tickers, weights, benchmarks=()):
        self.bot_name = "ShortWeightsBot"
        self.tickers = list(tickers)
        self.benchmark_tickers = list(benchmarks)
        self.symbol = None
        self.interval = "1d"
        self.period = "1y"
        self.datas = {}
        self.data = None
        self.params = {}
        self.LIQUIDATE_UNTRACKED = False
        self._weights = dict(weights)
        self.rebalance_count = 0

    def targetWeights(self, rows):
        self.rebalance_count += 1
        return dict(self._weights)


class _RebalanceOnceShortBot(_ShortWeightsBot):
    """Rebalances once on the first bar, then holds (returns None)."""

    def targetWeights(self, rows):
        self.rebalance_count += 1
        return dict(self._weights) if self.rebalance_count == 1 else None


def _run(bot, data, **kwargs):
    opts = {
        "initial_capital": 10000.0,
        "data": data,
        "save_to_db": False,
        "save_results_to_db": False,
        "slippage_pct": 0.0,
        "commission_pct": 0.0,
    }
    opts.update(kwargs)
    return backtest_bot(bot, **opts)


def test_short_on_falling_series_profits_net_of_borrow():
    """
    Shorting a falling stock profits, with borrow fees correctly deducted.
    Price falls from 100 to 50: gross profit is ~50%, net profit is positive
    and lower than 50% due to borrow fees.
    """
    falling = list(np.linspace(100.0, 50.0, BARS))
    data = {"QQQ": _frame(falling)}

    bot = _RebalanceOnceShortBot(["QQQ"], {"QQQ": -1.0})
    result = _run(bot, data, return_series=True)

    # 1. Profits net of borrow
    assert result["yearly_return"] > 0.0
    # Gross profit without borrow would be exactly 50% ($5,000 / $10,000)
    assert result["yearly_return"] < 0.50
    assert result["yearly_return"] == pytest.approx(0.498, abs=0.01)

    # 2. Exactly one short trade
    assert result["nrtrades"] == 1
    trades = result["trades"]
    assert len(trades) == 1
    assert trades[0]["side"] == "short"
    assert trades[0]["symbol"] == "QQQ"


def test_short_on_rising_series_triggers_force_cover_not_negative_equity():
    """
    When shorting a stock that triples (100 -> 300), maintenance breach occurs
    and triggers force-covering before equity can turn negative.
    """
    rising = list(np.linspace(100.0, 300.0, BARS))
    data = {"QQQ": _frame(rising)}

    bot = _RebalanceOnceShortBot(["QQQ"], {"QQQ": -1.0})
    result = _run(bot, data, return_series=True)

    # Without force-cover, equity would plunge to -$10,000 (yearly_return = -2.0)
    # With force-cover, equity is preserved above zero.
    assert result["yearly_return"] > -1.0
    for pt in result["equity_curve"]:
        assert pt["v"] > 0.0, f"Equity must stay positive, got {pt['v']}"

    # Force-cover generates an extra trade with side == "cover"
    assert result["nrtrades"] >= 2
    sides = [t["side"] for t in result["trades"]]
    assert "short" in sides
    assert "cover" in sides


def test_long_only_result_identical_to_before():
    """
    Long-only bots remain byte-identical with the short selling additions in place.
    """
    rising = list(np.linspace(100.0, 300.0, BARS))
    falling = list(np.linspace(300.0, 100.0, BARS))
    flat = [100.0] * BARS
    data = {"AAA": _frame(rising), "BBB": _frame(falling), "CCC": _frame(flat)}

    class _AlwaysBuy(Bot):
        def __init__(self, tickers):
            self.bot_name = "ParityBot"
            self.tickers = list(tickers)
            self.benchmark_tickers = []
            self.symbol = None
            self.interval = "1d"
            self.period = "1y"
            self.datas = {}
            self.data = None
            self.params = {}

        def decisionFunction(self, row):
            return 1

    free = _run(_AlwaysBuy(["AAA", "BBB", "CCC"]), data)
    assert free["yearly_return"] == pytest.approx(0.023192133866753648, abs=1e-15)
    assert free["nrtrades"] == 41


def test_alpha_metrics_report_lower_beta_for_hedged_book():
    """
    A market-neutral / hedged book (long AAA + short QQQ) has a lower beta
    against QQQ than an unhedged long book.
    """
    # QQQ moves up and down
    qqq_prices = [100.0 + 10.0 * np.sin(i / 3.0) + (i * 0.5) for i in range(BARS)]
    # AAA is correlated with QQQ
    aaa_prices = [p * 1.1 + 5.0 for p in qqq_prices]

    data = {"AAA": _frame(aaa_prices), "QQQ": _frame(qqq_prices)}

    # Unhedged: 100% long AAA (QQQ in universe but unweighted)
    unhedged_bot = _ShortWeightsBot(["AAA", "QQQ"], {"AAA": 1.0, "QQQ": 0.0})
    unhedged_bot.ALLOW_SHORT = False
    unhedged_res = _run(unhedged_bot, data)

    # Hedged: 100% long AAA, 80% short QQQ
    hedged_bot = _ShortWeightsBot(["AAA", "QQQ"], {"AAA": 1.0, "QQQ": -0.8})
    hedged_bot.ALLOW_SHORT = True
    hedged_bot.MAX_GROSS = 2.0
    hedged_bot.SHORTABLE = ("QQQ",)
    hedged_res = _run(hedged_bot, data)

    assert unhedged_res["beta"] is not None
    assert hedged_res["beta"] is not None
    assert hedged_res["beta"] < unhedged_res["beta"]


def test_short_refused_for_non_shortable_symbol():
    """
    Targeting a short position in a non-shortable symbol is refused.
    """
    flat = [100.0] * BARS
    data = {"NON_SHORTABLE": _frame(flat)}

    bot = _ShortWeightsBot(["NON_SHORTABLE"], {"NON_SHORTABLE": -0.5})
    bot.ALLOW_SHORT = True
    bot.SHORTABLE = ("QQQ", "SPY")  # NON_SHORTABLE not included

    result = _run(bot, data)
    assert result["nrtrades"] == 0
    assert result["yearly_return"] == pytest.approx(0.0, abs=1e-6)


def test_reg_t_initial_margin_breach_refuses_expansion():
    """
    Target portfolio breaching Reg T initial margin (equity < 0.5*long + 0.5*short)
    fails initial_ok and is refused whole.
    """
    flat = [100.0] * BARS
    data = {"AAA": _frame(flat), "QQQ": _frame(flat)}

    # 1.5 long + 1.0 short => Reg T requires 0.5*1.5 + 0.5*1.0 = 1.25 > 1.0 equity
    bot = _ShortWeightsBot(["AAA", "QQQ"], {"AAA": 1.5, "QQQ": -1.0})
    bot.ALLOW_SHORT = True
    bot.MAX_GROSS = 3.0  # Even though gross 2.5 <= 3.0, Reg T fails
    bot.SHORTABLE = ("QQQ",)

    result = _run(bot, data)
    assert result["nrtrades"] == 0
    assert result["yearly_return"] == pytest.approx(0.0, abs=1e-6)
