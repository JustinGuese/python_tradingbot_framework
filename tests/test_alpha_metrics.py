"""
Alpha vs QQQ is the optimisation target (CLAUDE.md "The target: alpha vs QQQ").

These pin the regression maths on constructed series with known answers, the
timestamp alignment that makes it work across data sources, and the rule that
a missing benchmark yields None — never a 0.0 an optimizer would rank.
"""

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.backtest import ALPHA_KEYS, _compute_alpha_metrics, backtest_bot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.hyperparameter_tuning import tune_hyperparameters


def _series_from_returns(returns, start=100.0):
    return list(start * np.cumprod(1.0 + np.asarray(returns)))


def _days(n, tz="UTC", hour=0):
    return pd.date_range("2026-01-05", periods=n, freq="B", tz=tz) + pd.Timedelta(hours=hour)


RNG = np.random.default_rng(7)
N = 200
QQQ_RETS = RNG.normal(0.0005, 0.012, N)


def _bench(returns=QQQ_RETS, **kw):
    return pd.Series(_series_from_returns(returns), index=_days(len(returns), **kw))


def test_pure_beta_has_zero_alpha():
    """A 2x-levered QQQ is all beta: beta 2, alpha ~0, corr 1."""
    bot = _series_from_returns(2 * QQQ_RETS)
    m = _compute_alpha_metrics(bot, list(_days(N)), _bench(), "1d")
    assert m["beta"] == pytest.approx(2.0, abs=0.02)
    assert m["alpha"] == pytest.approx(0.0, abs=0.01)
    assert m["benchmark_corr"] == pytest.approx(1.0, abs=1e-6)


def test_constant_excess_return_is_annualised_alpha():
    """Half-beta plus 4bp/day uncorrelated drift -> beta 0.5, alpha ~ 0.0004*252."""
    noise = RNG.normal(0.0, 0.001, N)  # expected t ~ 0.0004 / 0.001 * sqrt(200) = 5.7
    bot = _series_from_returns(0.5 * QQQ_RETS + 0.0004 + noise)
    m = _compute_alpha_metrics(bot, list(_days(N)), _bench(), "1d")
    assert m["beta"] == pytest.approx(0.5, abs=0.05)
    assert m["alpha"] == pytest.approx(0.0004 * 252, abs=0.04)
    assert m["alpha_t"] > 2.0


def test_flat_cash_bot_is_zero_not_none():
    """All cash is a measured result (no beta, no alpha), not a missing one."""
    m = _compute_alpha_metrics([10_000.0] * N, list(_days(N)), _bench(), "1d")
    assert m == {"alpha": 0.0, "alpha_t": 0.0, "beta": 0.0, "benchmark_corr": 0.0}


def test_daily_bars_align_across_time_of_day_and_timezone():
    """yfinance daily bars (midnight UTC) vs DB bars (16:00 ET) must still join."""
    bot = _series_from_returns(QQQ_RETS)
    bench = _bench(tz="America/New_York", hour=16)
    m = _compute_alpha_metrics(bot, list(_days(N, tz=None)), bench, "1d")
    assert m["beta"] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize(
    "bench",
    [None, pd.Series([100.0, 101.0])],
    ids=["missing", "too-short"],
)
def test_no_usable_benchmark_is_none(bench):
    m = _compute_alpha_metrics([1.0, 2.0, 3.0, 4.0], list(_days(4)), bench, "1d")
    assert m == dict.fromkeys(ALPHA_KEYS)


def test_no_overlap_is_none():
    later = pd.Series(_series_from_returns(QQQ_RETS), index=_days(N) + pd.Timedelta(days=3650))
    m = _compute_alpha_metrics(_series_from_returns(QQQ_RETS), list(_days(N)), later, "1d")
    assert m == dict.fromkeys(ALPHA_KEYS)


# --------------------------------------------------------------------------- #
#  Through backtest_bot                                                        #
# --------------------------------------------------------------------------- #


class _BuyAndHoldBot(Bot):
    def __init__(self, symbol="AAA", **params):
        # Bypass Bot.__init__: it runs DDL and inserts a portfolio row.
        self.bot_name = "AlphaBot"
        self.symbol = symbol
        self.tickers = [symbol]
        self.benchmark_tickers = []
        self.interval = "1d"
        self.period = "1y"
        self.datas = {}
        self.data = None
        self.params = params

    def decisionFunction(self, row):
        return 1


def _frame(closes):
    return pd.DataFrame(
        {
            "timestamp": _days(len(closes)),
            "close": closes,
            "trend_adx": [25.0] * len(closes),
        }
    )


def _backtest(bot, data, **kw):
    return backtest_bot(
        bot, data=data, save_to_db=False, save_results_to_db=False, slippage_pct=0.0, commission_pct=0.0, **kw
    )


def test_backtest_reports_alpha_with_explicit_benchmark():
    result = _backtest(_BuyAndHoldBot(), _frame(_series_from_returns(QQQ_RETS)), benchmark_close=_bench())
    assert result["beta"] == pytest.approx(1.0, abs=0.01)


def test_backtest_given_data_without_benchmark_does_not_fetch():
    """A caller that supplies data owns fetching (the UI backend): no hidden I/O."""
    bot = _BuyAndHoldBot()
    bot._data_service = None  # any fetch attempt would raise AttributeError
    result = _backtest(bot, _frame(_series_from_returns(QQQ_RETS)))
    assert all(result[k] is None for k in ALPHA_KEYS)


def test_backtest_of_qqq_itself_uses_its_own_data():
    result = _backtest(_BuyAndHoldBot(symbol="QQQ"), _frame(_series_from_returns(QQQ_RETS)))
    assert result["beta"] == pytest.approx(1.0, abs=0.01)


def test_tuner_rejects_unknown_objective():
    with pytest.raises(ValueError, match="objective must be one of"):
        tune_hyperparameters(_BuyAndHoldBot, {"x": [1]}, objective="profit")
