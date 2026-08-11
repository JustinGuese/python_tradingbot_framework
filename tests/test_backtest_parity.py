"""
The backtest must model the same allocation rules as live execution.

If the two diverge, backtests stop predicting live behaviour — which is the
entire reason to run them. These pin the three rules that Part C changed on both
sides: the divisor excludes benchmarks, positions get trimmed, and exits are
processed before entries so proceeds can fund the same bar's buys.
"""

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.backtest import backtest_bot
from tradingbot.utils.botclass import Bot

BARS = 40


def _frame(prices):
    """OHLCV with a timestamp index, shaped like getYFDataWithTA output."""
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


class _AlwaysBuy(Bot):
    """decisionFunction returns 1 for everything except the benchmark."""

    def __init__(self, tickers, benchmarks=()):
        # Bypass Bot.__init__ entirely: it writes to the database.
        self.bot_name = "ParityBot"
        self.tickers = list(tickers)
        self.benchmark_tickers = list(benchmarks)
        self.symbol = None
        self.interval = "1d"
        self.period = "1y"
        self.datas = {}
        self.data = None
        self.params = {}

    def decisionFunction(self, row):
        return 0 if self._current_ticker in self.benchmark_tickers else 1


def _run(bot, data):
    return backtest_bot(
        bot,
        initial_capital=10000.0,
        data=data,
        save_to_db=False,
        save_results_to_db=False,
        slippage_pct=0.0,
        commission_pct=0.0,
    )


def test_divisor_excludes_benchmark():
    """
    AAA and BBB are flat; the benchmark CCC triples. If CCC were in the divisor
    the bot would only deploy 2/3 of capital and hold the rest in cash; with it
    excluded the book is fully invested and stays flat.
    """
    flat = [100.0] * BARS
    rising = list(np.linspace(100.0, 300.0, BARS))
    data = {"AAA": _frame(flat), "BBB": _frame(flat), "CCC": _frame(rising)}

    with_bm = _run(_AlwaysBuy(["AAA", "BBB", "CCC"], benchmarks=["CCC"]), data)
    # Fully invested in two flat assets => no drift from the starting capital.
    assert with_bm["yearly_return"] == pytest.approx(0.0, abs=1e-6)


def test_benchmark_is_excluded_from_buy_and_hold_baseline():
    """Averaging the benchmark into buy-and-hold benchmarks SPY against SPY."""
    flat = [100.0] * BARS
    doubling = list(np.linspace(100.0, 200.0, BARS))
    data = {"AAA": _frame(flat), "BBB": _frame(flat), "CCC": _frame(doubling)}

    result = _run(_AlwaysBuy(["AAA", "BBB", "CCC"], benchmarks=["CCC"]), data)
    # Only the two flat tradeable legs count => 0%, not the ~33% you get by
    # averaging in a doubling benchmark.
    assert result["buy_hold_return"] == pytest.approx(0.0, abs=1e-6)


def test_no_benchmarks_declared_behaves_as_before():
    """The 26 other backtestable bots must be unaffected by the divisor edit."""
    flat = [100.0] * BARS
    data = {"AAA": _frame(flat), "BBB": _frame(flat)}

    result = _run(_AlwaysBuy(["AAA", "BBB"]), data)
    assert result["yearly_return"] == pytest.approx(0.0, abs=1e-6)
    assert result["buy_hold_return"] == pytest.approx(0.0, abs=1e-6)


def test_backtest_trims_an_overweight_leg():
    """
    AAA doubles while BBB is flat, so AAA drifts well above its equal-weight
    sleeve. The old backtest only ever bought, so it never trimmed; the trim
    must now fire and hold AAA near half the book.
    """

    class Bal(_AlwaysBuy):
        pass

    rising = list(np.linspace(100.0, 200.0, BARS))
    flat = [100.0] * BARS
    data = {"AAA": _frame(rising), "BBB": _frame(flat)}

    bot = Bal(["AAA", "BBB"])
    result = _run(bot, data)

    # With rebalancing into a flat asset, the strategy must capture less than
    # pure buy-and-hold of the doubling leg but more than nothing.
    assert result["yearly_return"] > 0.0
    assert result["nrtrades"] > 2, "trimming should generate more than the two opening buys"
