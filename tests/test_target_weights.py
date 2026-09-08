"""
Tests for the targetWeights bot type: the coercion contract and its classification.

targetWeights lets a bot return a target book instead of a -1/0/1 signal, which
is what makes vol-targeted strategies backtestable at all. Everything a bot
returns is untrusted input to _coerce_target_weights, and the caller reconciles
the WHOLE portfolio against its output — so a mistake there liquidates positions
rather than merely mis-sizing them. Hence the exhaustive coercion cases below.

The one rule worth restating because it is counter-intuitive: a weight on a
symbol the bot may not trade is dropped and NOT redistributed. A buggy bot
should under-invest visibly (money sitting in cash) rather than silently
over-weight the legs that remain.

These never touch a database: Bot.__init__ is stubbed and _coerce_target_weights
is a pure function.
"""

import math

import pytest

from tradingbot.utils.botclass import Bot

UNIVERSE = ["AAA", "BBB", "CCC"]
BENCHMARK = "SPY"


@pytest.fixture
def make_bot(mocker):
    """Build a bare Bot with the DB-touching base __init__ stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _make(tickers=None, benchmarks=(), liquidate_untracked=False):
        bot = Bot()
        bot.bot_name = "TestTargetWeights"
        bot.tickers = list(tickers if tickers is not None else [*UNIVERSE, BENCHMARK])
        bot.benchmark_tickers = list(benchmarks)
        bot.LIQUIDATE_UNTRACKED = liquidate_untracked
        return bot

    return _make


def _coerce(bot, raw, allowed=None, held=None):
    return bot._coerce_target_weights(raw, set(allowed if allowed is not None else UNIVERSE), held_weights=held)


# ------------------------------------------------------------------
# The USD residual
# ------------------------------------------------------------------


def test_weights_below_one_leave_the_rest_in_cash(make_bot):
    out = _coerce(make_bot(), {"AAA": 0.3, "BBB": 0.2})
    assert out["AAA"] == pytest.approx(0.3)
    assert out["BBB"] == pytest.approx(0.2)
    assert out["USD"] == pytest.approx(0.5)


def test_weights_summing_above_one_are_scaled_to_one(make_bot):
    """A bot asking for 150% gets its relative sizing kept and the level cut."""
    out = _coerce(make_bot(), {"AAA": 1.0, "BBB": 0.5})
    assert out["AAA"] == pytest.approx(2 / 3)
    assert out["BBB"] == pytest.approx(1 / 3)
    assert out["USD"] == pytest.approx(0.0)


def test_slightly_over_one_is_normalized_not_rejected(make_bot):
    """Float drift must not fail a CronJob."""
    out = _coerce(make_bot(), {"AAA": 0.500001, "BBB": 0.500001})
    assert sum(out.values()) == pytest.approx(1.0)
    assert out["AAA"] == pytest.approx(0.5)


def test_usd_key_is_ignored(make_bot):
    """Cash is the derived residual; a bot cannot dictate it or it double-counts."""
    out = _coerce(make_bot(), {"AAA": 0.4, "USD": 0.9})
    assert out["USD"] == pytest.approx(0.6)


def test_output_always_sums_to_one(make_bot):
    for raw in ({}, {"AAA": 0.1}, {"AAA": 5.0}, {"AAA": -1.0}, {"AAA": 0.5, "ZZZ": 0.5}):
        assert sum(_coerce(make_bot(), raw).values()) == pytest.approx(1.0)


# ------------------------------------------------------------------
# Bad input
# ------------------------------------------------------------------


def test_negative_weight_is_clamped_to_zero_and_key_kept(make_bot):
    """
    0.0 means "sell it", absent means "leave it alone" — normalize_weights'
    documented distinction, so the key must survive the clamp.
    """
    out = _coerce(make_bot(), {"AAA": -0.5, "BBB": 0.25})
    assert out["AAA"] == 0.0
    assert out["BBB"] == pytest.approx(0.25)
    assert out["USD"] == pytest.approx(0.75)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_weight_is_dropped(make_bot, bad):
    out = _coerce(make_bot(), {"AAA": bad, "BBB": 0.25})
    assert "AAA" not in out
    assert out["BBB"] == pytest.approx(0.25)
    assert all(math.isfinite(w) for w in out.values())


def test_non_numeric_weight_is_dropped(make_bot):
    out = _coerce(make_bot(), {"AAA": "lots", "BBB": 0.25})
    assert "AAA" not in out
    assert out["USD"] == pytest.approx(0.75)


def test_empty_dict_goes_fully_to_cash(make_bot):
    """Distinct from _multi_ticker_target_weights' {} — saying nothing is a decision."""
    assert _coerce(make_bot(), {}) == {"USD": 1.0}


def test_all_zero_goes_fully_to_cash(make_bot):
    assert _coerce(make_bot(), {"AAA": 0.0, "BBB": 0.0})["USD"] == pytest.approx(1.0)


# ------------------------------------------------------------------
# The allowed set
# ------------------------------------------------------------------


def test_weight_on_a_benchmark_is_dropped_not_redistributed(make_bot):
    """
    The whole point of benchmark_tickers: loaded for data, never fundable. And
    the dropped weight must NOT flow to BBB — under-investing is visible, silent
    over-weighting is not.
    """
    bot = make_bot(benchmarks=[BENCHMARK])
    out = _coerce(bot, {BENCHMARK: 0.5, "BBB": 0.25}, allowed=UNIVERSE)
    assert BENCHMARK not in out
    assert out["BBB"] == pytest.approx(0.25)
    assert out["USD"] == pytest.approx(0.75)


def test_weight_on_an_unknown_ticker_is_dropped(make_bot):
    out = _coerce(make_bot(), {"NOPE": 0.5, "AAA": 0.25})
    assert "NOPE" not in out
    assert out["USD"] == pytest.approx(0.75)


def test_omitted_ticker_simply_gets_no_weight(make_bot):
    out = _coerce(make_bot(), {"AAA": 1.0})
    assert "BBB" not in out and "CCC" not in out


# ------------------------------------------------------------------
# Untracked holdings
# ------------------------------------------------------------------


def test_untracked_holding_is_pinned_and_excluded_from_the_budget(make_bot):
    """Same rule as _multi_ticker_target_weights, so the two paths agree."""
    bot = make_bot(tickers=UNIVERSE)
    out = _coerce(bot, {"AAA": 1.0}, held={"LEGACY": 0.25})
    assert out["LEGACY"] == pytest.approx(0.25)
    assert out["AAA"] == pytest.approx(0.75)
    assert out["USD"] == pytest.approx(0.0)


def test_untracked_holding_is_liquidated_when_flag_is_set(make_bot):
    bot = make_bot(tickers=UNIVERSE, liquidate_untracked=True)
    out = _coerce(bot, {"AAA": 1.0}, held={"LEGACY": 0.25})
    assert "LEGACY" not in out
    assert out["AAA"] == pytest.approx(1.0)


def test_held_benchmark_is_not_pinned_so_it_gets_liquidated(make_bot):
    """
    Nothing else could ever exit a benchmark position: it can never be given
    weight, so if the pin kept it too, it would be stranded forever.
    """
    bot = make_bot(benchmarks=[BENCHMARK])
    out = _coerce(bot, {"AAA": 0.5}, allowed=UNIVERSE, held={BENCHMARK: 0.3})
    assert BENCHMARK not in out


# ------------------------------------------------------------------
# Classification
# ------------------------------------------------------------------


class _WeightsBot(Bot):
    def targetWeights(self, rows):
        return {"AAA": 1.0}


class _BothBot(Bot):
    def decisionFunction(self, row):
        return 1

    def targetWeights(self, rows):
        return {"AAA": 1.0}


def _classify(cls, mocker, tickers):
    mocker.patch.object(Bot, "__init__", lambda self, *a, **k: None)
    bot = cls()
    bot.tickers = list(tickers)
    bot.benchmark_tickers = []
    return bot


def test_backtest_type_is_target_weights(mocker):
    bot = _classify(_WeightsBot, mocker, UNIVERSE)
    assert bot.backtest_type == "target_weights"
    assert bot.can_backtest


def test_single_ticker_target_weights_bot_is_backtestable(mocker):
    """Sizing one position between 0 and 1 is a strategy, not a degenerate case."""
    bot = _classify(_WeightsBot, mocker, ["AAA"])
    assert bot.backtest_type == "target_weights"
    assert bot.can_backtest


def test_target_weights_takes_precedence_over_decision_function(mocker):
    """A bot defining both is one whose weights function calls the other as a helper."""
    bot = _classify(_BothBot, mocker, UNIVERSE)
    assert bot.backtest_type == "target_weights"


def test_plain_bot_is_still_unknown(mocker):
    """The new probe must not accidentally classify a bot that overrides nothing."""
    bot = _classify(Bot, mocker, UNIVERSE)
    assert bot.backtest_type == "unknown"
    assert not bot.can_backtest
