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
