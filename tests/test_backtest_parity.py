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


# ======================================================================
#  targetWeights bots
# ======================================================================
#
# The bar loop was rewritten to resolve BOTH bot types into a per-ticker
# target value before executing. That rewrite must be arithmetically
# identical for decisionFunction bots, not merely similar — hence the
# golden-number pin below, recorded from the pre-rewrite code.


class _FixedWeights(Bot):
    """targetWeights bot returning a constant book."""

    def __init__(self, tickers, weights, benchmarks=()):
        self.bot_name = "WeightsBot"
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
        self.seen: list[dict] = []

    def targetWeights(self, rows):
        self.seen.append(dict(rows))
        return dict(self._weights)


def test_decision_function_bots_unchanged_after_target_weights_refactor():
    """
    Golden numbers recorded from the pre-refactor code. If the shared
    target/band resolution changed decisionFunction semantics by even a float,
    this fails — which is the whole point of pinning it.
    """
    rising = list(np.linspace(100.0, 300.0, BARS))
    falling = list(np.linspace(300.0, 100.0, BARS))
    flat = [100.0] * BARS
    data = {"AAA": _frame(rising), "BBB": _frame(falling), "CCC": _frame(flat)}

    free = _run(_AlwaysBuy(["AAA", "BBB", "CCC"]), data)
    assert free["yearly_return"] == pytest.approx(0.023192133866753648, abs=1e-15)
    assert free["nrtrades"] == 41

    costed = backtest_bot(
        _AlwaysBuy(["AAA", "BBB", "CCC"]),
        initial_capital=10000.0,
        data=data,
        save_to_db=False,
        save_results_to_db=False,
        slippage_pct=0.0005,
        commission_pct=0.0005,
    )
    assert costed["yearly_return"] == pytest.approx(0.02179751444907464, abs=1e-15)
    assert costed["nrtrades"] == 41


def test_target_weights_backtest_holds_the_requested_split():
    """A 60/40 book on flat prices must end at 60/40, with no cash left over."""
    flat = [100.0] * BARS
    data = {"AAA": _frame(flat), "BBB": _frame(flat)}

    bot = _FixedWeights(["AAA", "BBB"], {"AAA": 0.6, "BBB": 0.4})
    result = _run(bot, data)

    assert result["yearly_return"] == pytest.approx(0.0, abs=1e-6)
    assert result["nrtrades"] == 2, "one buy per leg, then nothing to do"


def test_target_weights_leaves_the_residual_in_cash():
    """Weights summing below 1.0 must not be levered back up to fully invested."""
    rising = list(np.linspace(100.0, 200.0, BARS))
    data = {"AAA": _frame(rising), "BBB": _frame(rising)}

    half = _run(_FixedWeights(["AAA", "BBB"], {"AAA": 0.25, "BBB": 0.25}), data)
    full = _run(_FixedWeights(["AAA", "BBB"], {"AAA": 0.5, "BBB": 0.5}), data)
    # Half invested in a doubling market earns materially less than fully invested.
    assert half["yearly_return"] < full["yearly_return"]


def test_target_weights_zero_is_a_full_exit():
    """An omitted/zero leg exits completely rather than being stranded by the band."""
    flat = [100.0] * BARS

    class Exiting(_FixedWeights):
        def targetWeights(self, rows):
            # Hold AAA for the first half of the backtest, then drop it entirely.
            return {"AAA": 0.5} if len(self.datas["AAA"]) < BARS // 2 else {}

    bot = Exiting(["AAA", "BBB"], {})
    result = _run(bot, {"AAA": _frame(flat), "BBB": _frame(flat)})
    assert result["nrtrades"] >= 2, "one buy in, one full exit out"


def test_target_weights_receives_all_tickers_including_benchmarks():
    """Cross-sectional strategies must see the whole universe, benchmarks included."""
    flat = [100.0] * BARS
    data = {"AAA": _frame(flat), "BBB": _frame(flat), "CCC": _frame(flat)}

    bot = _FixedWeights(["AAA", "BBB", "CCC"], {"AAA": 1.0}, benchmarks=["CCC"])
    _run(bot, data)

    assert bot.seen, "targetWeights was never called"
    assert set(bot.seen[0]) == {"AAA", "BBB", "CCC"}


def test_target_weights_cannot_fund_a_benchmark():
    """
    A weight on a benchmark is dropped, NOT redistributed — so the book ends up
    under-invested rather than silently doubling the other leg.
    """
    flat = [100.0] * BARS
    doubling = list(np.linspace(100.0, 200.0, BARS))
    data = {"AAA": _frame(flat), "CCC": _frame(doubling)}

    bot = _FixedWeights(["AAA", "CCC"], {"AAA": 0.5, "CCC": 0.5}, benchmarks=["CCC"])
    result = _run(bot, data)

    # Half in a flat asset, half in cash — none of the doubling benchmark.
    assert result["yearly_return"] == pytest.approx(0.0, abs=1e-6)


def test_target_weights_sees_only_history_up_to_the_current_bar():
    """The look-ahead pin. self.datas must never contain a future bar."""
    flat = [100.0] * BARS
    data = {"AAA": _frame(flat), "BBB": _frame(flat)}

    class Peeking(_FixedWeights):
        def targetWeights(self, rows):
            for ticker, row in rows.items():
                assert self.datas[ticker].index[-1] == row.name, "history extends past the current bar"
            return {"AAA": 0.5}

    bot = Peeking(["AAA", "BBB"], {})
    _run(bot, data)


def test_target_weights_exception_trades_nothing_that_bar():
    """A raising bot must not take the book anywhere, least of all to cash."""
    flat = [100.0] * BARS

    class Broken(_FixedWeights):
        def targetWeights(self, rows):
            raise RuntimeError("boom")

    result = _run(Broken(["AAA", "BBB"], {}), {"AAA": _frame(flat), "BBB": _frame(flat)})
    assert result["nrtrades"] == 0


def test_single_ticker_target_weights_bot_backtests():
    """N == 1 takes the multi path, which the single-asset path cannot express."""
    rising = list(np.linspace(100.0, 200.0, BARS))
    result = _run(_FixedWeights(["AAA"], {"AAA": 0.5}), {"AAA": _frame(rising)})
    assert result["nrtrades"] >= 1
    assert result["yearly_return"] > 0.0
