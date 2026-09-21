"""
`backtest_bot(return_series=True)` must expose what a chart needs.

The equity curve and the trade list were computed and discarded before this, so
nothing downstream could draw a backtest. These pin the contract the API and the
Flutter charts depend on: the two curves share timestamps (so the lines align),
the trade log agrees with `nrtrades`, and — most importantly — the default
`return_series=False` still returns exactly the nine scalars every existing bot
and the `backtest_results` table expect.
"""

import pandas as pd
import pytest

from tradingbot.utils.backtest import ALPHA_KEYS, backtest_bot
from tradingbot.utils.botclass import Bot

BARS = 40

# The nine keys backtest_bot returned before return_series existed.
SCALAR_KEYS = {
    "yearly_return",
    "sharpe_ratio",
    "maxdrawdown",
    "sortino_ratio",
    "calmar_ratio",
    "win_rate",
    "volatility",
    "nrtrades",
    "buy_hold_return",
}
# Plus the alpha metrics, added later. Additive: the persisted row still takes
# only the nine above, and callers that index by name are unaffected.
RESULT_KEYS = SCALAR_KEYS | set(ALPHA_KEYS)


def _frame(prices, signals=None):
    """OHLCV shaped like getYFDataWithTA output, with a `timestamp` column.

    The single-ticker path requires `close` and `timestamp` as columns (not an
    index), unlike the multi-ticker path which accepts either.
    """
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="D", tz="UTC")
    frame = pd.DataFrame(
        {
            "timestamp": idx,
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1_000.0] * len(prices),
            # Non-zero everywhere: 0.0 is the TA-warmup sentinel the backtest skips.
            "trend_adx": [25.0] * len(prices),
        }
    )
    if signals is not None:
        frame["signal"] = signals
    return frame


class _SignalBot(Bot):
    """Reads its decision straight off a `signal` column, so tests control trades."""

    def __init__(self, tickers):
        # Bypass Bot.__init__ entirely: it runs DDL and inserts a portfolio row.
        self.bot_name = "SeriesBot"
        self.tickers = list(tickers)
        self.benchmark_tickers = []
        self.symbol = None
        self.interval = "1d"
        self.period = "1y"
        self.datas = {}
        self.data = None
        self.params = {}

    def decisionFunction(self, row):
        return int(row.get("signal", 0))


def _run(bot, data, **kwargs):
    return backtest_bot(
        bot,
        initial_capital=10000.0,
        data=data,
        save_to_db=False,
        save_results_to_db=False,
        slippage_pct=0.0,
        commission_pct=0.0,
        **kwargs,
    )


def _alternating_signals(n):
    """Buy for 5 bars, sell for 5 bars, repeat — several round trips."""
    return [1 if (i // 5) % 2 == 0 else -1 for i in range(n)]


# --------------------------------------------------------------------------- #
#  Backwards compatibility                                                     #
# --------------------------------------------------------------------------- #


def test_default_returns_only_the_original_scalars():
    """The 24 production bots must see no change at all."""
    data = _frame([100.0] * BARS, _alternating_signals(BARS))
    result = _run(_SignalBot(["AAA"]), data)

    assert set(result) == RESULT_KEYS
    assert "equity_curve" not in result


def test_default_multi_ticker_returns_only_the_original_scalars():
    prices = [100.0] * BARS
    signals = _alternating_signals(BARS)
    data = {"AAA": _frame(prices, signals), "BBB": _frame(prices, signals)}
    result = _run(_SignalBot(["AAA", "BBB"]), data)

    assert set(result) == RESULT_KEYS


# --------------------------------------------------------------------------- #
#  Single-ticker series                                                        #
# --------------------------------------------------------------------------- #


def test_single_ticker_series_shape():
    data = _frame([100.0] * BARS, _alternating_signals(BARS))
    result = _run(_SignalBot(["AAA"]), data, return_series=True)

    assert SCALAR_KEYS.issubset(set(result))
    equity = result["equity_curve"]
    benchmark = result["buy_hold_curve"]

    assert len(equity) == BARS
    # Same length AND same timestamps: the chart draws both against one x-axis.
    assert [p["t"] for p in equity] == [p["t"] for p in benchmark]
    assert all(isinstance(p["t"], str) for p in equity)
    assert equity[0]["t"].startswith("2026-01-01")


def test_single_ticker_trade_log_matches_nrtrades():
    data = _frame([100.0] * BARS, _alternating_signals(BARS))
    result = _run(_SignalBot(["AAA"]), data, return_series=True)

    trades = result["trades"]
    assert len(trades) == result["nrtrades"]
    assert result["nrtrades"] > 0, "alternating signals must produce round trips"

    # All-in / full-exit means the sides must strictly alternate, starting with a buy.
    assert [t["side"] for t in trades] == ["buy", "sell"] * (len(trades) // 2)
    for trade in trades:
        assert trade["symbol"] == "AAA"
        assert trade["qty"] > 0
        assert trade["price"] > 0


def test_buy_and_hold_curve_is_rebased_to_initial_capital():
    """Both lines must start at the same point or the chart is misleading."""
    rising = list(pd.Series(range(BARS), dtype=float) + 100.0)
    data = _frame(rising, [0] * BARS)
    result = _run(_SignalBot(["AAA"]), data, return_series=True)

    benchmark = result["buy_hold_curve"]
    assert benchmark[0]["v"] == pytest.approx(10000.0)
    # Never traded, so equity stays flat at the starting capital.
    assert result["equity_curve"][0]["v"] == pytest.approx(10000.0)
    assert result["equity_curve"][-1]["v"] == pytest.approx(10000.0)
    # ...while buy-and-hold tracks the price.
    assert benchmark[-1]["v"] == pytest.approx(10000.0 * rising[-1] / rising[0])


def test_equity_curve_tracks_the_price_while_holding():
    """A permanent long must match buy-and-hold once it is filled."""
    rising = list(pd.Series(range(BARS), dtype=float) + 100.0)
    data = _frame(rising, [1] * BARS)
    result = _run(_SignalBot(["AAA"]), data, return_series=True)

    equity = result["equity_curve"]
    assert result["nrtrades"] == 1, "one entry, never exited"
    # Bar 0 buys at that bar's close, so from bar 1 on the two curves coincide.
    for point, benchmark_point in zip(equity[1:], result["buy_hold_curve"][1:], strict=True):
        assert point["v"] == pytest.approx(benchmark_point["v"])


def test_warmup_bars_are_excluded_from_the_curve():
    """trend_adx == 0.0 marks TA warmup; those bars never reach the curve."""
    data = _frame([100.0] * BARS, [0] * BARS)
    data.loc[:9, "trend_adx"] = 0.0

    result = _run(_SignalBot(["AAA"]), data, return_series=True)

    assert len(result["equity_curve"]) == BARS - 10
    assert result["equity_curve"][0]["t"].startswith("2026-01-11")


# --------------------------------------------------------------------------- #
#  Multi-ticker series                                                         #
# --------------------------------------------------------------------------- #


def test_multi_ticker_series_shape_and_trade_log():
    prices = [100.0] * BARS
    signals = _alternating_signals(BARS)
    data = {"AAA": _frame(prices, signals), "BBB": _frame(prices, signals)}

    result = _run(_SignalBot(["AAA", "BBB"]), data, return_series=True)

    equity = result["equity_curve"]
    assert len(equity) == BARS
    assert [p["t"] for p in equity] == [p["t"] for p in result["buy_hold_curve"]]
    assert result["buy_hold_curve"][0]["v"] == pytest.approx(10000.0)

    trades = result["trades"]
    assert len(trades) == result["nrtrades"]
    assert {t["symbol"] for t in trades} == {"AAA", "BBB"}
    assert {t["side"] for t in trades} == {"buy", "sell"}


def test_multi_ticker_benchmark_excludes_benchmark_tickers():
    """CCC doubles but is a benchmark, so it must not lift the buy-and-hold line."""
    flat = [100.0] * BARS
    doubling = list(pd.Series(range(BARS), dtype=float) * (100.0 / (BARS - 1)) + 100.0)

    bot = _SignalBot(["AAA", "BBB", "CCC"])
    bot.benchmark_tickers = ["CCC"]
    data = {
        "AAA": _frame(flat, [0] * BARS),
        "BBB": _frame(flat, [0] * BARS),
        "CCC": _frame(doubling, [0] * BARS),
    }

    result = _run(bot, data, return_series=True)

    # Two flat tradeable legs => the benchmark line stays at initial capital.
    assert result["buy_hold_curve"][-1]["v"] == pytest.approx(10000.0)
