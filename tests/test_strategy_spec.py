"""
The strategy DSL: validation, column introspection and evaluation.

Specs arrive from the network, so validation is a security boundary as much as a
usability one — every rejection here is a request that would otherwise reach the
backtester with something it cannot evaluate.
"""

import math

import pandas as pd
import pytest

from tradingbot.utils.strategy_spec import (
    MAX_CONDITIONS,
    MAX_TICKERS,
    PREV_PREFIX,
    SpecError,
    crossover_columns,
    decide,
    evaluate_condition,
    referenced_columns,
    validate_spec,
)


def _spec(entry=None, exit_=None, **overrides):
    """A minimal valid spec, with pieces overridable per test."""
    base = {
        "version": 1,
        "tickers": ["SPY"],
        "interval": "1d",
        "period": "2y",
        "entry": entry
        or {"match": "all", "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": "<", "right": {"const": 30}}]},
        "exit": exit_
        or {"match": "any", "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": ">", "right": {"const": 70}}]},
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
#  Validation — the happy path                                                 #
# --------------------------------------------------------------------------- #


def test_valid_spec_round_trips():
    spec = validate_spec(_spec())
    assert spec.tickers == ("SPY",)
    assert spec.interval == "1d"
    assert validate_spec(spec.to_dict()).to_dict() == spec.to_dict()


def test_tickers_are_uppercased_and_deduped():
    spec = validate_spec(_spec(tickers=["spy", "SPY", "qqq"]))
    assert spec.tickers == ("SPY", "QQQ")


def test_a_bare_string_ticker_is_accepted():
    assert validate_spec(_spec(tickers="SPY")).tickers == ("SPY",)


def test_yfinance_style_symbols_are_allowed():
    spec = validate_spec(_spec(tickers=["BTC-USD", "EURUSD=X", "^GSPC", "BRK-B"]))
    assert spec.tickers == ("BTC-USD", "EURUSD=X", "^GSPC", "BRK-B")


# --------------------------------------------------------------------------- #
#  Validation — rejections                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"version": 99}, "version"),
        ({"interval": "1m"}, "interval"),
        ({"period": "10y"}, "period"),
        ({"tickers": []}, "tickers"),
        ({"tickers": ["A"] * (MAX_TICKERS + 1)}, "at most"),
        ({"tickers": ["DROP TABLE"]}, "invalid characters"),
    ],
)
def test_top_level_rejections(overrides, fragment):
    with pytest.raises(SpecError, match=fragment):
        validate_spec(_spec(**overrides))


def test_unknown_indicator_is_rejected():
    entry = {
        "match": "all",
        "conditions": [{"left": {"indicator": "not_an_indicator"}, "op": "<", "right": {"const": 1}}],
    }
    with pytest.raises(SpecError, match="unknown indicator"):
        validate_spec(_spec(entry=entry))


def test_unknown_operator_is_rejected():
    entry = {"match": "all", "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": "~=", "right": {"const": 1}}]}
    with pytest.raises(SpecError, match=r"entry\.conditions\[0\]\.op"):
        validate_spec(_spec(entry=entry))


def test_empty_condition_list_is_rejected():
    """`all([])` is True, so an empty exit group would sell on every single bar."""
    with pytest.raises(SpecError, match="at least one condition"):
        validate_spec(_spec(exit_={"match": "all", "conditions": []}))


def test_too_many_conditions_is_rejected():
    condition = {"left": {"indicator": "momentum_rsi"}, "op": "<", "right": {"const": 30}}
    entry = {"match": "all", "conditions": [condition] * (MAX_CONDITIONS + 1)}
    with pytest.raises(SpecError, match="at most"):
        validate_spec(_spec(entry=entry))


def test_two_constants_is_rejected():
    entry = {"match": "all", "conditions": [{"left": {"const": 1}, "op": "<", "right": {"const": 2}}]}
    with pytest.raises(SpecError, match="two constants"):
        validate_spec(_spec(entry=entry))


def test_a_constant_cannot_cross():
    entry = {
        "match": "all",
        "conditions": [{"left": {"const": 30}, "op": "crosses_above", "right": {"indicator": "momentum_rsi"}}],
    }
    with pytest.raises(SpecError, match="cannot cross"):
        validate_spec(_spec(entry=entry))


def test_boolean_is_not_a_number():
    """JSON `true` is an int subclass in Python; accepting it as 1 hides a client bug."""
    entry = {
        "match": "all",
        "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": "<", "right": {"const": True}}],
    }
    with pytest.raises(SpecError, match="expected a number"):
        validate_spec(_spec(entry=entry))


def test_operand_must_have_exactly_one_key():
    entry = {
        "match": "all",
        "conditions": [{"left": {"indicator": "momentum_rsi", "const": 3}, "op": "<", "right": {"const": 30}}],
    }
    with pytest.raises(SpecError, match="exactly one"):
        validate_spec(_spec(entry=entry))


def test_price_operand_is_restricted_to_ohlcv():
    entry = {"match": "all", "conditions": [{"left": {"price": "adj_close"}, "op": "<", "right": {"const": 30}}]}
    with pytest.raises(SpecError, match="price"):
        validate_spec(_spec(entry=entry))


def test_period_must_be_available_at_the_chosen_interval():
    """Yahoo serves at most 730 days of hourly bars, and returns a short frame
    rather than an error — so "5y hourly" would silently measure ~2y."""
    with pytest.raises(SpecError, match="not available at the '1h' interval"):
        validate_spec(_spec(interval="1h", period="5y"))

    # The same period is fine on daily bars.
    assert validate_spec(_spec(interval="1d", period="5y")).period == "5y"
    # And a short period is fine on hourly.
    assert validate_spec(_spec(interval="1h", period="6mo")).interval == "1h"


def test_a_curated_column_set_can_narrow_what_is_accepted():
    """The API passes its dropdown set so a spec can't reference an unlisted column."""
    with pytest.raises(SpecError, match="unknown indicator"):
        validate_spec(_spec(), known_columns=frozenset({"trend_macd"}))


# --------------------------------------------------------------------------- #
#  Column introspection                                                        #
# --------------------------------------------------------------------------- #


def test_referenced_columns_covers_both_groups_and_prices():
    entry = {
        "match": "all",
        "conditions": [{"left": {"indicator": "trend_ema_fast"}, "op": ">", "right": {"indicator": "trend_ema_slow"}}],
    }
    exit_ = {"match": "any", "conditions": [{"left": {"price": "close"}, "op": "<", "right": {"const": 10}}]}
    spec = validate_spec(_spec(entry=entry, exit_=exit_))

    assert referenced_columns(spec) == {"trend_ema_fast", "trend_ema_slow", "close"}


def test_crossover_columns_only_lists_cross_conditions():
    entry = {
        "match": "all",
        "conditions": [
            {"left": {"indicator": "momentum_rsi"}, "op": "crosses_above", "right": {"const": 30}},
            {"left": {"indicator": "trend_adx"}, "op": ">", "right": {"const": 25}},
        ],
    }
    spec = validate_spec(_spec(entry=entry))

    # trend_adx is a plain comparison, so it needs no previous-bar copy.
    assert crossover_columns(spec) == {"momentum_rsi"}


def test_crossover_columns_includes_both_sides():
    entry = {
        "match": "all",
        "conditions": [
            {"left": {"indicator": "trend_ema_fast"}, "op": "crosses_above", "right": {"indicator": "trend_ema_slow"}}
        ],
    }
    spec = validate_spec(_spec(entry=entry))
    assert crossover_columns(spec) == {"trend_ema_fast", "trend_ema_slow"}


# --------------------------------------------------------------------------- #
#  Evaluation                                                                  #
# --------------------------------------------------------------------------- #


def _row(**values):
    return pd.Series(values)


def _one(op, left_value, right, prev_left=None, prev_right=None):
    """Build and evaluate a single condition against a synthetic row."""
    right_operand = {"const": right} if isinstance(right, int | float) else {"indicator": right}
    entry = {"match": "all", "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": op, "right": right_operand}]}
    spec = validate_spec(_spec(entry=entry))

    values = {"momentum_rsi": left_value}
    if prev_left is not None:
        values[PREV_PREFIX + "momentum_rsi"] = prev_left
    if isinstance(right, str):
        values[right] = 0.0
        if prev_right is not None:
            values[PREV_PREFIX + right] = prev_right
    return evaluate_condition(spec.entry.conditions[0], _row(**values))


@pytest.mark.parametrize(
    ("op", "value", "threshold", "expected"),
    [
        ("<", 25.0, 30.0, True),
        ("<", 35.0, 30.0, False),
        ("<=", 30.0, 30.0, True),
        (">", 71.0, 70.0, True),
        (">=", 70.0, 70.0, True),
        (">", 69.0, 70.0, False),
    ],
)
def test_comparisons(op, value, threshold, expected):
    assert _one(op, value, threshold) is expected


def test_crosses_above_needs_the_previous_bar_below():
    assert _one("crosses_above", 32.0, 30.0, prev_left=28.0) is True
    # Already above on the previous bar: that is not a cross, it is a state.
    assert _one("crosses_above", 32.0, 30.0, prev_left=31.0) is False


def test_crosses_below_mirrors_it():
    assert _one("crosses_below", 28.0, 30.0, prev_left=32.0) is True
    assert _one("crosses_below", 28.0, 30.0, prev_left=29.0) is False


def test_a_cross_without_a_previous_bar_is_false():
    """The first evaluated bar has no predecessor, so no cross can be claimed."""
    assert _one("crosses_above", 32.0, 30.0, prev_left=None) is False


def test_nan_inputs_make_a_condition_false():
    """Warmup leaves NaNs; a strategy must not fire on undefined inputs."""
    assert _one("<", float("nan"), 30.0) is False


def test_infinite_inputs_make_a_condition_false():
    assert _one("<", math.inf, 30.0) is False


def test_a_missing_column_makes_a_condition_false():
    entry = {"match": "all", "conditions": [{"left": {"indicator": "trend_stc"}, "op": "<", "right": {"const": 30}}]}
    spec = validate_spec(_spec(entry=entry))
    assert evaluate_condition(spec.entry.conditions[0], _row(momentum_rsi=10.0)) is False


def test_match_all_versus_any():
    conditions = [
        {"left": {"indicator": "momentum_rsi"}, "op": "<", "right": {"const": 30}},
        {"left": {"indicator": "trend_adx"}, "op": ">", "right": {"const": 25}},
    ]
    row = _row(momentum_rsi=20.0, trend_adx=10.0)  # first true, second false

    all_spec = validate_spec(_spec(entry={"match": "all", "conditions": conditions}))
    any_spec = validate_spec(_spec(entry={"match": "any", "conditions": conditions}))

    assert decide(all_spec, row) == 0
    assert decide(any_spec, row) == 1


# --------------------------------------------------------------------------- #
#  decide()                                                                    #
# --------------------------------------------------------------------------- #


def test_decide_returns_buy_hold_and_sell():
    spec = validate_spec(_spec())
    assert decide(spec, _row(momentum_rsi=20.0)) == 1
    assert decide(spec, _row(momentum_rsi=50.0)) == 0
    assert decide(spec, _row(momentum_rsi=80.0)) == -1


def test_a_bar_that_is_both_entry_and_exit_is_a_hold():
    """A contradictory bar carries no information; silently preferring one side
    would hide the contradiction from the user."""
    entry = {"match": "all", "conditions": [{"left": {"indicator": "trend_adx"}, "op": ">", "right": {"const": 10}}]}
    exit_ = {"match": "all", "conditions": [{"left": {"indicator": "trend_adx"}, "op": ">", "right": {"const": 5}}]}
    spec = validate_spec(_spec(entry=entry, exit_=exit_))

    assert decide(spec, _row(trend_adx=20.0)) == 0
