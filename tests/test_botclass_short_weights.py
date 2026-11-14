"""
Unit tests for Step S1b: Bot class attrs, short()/cover() wrappers, and signed target weights.
"""

import logging
from unittest.mock import MagicMock

import pytest

from tradingbot.utils.botclass import Bot
from tradingbot.utils.weights import (
    gross_weight,
    net_weight,
    positive_weight_sum,
)

UNIVERSE = ["AAA", "BBB", "QQQ"]


@pytest.fixture
def make_bot(mocker):
    """Build a bare Bot with the DB-touching base __init__ stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _make(
        tickers=None,
        benchmarks=(),
        allow_short=False,
        max_gross=1.0,
        shortable=None,
    ):
        bot = Bot()
        bot.bot_name = "TestBot"
        bot.tickers = list(tickers if tickers is not None else UNIVERSE)
        bot.benchmark_tickers = list(benchmarks)
        bot.LIQUIDATE_UNTRACKED = False
        bot.ALLOW_SHORT = allow_short
        bot.MAX_GROSS = max_gross
        bot.SHORTABLE = shortable
        bot._portfolio_manager = MagicMock()
        return bot

    return _make


# ------------------------------------------------------------------
# 1. Weights helpers (gross_weight, net_weight)
# ------------------------------------------------------------------


def test_gross_and_net_weight_calculation():
    weights = {"AAA": 0.6, "BBB": -0.4, "USD": 0.8}
    assert gross_weight(weights) == pytest.approx(1.0)
    assert net_weight(weights) == pytest.approx(0.2)

    # Without USD key
    pure = {"AAA": 0.5, "BBB": -0.5}
    assert gross_weight(pure) == pytest.approx(1.0)
    assert net_weight(pure) == pytest.approx(0.0)

    # Empty weights
    assert gross_weight({}) == 0.0
    assert net_weight({}) == 0.0

    # positive_weight_sum semantics unchanged
    assert positive_weight_sum(weights) == pytest.approx(1.4)
    assert positive_weight_sum(pure) == pytest.approx(0.5)


# ------------------------------------------------------------------
# 2. Bot class defaults & short()/cover() wrappers
# ------------------------------------------------------------------


def test_bot_class_defaults():
    assert Bot.ALLOW_SHORT is False
    assert Bot.MAX_GROSS == 1.0
    assert Bot.SHORTABLE is None


def test_short_and_cover_wrappers_call_portfolio_manager(make_bot):
    bot = make_bot(allow_short=True)
    bot.short("QQQ", quantity_usd=500.0)
    bot._portfolio_manager.short.assert_called_once_with("QQQ", quantity_usd=500.0)

    bot.cover("QQQ", quantity_usd=-1)
    bot._portfolio_manager.cover.assert_called_once_with("QQQ", quantity_usd=-1)


def test_rebalance_portfolio_passes_kwargs_only_when_allow_short(make_bot):
    # Default bot (ALLOW_SHORT=False): no extra kwargs
    default_bot = make_bot(allow_short=False)
    target = {"AAA": 0.8, "USD": 0.2}
    default_bot.rebalancePortfolio(target, onlyOver50USD=True)
    default_bot._portfolio_manager.rebalance_portfolio.assert_called_once_with(
        target,
        only_over_50_usd=True,
    )

    # Opted-in bot (ALLOW_SHORT=True): passes allow_short, max_gross, shortable
    short_bot = make_bot(allow_short=True, max_gross=2.0, shortable=["QQQ"])
    short_bot.rebalancePortfolio(target, onlyOver50USD=False)
    short_bot._portfolio_manager.rebalance_portfolio.assert_called_once_with(
        target,
        only_over_50_usd=False,
        allow_short=True,
        max_gross=2.0,
        shortable=["QQQ"],
    )


# ------------------------------------------------------------------
# 3. _coerce_target_weights: Default bot vs Opted-in bot
# ------------------------------------------------------------------


def test_default_bot_clamps_negatives_exactly_as_today(make_bot):
    bot = make_bot(allow_short=False)
    raw = {"AAA": -0.5, "BBB": 0.25}
    out = bot._coerce_target_weights(raw, set(UNIVERSE))

    # Negative AAA is clamped to 0.0, USD residual absorbs remaining (0.75)
    assert out["AAA"] == 0.0
    assert out["BBB"] == pytest.approx(0.25)
    assert out["USD"] == pytest.approx(0.75)
    assert sum(out.values()) == pytest.approx(1.0)


def test_opted_in_bot_keeps_negative_and_usd_residual_normalises(make_bot):
    bot = make_bot(allow_short=True, max_gross=1.0)
    raw = {"QQQ": -0.5}
    out = bot._coerce_target_weights(raw, set(UNIVERSE))

    # Negative QQQ is kept (-0.5), USD residual is 1.0 - (-0.5) = 1.5
    assert out["QQQ"] == pytest.approx(-0.5)
    assert out["USD"] == pytest.approx(1.5)
    assert sum(out.values()) == pytest.approx(1.0)


def test_hedged_portfolio_normalises_with_max_gross_two(make_bot):
    bot = make_bot(allow_short=True, max_gross=2.0)
    raw = {"AAA": 1.0, "QQQ": -1.0}
    out = bot._coerce_target_weights(raw, set(UNIVERSE))

    assert out["AAA"] == pytest.approx(1.0)
    assert out["QQQ"] == pytest.approx(-1.0)
    assert out["USD"] == pytest.approx(1.0)
    assert gross_weight(out) == pytest.approx(2.0)
    assert sum(out.values()) == pytest.approx(1.0)


# ------------------------------------------------------------------
# 4. Edge cases: Gross scaling, error logging, and decision -1
# ------------------------------------------------------------------


def test_gross_above_max_gross_is_scaled_down_and_logs_error(make_bot, caplog):
    bot = make_bot(allow_short=True, max_gross=1.0)
    raw = {"AAA": 1.0, "QQQ": -1.0}  # gross = 2.0 > max_gross (1.0)

    with caplog.at_level(logging.ERROR):
        out = bot._coerce_target_weights(raw, set(UNIVERSE))

    assert "targetWeights gross" in caplog.text or "exceeded MAX_GROSS" in caplog.text
    # Scaled down by 1.0 / 2.0 = 0.5
    assert out["AAA"] == pytest.approx(0.5)
    assert out["QQQ"] == pytest.approx(-0.5)
    assert out["USD"] == pytest.approx(1.0)
    assert gross_weight(out) == pytest.approx(1.0)
    assert sum(out.values()) == pytest.approx(1.0)


def test_decision_minus_one_still_exits_even_when_allow_short_is_true(make_bot):
    # In _multi_ticker_target_weights, signal == -1 must exit, not short
    bot = make_bot(allow_short=True)
    decisions = {"AAA": 1, "BBB": -1, "QQQ": 0}
    prices = {"AAA": 100.0, "BBB": 100.0, "QQQ": 100.0}
    portfolio = {"USD": 1000.0, "BBB": 10.0}

    weights = bot._multi_ticker_target_weights(decisions, prices, portfolio)

    # BBB was held, but decision -1 means exit (not in weights)
    assert "BBB" not in weights
    assert "AAA" in weights
    assert weights["AAA"] > 0
    assert sum(weights.values()) == pytest.approx(1.0)


def test_unauthorized_symbol_is_dropped_even_when_negative(make_bot):
    bot = make_bot(allow_short=True)
    raw = {"AAA": 0.4, "UNAUTHORIZED": -0.4}
    out = bot._coerce_target_weights(raw, allowed={"AAA"})

    assert "UNAUTHORIZED" not in out
    assert out["AAA"] == pytest.approx(0.4)
    assert out["USD"] == pytest.approx(0.6)
    assert sum(out.values()) == pytest.approx(1.0)
