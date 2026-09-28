"""
One decision path: the live bot and the replay backtest must decide the same.

Modelled on test_target_weights_live_and_backtest_agree_on_one_bar: the SAME
decide function (utils/option_strategies) runs once through the live adapters
(LiveMarket/LiveHoldings over the fake BSTicker chain) and once through the
replay adapters (ReplayDayMarket/ReplayHoldings over the option_quotes rows
that live fetch stored). The action lists must be identical, and the replay
executor must fill them.
"""

import pandas as pd
import pytest
from test_option_strategies import (  # noqa: F401  (chain is a fixture)
    NEAR,
    RICH_IV,
    TODAY,
    WILD,
    _dated,
    _make_bot,
    _make_multi_bot,
    _ohlc,
    _series,
    chain,
)

from tradingbot.option_crossvolbot import OptionCrossVolBot
from tradingbot.option_earningscrushbot import OptionEarningsCrushBot
from tradingbot.option_indexvolbot import OptionIndexVolBot
from tradingbot.option_mispricingscanbot import OptionMispricingScanBot
from tradingbot.utils import option_strategies as st
from tradingbot.utils import options
from tradingbot.utils.option_decide import Close, Hedge, Open, summary
from tradingbot.utils.option_replay import (
    PriceHistory,
    ReplayBook,
    ReplayDayMarket,
    ReplayHoldings,
    ReplayMarket,
    execute,
)
from tradingbot.utils.option_strategy_bot import LiveHoldings, LiveMarket


def _prices(data) -> PriceHistory:
    frame = _ohlc(data)
    flat = lambda level: frame.assign(open=level, high=level, low=level, close=level)  # noqa: E731
    return PriceHistory({"AAPL": frame, "^VIX": flat(20.0), "^VIX3M": flat(22.0), "^VVIX": flat(90.0)})


def _replay(data):
    market = ReplayMarket(TODAY, TODAY)
    return ReplayDayMarket(market, TODAY, _prices(data)), ReplayHoldings(ReplayBook(market), TODAY), market


pytestmark = pytest.mark.usefixtures("chain")  # the fake BSTicker chain from test_option_strategies


@pytest.fixture
def close_at_20_utc(mocker):
    """Today closes at 20:00 UTC whatever the real calendar says, so the test runs any day."""
    mocker.patch(
        "tradingbot.utils.market_calendar.session_close_utc",
        lambda d: pd.Timestamp(d).tz_localize("UTC").to_pydatetime().replace(hour=20),
    )


def test_index_vol_decides_the_same_live_and_in_replay(sqlite_db, mocker, close_at_20_utc):
    mocker.patch("tradingbot.option_indexvolbot.UNDERLYING", "AAPL")
    bot = _make_bot(OptionIndexVolBot, mocker, _series(RICH_IV))
    mocker.patch.object(bot, "getYFData", return_value=_dated(_series(RICH_IV)))
    rules = OptionIndexVolBot.RULES

    live = st.decide_indexvol(LiveMarket(bot), LiveHoldings(bot), rules, "AAPL")
    market, holdings, stored = _replay(_series(RICH_IV))
    replay = st.decide_indexvol(market, holdings, rules, "AAPL")

    assert summary(live) == (1, 0) and live == replay
    book = ReplayBook(stored)
    assert execute(replay, book, TODAY) == (1, 0)
    assert len(book.positions) == 4 and book.cash > 100_000.0  # a condor opens for a credit


def test_cross_vol_decides_the_same_live_and_in_replay(sqlite_db, mocker):
    bot = _make_multi_bot(OptionCrossVolBot, "option_crossvolbot", mocker, _series(RICH_IV))
    rules = OptionCrossVolBot.RULES

    live = st.decide_crossvol(LiveMarket(bot), LiveHoldings(bot), rules, ("AAPL",))
    market, holdings, _ = _replay(_series(RICH_IV))
    replay = st.decide_crossvol(market, holdings, rules, ("AAPL",))

    assert summary(live) == (1, 0) and live == replay


def test_earnings_crush_decides_the_same_live_and_in_replay(sqlite_db, mocker, close_at_20_utc):
    bot = _make_multi_bot(OptionEarningsCrushBot, "option_earningscrushbot", mocker, _series(RICH_IV))
    tomorrow = (pd.Timestamp(TODAY) + pd.offsets.BDay(1)).date()
    # "Reports tomorrow" whatever weekday the suite runs on (a Saturday has no next-session report).
    one_session = lambda a, b: 1 if b > a else 0  # noqa: E731
    mocker.patch("tradingbot.utils.option_rules.business_days", one_session)
    mocker.patch("tradingbot.utils.option_strategies.business_days", one_session)
    mocker.patch.object(options, "next_earnings_event", lambda u, today=None: (tomorrow, False))
    past = [((pd.Timestamp(TODAY) - pd.offsets.BDay(20 * i)).date(), False) for i in range(1, 13)]
    mocker.patch.object(options, "earnings_events", lambda u, limit=40: past)
    mocker.patch.object(options, "atm_iv", lambda view, r=None, american=False: 0.60 if view.expiry == NEAR else 0.30)
    rules = OptionEarningsCrushBot.RULES
    evening = pd.Timestamp(f"{TODAY} 19:30", tz="UTC").to_pydatetime()

    live = st.decide_earningscrush(LiveMarket(bot, evening), LiveHoldings(bot), rules, ("AAPL",))
    market, holdings, _ = _replay(_series(RICH_IV))  # replay "now" is 15 minutes before the close
    replay = st.decide_earningscrush(market, holdings, rules, ("AAPL",))

    assert summary(live) == (1, 0) and live == replay


def test_replay_executor_hedges_and_closes_with_the_stock(sqlite_db, mocker):
    """A cheap-side scanner position: straddle, then a Whalley-Wilmott hedge; Close(include_stock) flattens both."""
    bot = _make_bot(OptionMispricingScanBot, mocker, _series(WILD))
    options.load_chain("AAPL", 35)  # store today's quotes for the replay
    market, _, stored = _replay(_series(WILD))
    view = market.chain("AAPL", 35)
    straddle = options.select_straddle(view)
    book = ReplayBook(stored)
    assert execute([Open(straddle, 20_000.0)], book, TODAY) == (1, 0)
    book.shares["AAPL"] = 0.0
    # Push the book off delta-neutral so the band must act: add a lone long call.
    call = next(k for k, _ in straddle.legs if options.parse_occ(k).right == "C")
    book.positions[call] += 500.0
    execute([Hedge("AAPL", 0.0, ww=(0.0005, 1e-4))], book, TODAY)
    assert book.shares["AAPL"] < 0  # sold stock against the extra calls
    holdings = ReplayHoldings(book, TODAY)
    assert holdings.opened_on("AAPL") == TODAY
    assert holdings.structure_pnl("AAPL", holdings.book("AAPL")) == pytest.approx(
        book.equity(TODAY) - 100_000.0, abs=1e-6
    )
    execute([Close("AAPL", include_stock=True)], book, TODAY)
    assert book.positions == {} and "AAPL" not in book.shares
    assert bot.dbBot.portfolio == {"USD": 100_000.0}  # the live bot was never touched
