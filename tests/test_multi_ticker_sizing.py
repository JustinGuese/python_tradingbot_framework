"""
Tests for multi-ticker allocation: the benchmark/tradeable split and trimming.

The bugs these pin, all in Bot._run_multi_ticker_iteration:

  * `N = len(self.tickers)` counted tickers the bot can never trade.
    GoldenButterflyMomBot carries SPY purely as an RRG baseline and returns 0 for
    it forever, so it divided by 6 for 5 sleeves and stranded 1/6 of capital.
  * The loop only bought on signal 1 and only sold on signal -1, so a position
    that drifted above target was never trimmed.
  * Buys were sized against a stale pre-loop snapshot and executed in ticker
    order while PortfolioManager.buy silently clamps to available cash — so a
    sell that sorted after a buy never funded it.

These never touch a database: Bot.__init__ is stubbed and the weights builder is
a pure function of (decisions, prices, portfolio).
"""

import pytest

from tradingbot.utils.botclass import Bot

UNIVERSE = ["VTI", "IJS", "TLT", "SHY", "IAU"]
BENCHMARK = "SPY"
PRICES = dict.fromkeys([*UNIVERSE, BENCHMARK, "FOO"], 100.0)


@pytest.fixture
def make_bot(mocker):
    """Build a bare Bot with the DB-touching base __init__ stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _make(tickers=None, benchmarks=(), liquidate_untracked=False):
        bot = Bot()
        bot.bot_name = "TestMultiTicker"
        bot.tickers = list(tickers if tickers is not None else [*UNIVERSE, BENCHMARK])
        bot.benchmark_tickers = list(benchmarks)
        bot.LIQUIDATE_UNTRACKED = liquidate_untracked
        return bot

    return _make


def _weights(bot, decisions, portfolio, prices=None):
    return bot._multi_ticker_target_weights(decisions, prices or PRICES, portfolio)


# ------------------------------------------------------------------
# tradeable_tickers
# ------------------------------------------------------------------


def test_tradeable_tickers_defaults_to_all_tickers(make_bot):
    """Guards the 27 bots that declare no benchmarks."""
    bot = make_bot(tickers=["A", "B"], benchmarks=())
    assert bot.tradeable_tickers == ["A", "B"]


def test_tradeable_tickers_excludes_benchmarks(make_bot):
    bot = make_bot(benchmarks=[BENCHMARK])
    assert bot.tradeable_tickers == UNIVERSE
    assert BENCHMARK not in bot.tradeable_tickers


# ------------------------------------------------------------------
# __init__ validation (real __init__, DB calls patched out)
# ------------------------------------------------------------------


def test_benchmark_not_in_tickers_raises_before_touching_the_db(mocker):
    """A misconfigured bot must not first materialise a $10k row in `bots`."""
    mocker.patch("tradingbot.utils.botclass.init_db")
    create = mocker.patch("tradingbot.utils.botclass.BotRepository.create_or_get_bot")

    with pytest.raises(ValueError, match="not in tickers"):
        Bot("Broken", tickers=["VTI"], benchmark_tickers=["SPY"])

    create.assert_not_called()


def test_all_tickers_benchmark_raises(mocker):
    mocker.patch("tradingbot.utils.botclass.init_db")
    mocker.patch("tradingbot.utils.botclass.BotRepository.create_or_get_bot")

    with pytest.raises(ValueError, match="nothing left to trade"):
        Bot("Broken", tickers=["SPY"], benchmark_tickers=["SPY"])


def test_duplicate_tickers_are_deduped(mocker):
    """A repeat would both inflate the divisor and get traded twice."""
    mocker.patch("tradingbot.utils.botclass.init_db")
    mocker.patch("tradingbot.utils.botclass.BotRepository.create_or_get_bot")

    bot = Bot("Dupes", tickers=["VTI", "VTI", "TLT"])
    assert bot.tickers == ["VTI", "TLT"]


# ------------------------------------------------------------------
# The divisor
# ------------------------------------------------------------------


def test_divisor_excludes_benchmark(make_bot):
    """The headline regression: this returned 1/6 per sleeve before the fix."""
    bot = make_bot(benchmarks=[BENCHMARK])
    w = _weights(bot, dict.fromkeys(UNIVERSE, 1), {"USD": 10000.0})

    for sym in UNIVERSE:
        assert w[sym] == pytest.approx(0.2)
    assert BENCHMARK not in w
    assert w["USD"] == pytest.approx(0.0)


def test_benchmark_holding_is_liquidated(make_bot):
    """Nothing else in the codebase could ever exit a benchmark position."""
    bot = make_bot(benchmarks=[BENCHMARK])
    w = _weights(bot, dict.fromkeys(UNIVERSE, 1), {"USD": 5000.0, BENCHMARK: 50.0})

    assert BENCHMARK not in w
    # SPY's $5,000 is in the sizing base, so the book is $10,000.
    assert w["VTI"] == pytest.approx(0.2)


# ------------------------------------------------------------------
# Trimming and signal semantics
# ------------------------------------------------------------------


def test_overweight_leg_is_trimmed(make_bot):
    """Fails on the old code, which only ever bought on signal 1."""
    bot = make_bot(benchmarks=[BENCHMARK])
    # VTI is 40% of a $10,000 book; equal weight is 20%.
    w = _weights(bot, dict.fromkeys(UNIVERSE, 1), {"USD": 6000.0, "VTI": 40.0})

    assert w["VTI"] == pytest.approx(0.2)


def test_hold_signal_is_not_funded(make_bot):
    bot = make_bot(benchmarks=[BENCHMARK])
    decisions = {**dict.fromkeys(UNIVERSE, 1), "VTI": 0}
    w = _weights(bot, decisions, {"USD": 9500.0, "VTI": 5.0})

    assert w["VTI"] == pytest.approx(0.05)  # unchanged, not raised to a sleeve


def test_hold_signal_is_capped(make_bot):
    """The cap is a risk limit, independent of signal — this is the half of the
    buy-only asymmetry that trimming fixes."""
    bot = make_bot(benchmarks=[BENCHMARK])
    decisions = {**dict.fromkeys(UNIVERSE, 1), "VTI": 0}
    w = _weights(bot, decisions, {"USD": 6000.0, "VTI": 40.0})

    assert w["VTI"] == pytest.approx(0.2)


def test_hold_signal_with_no_position_stays_flat(make_bot):
    """
    The KronosTraderBot case: its decisionFunction returns 0 for "no prediction
    available", not merely "no edge". Funding a 0 leg would make it buy every
    symbol it has no opinion about.
    """
    bot = make_bot(benchmarks=[BENCHMARK])
    w = _weights(bot, dict.fromkeys(UNIVERSE, 0), {"USD": 10000.0})

    assert w == {"USD": pytest.approx(1.0)}


def test_sell_signal_exits_even_a_tiny_position(make_bot):
    """A strategy that says 'get out' must be able to get out at any size."""
    bot = make_bot(benchmarks=[BENCHMARK])
    decisions = {**dict.fromkeys(UNIVERSE, 1), "VTI": -1}
    w = _weights(bot, decisions, {"USD": 9970.0, "VTI": 0.3})

    assert "VTI" not in w


# ------------------------------------------------------------------
# Untracked and unpriceable symbols
# ------------------------------------------------------------------


def test_untracked_holding_is_preserved_and_excluded_from_the_base(make_bot):
    bot = make_bot(benchmarks=[BENCHMARK])
    # FOO is $2,000 of a $10,000 book and is not in self.tickers.
    w = _weights(bot, dict.fromkeys(UNIVERSE, 1), {"USD": 8000.0, "FOO": 20.0})

    assert w["FOO"] == pytest.approx(0.2)
    # The remaining $8,000 is split five ways => 16% each.
    assert w["VTI"] == pytest.approx(0.16)
    assert sum(w.values()) == pytest.approx(1.0)


def test_untracked_holding_is_liquidated_when_flag_is_set(make_bot):
    bot = make_bot(benchmarks=[BENCHMARK], liquidate_untracked=True)
    w = _weights(bot, dict.fromkeys(UNIVERSE, 1), {"USD": 8000.0, "FOO": 20.0})

    assert "FOO" not in w
    assert w["VTI"] == pytest.approx(0.2)  # full $10,000 base


def test_unpriceable_tradeable_ticker_leaves_the_divisor(make_bot):
    """
    Sizing an unpriceable ticker against an assumed value of zero would buy a
    full sleeve on top of a position already held.
    """
    bot = make_bot(benchmarks=[BENCHMARK])
    prices = {**PRICES, "TLT": None}
    w = _weights(bot, dict.fromkeys(UNIVERSE, 1), {"USD": 10000.0}, prices=prices)

    assert "TLT" not in w
    assert w["VTI"] == pytest.approx(0.25)  # divided by 4, not 5


def test_empty_portfolio_returns_no_weights(make_bot):
    bot = make_bot(benchmarks=[BENCHMARK])
    assert _weights(bot, dict.fromkeys(UNIVERSE, 1), {"USD": 0.0}) == {}


@pytest.mark.parametrize(
    "decisions",
    [
        dict.fromkeys(UNIVERSE, 1),
        dict.fromkeys(UNIVERSE, 0),
        dict.fromkeys(UNIVERSE, -1),
        {"VTI": 1, "IJS": 0, "TLT": -1, "SHY": 1, "IAU": 0},
    ],
)
def test_weights_always_sum_to_one(make_bot, decisions):
    """rebalance_portfolio raises if the sum strays more than 0.01 from 1.0."""
    bot = make_bot(benchmarks=[BENCHMARK])
    portfolio = {"USD": 3000.0, "VTI": 20.0, "IJS": 30.0, "FOO": 20.0}
    w = _weights(bot, decisions, portfolio)

    assert sum(w.values()) == pytest.approx(1.0)
    assert all(v >= 0 for v in w.values())
