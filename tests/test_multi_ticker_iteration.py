"""
Integration tests for the multi-ticker runner.

The regression that motivates the delegation to rebalance_portfolio: the old
loop issued one transaction per leg in ticker order, and PortfolioManager.buy
silently clamps to available cash and never retries. So when a sell sorted after
a buy, the buy was sized against pre-sale cash and got truncated — permanent,
silent under-investment that persisted until a later run happened to order
things favourably. test_sells_fund_buys_in_one_pass fails on the old code.
"""

import pandas as pd
import pytest

from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.botclass import Bot
from tradingbot.utils.config import ExecutionConfig
from tradingbot.utils.portfolio_manager import PortfolioManager

FREE = ExecutionConfig(slippage_pct=0.0, commission_pct=0.0, min_trade_usd=1.0, rebalance_band_pct=0.0)
UNIVERSE = ["VTI", "IJS", "TLT", "SHY", "IAU"]
BENCHMARK = "SPY"


@pytest.fixture
def runner(mocker, sqlite_db, db_session):
    """
    A Bot on the multi-ticker path with real DB writes but no network.

    Data fetch, decisions and pricing are stubbed; everything from
    _multi_ticker_target_weights through PortfolioManager is the real code.
    """
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _make(tickers, benchmarks, decisions, prices, portfolio, name="RunnerBot"):
        bot = Bot()
        bot.bot_name = name
        bot.tickers = list(tickers)
        bot.benchmark_tickers = list(benchmarks)
        bot.LIQUIDATE_UNTRACKED = False
        bot.interval = "1d"
        bot.period = "1y"
        bot.datas = {}
        bot.data = None  # only the pre-delegation buy/sell path reads this

        dbbot = BotRepository.create_or_get_bot(name, session=db_session)
        dbbot.portfolio = dict(portfolio)
        BotRepository.update_bot(dbbot, session=db_session)
        db_session.commit()

        ds = mocker.MagicMock()
        ds.get_latest_price.side_effect = lambda sym, cached=None: prices[sym]
        ds.get_latest_prices_batch.side_effect = lambda syms: {s: prices[s] for s in syms if s in prices}

        bot._bot_repository = BotRepository
        bot._data_service = ds
        bot.dbBot = dbbot
        bot._portfolio_manager = PortfolioManager(dbbot, name, ds, BotRepository, execution_config=FREE)

        # Stub the network-facing helpers only.
        bot.getYFDataWithTA = mocker.MagicMock(side_effect=lambda symbol, **kw: pd.DataFrame({"close": [1.0]}))
        bot.getLatestDecision = mocker.MagicMock(
            side_effect=lambda data: decisions.get(getattr(bot, "_current_ticker", None), 0)
        )
        bot.getLatestPricesBatch = mocker.MagicMock(side_effect=lambda syms: {s: prices[s] for s in syms})
        bot.getLatestPrice = mocker.MagicMock(side_effect=lambda sym: prices[sym])
        return bot

    return _make


def _portfolio(db_session, name="RunnerBot"):
    db_session.expire_all()
    return BotRepository.create_or_get_bot(name, session=db_session).portfolio


def test_sells_fund_buys_in_one_pass(runner, db_session):
    """
    THE regression, and note the ticker ORDER is the whole point: the buy leg
    (BBB) is listed before the sell leg (AAA). The old per-leg loop walked
    tickers in order, so it tried to buy BBB while cash was still $0 — the buy
    was silently clamped to nothing — and only then sold AAA, leaving the
    proceeds idle until some later run happened to order things favourably.

    The book is $5,000 in AAA. BBB is told to buy, AAA to exit. With two
    tradeable legs an equal-weight sleeve is $2,500, so the correct outcome is
    BBB fully funded to $2,500 (50 shares at $50) with the rest in cash.
    """
    bot = runner(
        tickers=["BBB", "AAA"],
        benchmarks=[],
        decisions={"AAA": -1, "BBB": 1},
        prices={"AAA": 100.0, "BBB": 50.0},
        portfolio={"USD": 0.0, "AAA": 50.0},  # $5,000 all in AAA
    )

    bot._run_multi_ticker_iteration()

    final = _portfolio(db_session)
    assert "AAA" not in final
    # Funded from AAA's sale proceeds, which had not happened yet at buy time
    # under the old ordering.
    assert final["BBB"] == pytest.approx(50.0)
    assert final["USD"] == pytest.approx(2500.0)


def test_benchmark_gets_data_but_never_a_decision(runner, mocker):
    seen = []
    bot = runner(
        tickers=[*UNIVERSE, BENCHMARK],
        benchmarks=[BENCHMARK],
        decisions=dict.fromkeys(UNIVERSE, 0),
        prices=dict.fromkeys([*UNIVERSE, BENCHMARK], 100.0),
        portfolio={"USD": 10000.0},
    )
    bot.getLatestDecision = mocker.MagicMock(side_effect=lambda data: seen.append(bot._current_ticker) or 0)

    bot._run_multi_ticker_iteration()

    fetched = {c.kwargs["symbol"] for c in bot.getYFDataWithTA.call_args_list}
    assert BENCHMARK in fetched, "benchmark data must still be loaded for the RRG"
    assert BENCHMARK not in seen, "benchmark must never be asked for a decision"
    assert set(seen) == set(UNIVERSE)


def test_benchmark_capital_is_reclaimed(runner, db_session):
    """
    The whole point of Part C: with the divisor at 5 instead of 6, a fully
    invested book deploys everything rather than stranding 1/6 in cash.
    """
    bot = runner(
        tickers=[*UNIVERSE, BENCHMARK],
        benchmarks=[BENCHMARK],
        decisions=dict.fromkeys(UNIVERSE, 1),
        prices=dict.fromkeys([*UNIVERSE, BENCHMARK], 100.0),
        portfolio={"USD": 10000.0},
    )

    bot._run_multi_ticker_iteration()

    final = _portfolio(db_session)
    assert final.get("USD", 0) == pytest.approx(0.0, abs=1e-6)
    for sym in UNIVERSE:
        assert final[sym] == pytest.approx(20.0)  # $2,000 each at $100
    assert BENCHMARK not in final


def test_untracked_holding_is_left_alone_by_default(runner, db_session):
    """KronosTraderBot rebuilds its universe each run; a gap must not liquidate."""
    bot = runner(
        tickers=["AAA", "BBB"],
        benchmarks=[],
        decisions={"AAA": 1, "BBB": 1},
        prices={"AAA": 100.0, "BBB": 50.0, "ZZZZ": 10.0},
        portfolio={"USD": 5000.0, "ZZZZ": 100.0},
    )

    bot._run_multi_ticker_iteration()

    final = _portfolio(db_session)
    assert final["ZZZZ"] == pytest.approx(100.0)


def test_prices_are_prewarmed_before_the_rebalance(runner, mocker):
    """
    rebalance_portfolio prices each leg with get_latest_price and passes no
    cached frame, so without a prewarm those lookups can hit yfinance while
    holding the bots row lock.
    """
    bot = runner(
        tickers=["AAA", "BBB"],
        benchmarks=[],
        decisions={"AAA": 1, "BBB": 1},
        prices={"AAA": 100.0, "BBB": 50.0},
        portfolio={"USD": 10000.0},
    )
    order = []
    bot.getLatestPrice = mocker.MagicMock(side_effect=lambda sym: order.append("prewarm") or 100.0)
    real_rebalance = bot.rebalancePortfolio
    bot.rebalancePortfolio = mocker.MagicMock(
        side_effect=lambda w, **kw: order.append("rebalance") or real_rebalance(w, **kw)
    )

    bot._run_multi_ticker_iteration()

    assert order.index("rebalance") == len(order) - 1
    assert "prewarm" in order


# ======================================================================
#  targetWeights live path
# ======================================================================


@pytest.fixture
def weights_runner(runner, mocker):
    """
    A runner whose bot returns a fixed target book instead of -1/0/1 signals.

    targetWeights has to live on a real subclass, not on the instance: dispatch
    tests `type(self).targetWeights is not Bot.targetWeights`, so an instance
    attribute would be invisible to it. Building a throwaway subclass per bot
    also keeps the patch off Bot itself, where it would leak into every other
    test in the session.
    """

    def _make(tickers, benchmarks, weights, prices, portfolio, name="WeightsRunnerBot", frames=None):
        bot = runner(
            tickers=tickers,
            benchmarks=benchmarks,
            decisions={},
            prices=prices,
            portfolio=portfolio,
            name=name,
        )
        bot.__class__ = type("_WeightsRunnerBot", (Bot,), {"targetWeights": lambda self, rows: dict(weights)})
        # `runner` builds bots for _run_multi_ticker_iteration, which never reads
        # .symbol; makeOneIteration's dispatch does, so set it here.
        bot.symbol = None

        # One bar per ticker is enough: the live path only reads .iloc[-1].
        default = pd.DataFrame({"close": [1.0]})
        by_ticker = frames or {}
        bot.getYFDataWithTA = mocker.MagicMock(side_effect=lambda symbol, **kw: by_ticker.get(symbol, default))
        return bot

    return _make


def test_target_weights_live_path_applies_the_book(weights_runner, db_session):
    """A 60/40 target on a $10k cash book buys 60 and 40 units at $100."""
    bot = weights_runner(
        tickers=["AAA", "BBB"],
        benchmarks=[],
        weights={"AAA": 0.6, "BBB": 0.4},
        prices={"AAA": 100.0, "BBB": 100.0},
        portfolio={"USD": 10000.0},
    )

    bot._run_target_weights_iteration()

    final = _portfolio(db_session, "WeightsRunnerBot")
    assert final["AAA"] == pytest.approx(60.0)
    assert final["BBB"] == pytest.approx(40.0)
    assert final.get("USD", 0.0) == pytest.approx(0.0, abs=1e-6)


def test_target_weights_live_path_leaves_the_residual_in_cash(weights_runner, db_session):
    """Weights below 1.0 must not be levered back up to fully invested."""
    bot = weights_runner(
        tickers=["AAA", "BBB"],
        benchmarks=[],
        weights={"AAA": 0.25, "BBB": 0.25},
        prices={"AAA": 100.0, "BBB": 100.0},
        portfolio={"USD": 10000.0},
    )

    bot._run_target_weights_iteration()

    final = _portfolio(db_session, "WeightsRunnerBot")
    assert final["AAA"] == pytest.approx(25.0)
    assert final["USD"] == pytest.approx(5000.0)


def test_target_weights_live_path_liquidates_a_held_benchmark(weights_runner, db_session):
    """
    A benchmark can never be given weight, so if it were also pinned as an
    untracked holding nothing could ever exit it.
    """
    bot = weights_runner(
        tickers=["AAA", BENCHMARK],
        benchmarks=[BENCHMARK],
        weights={"AAA": 1.0},
        prices={"AAA": 100.0, BENCHMARK: 100.0},
        portfolio={"USD": 5000.0, BENCHMARK: 50.0},
    )

    bot._run_target_weights_iteration()

    final = _portfolio(db_session, "WeightsRunnerBot")
    assert BENCHMARK not in final
    assert final["AAA"] == pytest.approx(100.0)


def test_target_weights_live_path_fetches_benchmark_data(weights_runner):
    """The benchmark is unfundable but must still reach targetWeights."""
    bot = weights_runner(
        tickers=["AAA", BENCHMARK],
        benchmarks=[BENCHMARK],
        weights={"AAA": 1.0},
        prices={"AAA": 100.0, BENCHMARK: 100.0},
        portfolio={"USD": 10000.0},
    )

    bot._run_target_weights_iteration()

    fetched = {c.kwargs["symbol"] for c in bot.getYFDataWithTA.call_args_list}
    assert BENCHMARK in fetched


def test_target_weights_live_path_skips_the_run_on_missing_data(weights_runner, db_session):
    """
    A data outage must not read as a deliberate exit. An absent ticker would
    come back with no weight and get liquidated — so the run aborts instead.
    """
    bot = weights_runner(
        tickers=["AAA", "BBB"],
        benchmarks=[],
        weights={"AAA": 0.5, "BBB": 0.5},
        prices={"AAA": 100.0, "BBB": 100.0},
        portfolio={"USD": 0.0, "AAA": 50.0},
        frames={"AAA": pd.DataFrame({"close": [1.0]}), "BBB": pd.DataFrame({"close": []})},
    )

    assert bot._run_target_weights_iteration() == 0

    final = _portfolio(db_session, "WeightsRunnerBot")
    assert final["AAA"] == pytest.approx(50.0), "the position must survive a data outage"


def test_target_weights_dispatches_from_make_one_iteration(weights_runner, mocker):
    """makeOneIteration must route to the weights path, ahead of the N>1 check."""
    bot = weights_runner(
        tickers=["AAA", "BBB"],
        benchmarks=[],
        weights={"AAA": 1.0},
        prices={"AAA": 100.0, "BBB": 100.0},
        portfolio={"USD": 10000.0},
    )
    weights_path = mocker.patch.object(type(bot), "_run_target_weights_iteration", return_value=7)
    multi_path = mocker.patch.object(type(bot), "_run_multi_ticker_iteration", return_value=0)

    assert bot.makeOneIteration() == 7
    assert weights_path.called
    assert not multi_path.called


def _bar_frame(price, bars=3):
    idx = pd.date_range("2026-01-01", periods=bars, freq="D")
    return pd.DataFrame(
        {
            "open": [price] * bars,
            "high": [price] * bars,
            "low": [price] * bars,
            "close": [price] * bars,
            "volume": [1000.0] * bars,
            "trend_adx": [25.0] * bars,
        },
        index=idx,
    )


def test_target_weights_live_and_backtest_agree_on_one_bar(weights_runner, db_session, mocker):
    """
    THE parity assertion. Both paths call the same targetWeights and the same
    _coerce_target_weights; only execution differs. With costs and the no-trade
    band zeroed on both sides, one bar through each must land on identical
    per-symbol dollar values — otherwise a backtest stops predicting live
    behaviour, which is the only reason to run one.
    """
    from tradingbot.utils.backtest import backtest_bot

    target = {"AAA": 0.6, "BBB": 0.3}
    prices = {"AAA": 100.0, "BBB": 50.0}

    # --- live: the `runner` fixture already wires PortfolioManager with FREE ---
    bot = weights_runner(
        tickers=["AAA", "BBB"],
        benchmarks=[],
        weights=target,
        prices=prices,
        portfolio={"USD": 10000.0},
    )
    bot._run_target_weights_iteration()
    live = _portfolio(db_session, "WeightsRunnerBot")
    live_values = {sym: live.get(sym, 0.0) * px for sym, px in prices.items()}
    live_values["USD"] = live.get("USD", 0.0)

    # --- backtest: same decision, same prices, costs and band zeroed ---
    class _Fixed(Bot):
        def __init__(self):
            self.bot_name = "ParityWeights"
            self.tickers = ["AAA", "BBB"]
            self.benchmark_tickers = []
            self.symbol = None
            self.interval = "1d"
            self.period = "1y"
            self.datas = {}
            self.data = None
            self.params = {}
            self.LIQUIDATE_UNTRACKED = False

        def targetWeights(self, rows):
            return dict(target)

    mocker.patch("tradingbot.utils.backtest.EXECUTION_CONFIG", FREE)
    result = backtest_bot(
        _Fixed(),
        initial_capital=10000.0,
        data={sym: _bar_frame(px) for sym, px in prices.items()},
        save_to_db=False,
        save_results_to_db=False,
        slippage_pct=0.0,
        commission_pct=0.0,
        return_series=True,
    )

    # Prices are flat, so the book never drifts: the first bar's fills ARE the
    # steady state, and comparing final values is comparing the same thing live
    # settled on.
    bt_values = dict.fromkeys(prices, 0.0)
    first_ts = result["trades"][0]["t"]
    for trade in result["trades"]:
        if trade["t"] != first_ts:
            break
        bt_values[trade["symbol"]] += trade["qty"] * trade["price"]
    bt_values["USD"] = 10000.0 - sum(v for k, v in bt_values.items() if k != "USD")

    for symbol in ("AAA", "BBB", "USD"):
        assert live_values[symbol] == pytest.approx(bt_values[symbol], abs=1e-6), (
            f"{symbol}: live {live_values[symbol]} != backtest {bt_values[symbol]}"
        )
