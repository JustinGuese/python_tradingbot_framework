"""
Tests for SignedFxCarryBot (PLAN-carryblend.md S2).

Verifies:
1. Bot configuration (ALLOW_SHORT=True, SHORTABLE=ALL_FX_PAIRS, CARRY_ACCRUAL=True, param_grid, tickers).
2. targetWeights sums:
   - sum of longs == -sum of shorts == gross / 2
   - gross sum == gross
   - net sum == 0.0 (dollar-neutral)
3. Negative weights only assigned to SHORTABLE symbols.
4. Inverted pairs receive reversed sign for foreign carry:
   - When foreign yield > USD yield (diff > 0):
     - Direct pair (e.g. EURUSD=X) is LONG (+).
     - Inverted pair (e.g. USDJPY=X) is SHORT (-).
   - When foreign yield < USD yield (diff < 0):
     - Direct pair is SHORT (-).
     - Inverted pair is LONG (+).
5. History too short returns {}:
   - fewer than 2 bars
   - empty dataframe
   - fewer than 2*k valid tickers / differentials
6. Monthly rebalance gate:
   - rebalances on first bar of the calendar month
   - returns None mid-month when holdings are already present
   - builds book when flat live
7. carry_rates logic:
   - returns accurate diff for direct and inverted pairs
   - deducts markup properly for longs and shorts
   - includes "USD" rate for cash collateral credit
8. backtest_bot integration:
   - runs cleanly in backtest_bot with carry accrual and short positions.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from tradingbot.signedfxcarrybot import (
    ALL_FX_PAIRS,
    SignedFxCarryBot,
    compute_signed_fx_carry_weights,
)
from tradingbot.utils.botclass import Bot

LIVE_ROWS = {"EURUSD=X": pd.Series({"timestamp": pd.Timestamp("2024-08-01"), "close": 1.10})}


@pytest.fixture
def make_rates_df():
    """Build a synthetic rates DataFrame with known short rates."""
    idx = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    # Base USD at 5.0%
    data = {
        "USD": 0.050,
        "EUR": 0.035,  # diff: -0.015 (lower than USD)
        "GBP": 0.045,  # diff: -0.005 (lower than USD)
        "AUD": 0.065,  # diff: +0.015 (higher than USD)
        "NZD": 0.070,  # diff: +0.020 (higher than USD)
        "JPY": 0.005,  # diff: -0.045 (much lower than USD)
        "CAD": 0.040,  # diff: -0.010 (lower than USD)
        "CHF": 0.010,  # diff: -0.040 (much lower than USD)
        "NOK": 0.060,  # diff: +0.010 (higher than USD)
        "SEK": 0.030,  # diff: -0.020 (lower than USD)
    }
    df = pd.DataFrame({k: [v] * len(idx) for k, v in data.items()}, index=idx)
    return df


@pytest.fixture
def make_fx_carry_bot(mocker, make_rates_df):
    """Build a SignedFxCarryBot with DB initialization stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _factory(
        k: int = 3,
        gross: float = 1.0,
        markup: float = 0.0,
        rates: pd.DataFrame | None = None,
    ):
        actual_rates = rates if rates is not None else make_rates_df
        bot = SignedFxCarryBot(k=k, gross=gross, markup=markup, rates=actual_rates)
        bot.bot_name = "SignedFxCarryBot"
        bot.tickers = list(ALL_FX_PAIRS)
        bot.interval = "1d"
        bot.period = "max"
        bot.k = k
        bot.gross = gross
        bot.markup = markup
        bot.lag_days = 25
        bot._rates = actual_rates
        bot._current_weights = {}
        bot.datas = {}
        return bot

    return _factory


def _make_daily_fx_data(
    end: str,
    n_bars: int,
    tickers: tuple[str, ...] = ALL_FX_PAIRS,
) -> dict[str, pd.DataFrame]:
    """Create synthetic price data for FX spot pairs ending on `end`."""
    idx = pd.bdate_range(end=end, periods=n_bars)
    datas: dict[str, pd.DataFrame] = {}
    for t in tickers:
        base_price = 150.0 if "JPY" in t else (1.10 if "EUR" in t else 1.0)
        prices = [base_price] * n_bars
        datas[t] = pd.DataFrame(
            {
                "open": prices,
                "high": prices,
                "low": prices,
                "close": prices,
                "volume": 1000.0,
            },
            index=idx,
        )
    return datas


# ------------------------------------------------------------------
# 1. Bot configuration
# ------------------------------------------------------------------


def test_bot_configuration(make_fx_carry_bot):
    """Verify class attributes and defaults."""
    bot = make_fx_carry_bot()
    assert bot.ALLOW_SHORT is True
    assert bot.SHORTABLE == ALL_FX_PAIRS
    assert bot.CARRY_ACCRUAL is True
    assert bot.MAX_GROSS >= 1.5
    assert set(bot.tickers) == set(ALL_FX_PAIRS)
    assert bot.k == 3
    assert bot.gross == 1.0
    assert bot.markup == 0.0

    expected_grid = {
        "k": [2, 3, 4],
        "gross": [1.0, 1.5],
        "markup": [0.0, 0.005, 0.01],
    }
    assert SignedFxCarryBot.param_grid == expected_grid
    assert SignedFxCarryBot.BACKTEST_PERIOD == "max"


# ------------------------------------------------------------------
# 2. Target weights sums & dollar neutrality
# ------------------------------------------------------------------


def test_target_weights_sums_default_k3_gross1(make_fx_carry_bot):
    """Test targetWeights sums with default params (k=3, gross=1.0).

    Verifies:
    - sum of longs == -sum of shorts == gross / 2 == 0.5
    - gross sum == gross == 1.0
    - net sum == 0.0 (dollar-neutral)
    """
    bot = make_fx_carry_bot(k=3, gross=1.0)
    # 2024-08-01 is Thursday and starts a new calendar month (month roll)
    bot.datas = _make_daily_fx_data(end="2024-08-01", n_bars=30)

    weights = bot.targetWeights()
    assert weights is not None
    assert len(weights) == 6  # 3 longs + 3 shorts

    longs = {k: v for k, v in weights.items() if v > 0}
    shorts = {k: v for k, v in weights.items() if v < 0}

    assert len(longs) == 3
    assert len(shorts) == 3

    sum_longs = sum(longs.values())
    sum_shorts = sum(shorts.values())
    gross_sum = sum(abs(v) for v in weights.values())
    net_sum = sum(weights.values())

    assert sum_longs == pytest.approx(0.5, abs=1e-6)
    assert sum_shorts == pytest.approx(-0.5, abs=1e-6)
    assert sum_longs == pytest.approx(-sum_shorts, abs=1e-6)
    assert gross_sum == pytest.approx(1.0, abs=1e-6)
    assert net_sum == pytest.approx(0.0, abs=1e-6)


def test_target_weights_sums_k2_gross1_5(make_fx_carry_bot):
    """Test targetWeights sums with k=2, gross=1.5."""
    bot = make_fx_carry_bot(k=2, gross=1.5)
    bot.datas = _make_daily_fx_data(end="2024-08-01", n_bars=30)

    weights = bot.targetWeights()
    assert weights is not None
    assert len(weights) == 4  # 2 longs + 2 shorts

    longs = {k: v for k, v in weights.items() if v > 0}
    shorts = {k: v for k, v in weights.items() if v < 0}

    assert len(longs) == 2
    assert len(shorts) == 2

    assert sum(longs.values()) == pytest.approx(0.75, abs=1e-6)
    assert sum(shorts.values()) == pytest.approx(-0.75, abs=1e-6)
    assert sum(weights.values()) == pytest.approx(0.0, abs=1e-6)
    assert sum(abs(v) for v in weights.values()) == pytest.approx(1.5, abs=1e-6)

    # Each long is 1.5 / 4 = 0.375, each short is -0.375
    for v in longs.values():
        assert v == pytest.approx(0.375, abs=1e-6)
    for v in shorts.values():
        assert v == pytest.approx(-0.375, abs=1e-6)


# ------------------------------------------------------------------
# 3. Shortable enforcement
# ------------------------------------------------------------------


def test_only_shortable_symbols_receive_negative_weights(make_fx_carry_bot):
    """Verify that any symbol assigned a negative weight is strictly in SHORTABLE."""
    bot = make_fx_carry_bot(k=3, gross=1.0)
    bot.datas = _make_daily_fx_data(end="2024-08-01", n_bars=30)

    weights = bot.targetWeights()
    assert weights is not None

    short_symbols = [s for s, w in weights.items() if w < 0]
    assert len(short_symbols) > 0
    for sym in short_symbols:
        assert sym in bot.SHORTABLE


# ------------------------------------------------------------------
# 4. Inversion logic: reversed sign for inverted pairs
# ------------------------------------------------------------------


def test_inverted_pairs_receive_reversed_sign_for_foreign_carry():
    """
    Test directional logic and inversion:
    - Direct pairs: score = r_foreign - r_usd.
      When r_foreign > r_usd, buying the pair earns positive carry -> LONG weight.
    - Inverted pairs (base USD, quote foreign): score = r_usd - r_foreign.
      When r_foreign < r_usd (low-yielding foreign ccy like JPY, CHF):
        r_usd - r_foreign > 0 -> LONG USD / SHORT foreign -> LONG the pair (positive weight).
      When r_foreign > r_usd (high-yielding foreign ccy):
        r_usd - r_foreign < 0 -> SHORT USD / LONG foreign -> SHORT the pair (negative weight).
    """
    # Scenario 1: USD = 5.0%, JPY = 0.5% (low yield), NZD = 7.0% (high yield)
    rates_row = pd.Series(
        {
            "USD": 0.050,
            "NZD": 0.070,  # Direct: diff = +0.020 -> score = +0.020
            "AUD": 0.065,  # Direct: diff = +0.015 -> score = +0.015
            "GBP": 0.045,  # Direct: diff = -0.005 -> score = -0.005
            "EUR": 0.035,  # Direct: diff = -0.015 -> score = -0.015
            "JPY": 0.005,  # Inverted: diff = -0.045 -> score = r_usd - r_jpy = +0.045
            "CHF": 0.010,  # Inverted: diff = -0.040 -> score = r_usd - r_chf = +0.040
            "SEK": 0.030,  # Inverted: diff = -0.020 -> score = r_usd - r_sek = +0.020
            "CAD": 0.040,  # Inverted: diff = -0.010 -> score = r_usd - r_cad = +0.010
            "NOK": 0.080,  # Inverted: diff = +0.030 -> score = r_usd - r_nok = -0.030 (high foreign yield!)
        }
    )

    weights = compute_signed_fx_carry_weights(rates_row, k=2, gross=1.0)
    assert weights != {}

    # USDJPY=X is inverted: JPY has low rate (0.5%), so USDJPY score is +4.5% (highest score).
    # Buying USDJPY goes long USD and short JPY, earning positive carry.
    assert weights["USDJPY=X"] > 0, "Low-yield foreign ccy on inverted pair must receive positive weight (long pair)"

    # USDCHF=X is inverted: CHF has low rate (1.0%), so USDCHF score is +4.0% (second highest).
    assert weights["USDCHF=X"] > 0, "Low-yield foreign ccy on inverted pair must receive positive weight (long pair)"

    # USDNOK=X is inverted: NOK has high rate (8.0%), so USDNOK score is -3.0% (lowest score).
    # Shorting USDNOK goes short USD and long NOK, earning positive carry.
    assert weights["USDNOK=X"] < 0, "High-yield foreign ccy on inverted pair must receive negative weight (short pair)"

    # EURUSD=X is direct: EUR has low rate (3.5% < 5.0%), so EURUSD score is -1.5% (second lowest).
    # Shorting EURUSD goes short EUR and long USD.
    assert weights["EURUSD=X"] < 0, "Low-yield foreign ccy on direct pair must receive negative weight (short pair)"


def test_inverted_pair_reversal_sign_direct_vs_inverted():
    """Compare direct and inverted pairs when both have higher or lower yield than USD."""
    # When both EUR (direct) and JPY (inverted) yield 8.0% while USD yields 4.0%:
    rates_high_foreign = pd.Series(
        {
            "USD": 0.04,
            "EUR": 0.08,  # EUR - USD = +0.04 -> Direct pair LONG
            "GBP": 0.07,
            "AUD": 0.06,
            "NZD": 0.05,
            "JPY": 0.08,  # USD - JPY = -0.04 -> Inverted pair SHORT (reversed sign!)
            "CAD": 0.03,
            "CHF": 0.02,
            "NOK": 0.01,
            "SEK": 0.01,
        }
    )

    w_high = compute_signed_fx_carry_weights(rates_high_foreign, k=2, gross=1.0)
    # EURUSD is direct with positive foreign carry -> LONG
    assert w_high["EURUSD=X"] > 0
    # USDJPY is inverted with positive foreign carry -> SHORT (reversed sign!)
    assert w_high["USDJPY=X"] < 0


# ------------------------------------------------------------------
# 5. History too short returns {}
# ------------------------------------------------------------------


def test_fewer_than_two_bars_returns_empty(make_fx_carry_bot):
    """Verify that {} is returned when history has fewer than 2 bars."""
    bot = make_fx_carry_bot(k=3)

    # 1 bar
    bot.datas = _make_daily_fx_data(end="2024-08-01", n_bars=1)
    assert bot.targetWeights() == {}

    # Empty datas
    bot.datas = {}
    assert bot.targetWeights() == {}


def test_fewer_than_2k_tickers_returns_empty(make_fx_carry_bot):
    """Verify {} is returned when fewer than 2*k tickers are available."""
    bot = make_fx_carry_bot(k=3)  # needs 2*k = 6 tickers

    # Provide only 4 tickers with sufficient bars
    four_tickers = ("EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCAD=X")
    bot.datas = _make_daily_fx_data(end="2024-08-01", n_bars=30, tickers=four_tickers)
    assert bot.targetWeights() == {}


def test_missing_usd_rate_returns_empty():
    """Verify compute_signed_fx_carry_weights returns {} when USD rate is missing."""
    rates_no_usd = pd.Series({"EUR": 0.03, "JPY": 0.01})
    assert compute_signed_fx_carry_weights(rates_no_usd, k=2) == {}


# ------------------------------------------------------------------
# 6. Monthly rebalance gate
# ------------------------------------------------------------------


def test_monthly_rebalance_mid_month_with_holdings_returns_none(make_fx_carry_bot):
    """Mid-month bar with existing live holdings returns None (no rebalance)."""
    bot = make_fx_carry_bot(k=3)
    # 2024-07-15 is mid-month (Monday)
    bot.datas = _make_daily_fx_data(end="2024-07-15", n_bars=30)

    with patch("tradingbot.utils.live_state.current_weights", return_value={"EURUSD=X": 0.25}):
        res = bot.targetWeights(LIVE_ROWS)
        assert res is None


def test_monthly_rebalance_builds_when_flat_live(make_fx_carry_bot):
    """Mid-month bar builds the book immediately when live portfolio is flat."""
    bot = make_fx_carry_bot(k=3)
    # 2024-07-15 is mid-month
    bot.datas = _make_daily_fx_data(end="2024-07-15", n_bars=30)

    with patch("tradingbot.utils.live_state.current_weights", return_value={}):
        res = bot.targetWeights(LIVE_ROWS)
        assert res is not None
        assert len(res) == 6


# ------------------------------------------------------------------
# 7. Carry rates logic & markup
# ------------------------------------------------------------------


def test_carry_rates_direct_and_inverted_zero_markup(make_fx_carry_bot):
    """Verify carry_rates returns exact differentials and USD rate at markup=0."""
    rates_df = pd.DataFrame(
        {
            "USD": [0.05],
            "EUR": [0.03],  # Direct: diff = 0.03 - 0.05 = -0.02
            "GBP": [0.04],  # Direct: diff = 0.04 - 0.05 = -0.01
            "AUD": [0.06],  # Direct: diff = 0.06 - 0.05 = +0.01
            "NZD": [0.07],  # Direct: diff = 0.07 - 0.05 = +0.02
            "JPY": [0.01],  # Inverted: diff = 0.05 - 0.01 = +0.04
            "CAD": [0.04],  # Inverted: diff = 0.05 - 0.04 = +0.01
            "CHF": [0.01],  # Inverted: diff = 0.05 - 0.01 = +0.04
            "NOK": [0.08],  # Inverted: diff = 0.05 - 0.08 = -0.03
            "SEK": [0.02],  # Inverted: diff = 0.05 - 0.02 = +0.03
        },
        index=[pd.Timestamp("2024-08-01")],
    )

    bot = make_fx_carry_bot(k=3, markup=0.0, rates=rates_df)
    rates = bot.carry_rates(pd.Timestamp("2024-08-01"))

    assert rates["USD"] == pytest.approx(0.05)
    # Direct pair EURUSD=X: 0.03 - 0.05 = -0.02
    assert rates["EURUSD=X"] == pytest.approx(-0.02)
    # Direct pair NZDUSD=X: 0.07 - 0.05 = +0.02
    assert rates["NZDUSD=X"] == pytest.approx(+0.02)
    # Inverted pair USDJPY=X: 0.05 - 0.01 = +0.04
    assert rates["USDJPY=X"] == pytest.approx(+0.04)
    # Inverted pair USDNOK=X: 0.05 - 0.08 = -0.03
    assert rates["USDNOK=X"] == pytest.approx(-0.03)


def test_carry_rates_with_markup_deduction(make_fx_carry_bot):
    """Verify swap markup is deducted from earned return for both long and short positions."""
    rates_df = pd.DataFrame(
        {
            "USD": [0.05],
            "EUR": [0.03],  # diff = -0.02
            "NZD": [0.07],  # diff = +0.02
            "GBP": [0.05],
            "AUD": [0.05],
            "JPY": [0.01],  # diff = +0.04
            "CAD": [0.05],
            "CHF": [0.05],
            "NOK": [0.08],  # diff = -0.03
            "SEK": [0.05],
        },
        index=[pd.Timestamp("2024-08-01")],
    )

    markup = 0.005  # 50 bps
    bot = make_fx_carry_bot(k=2, markup=markup, rates=rates_df)

    # Simulate held weights:
    # NZDUSD is LONG (w > 0, diff = +0.02) -> rate = diff - markup = +0.015
    # USDJPY is LONG (w > 0, diff = +0.04) -> rate = diff - markup = +0.035
    # EURUSD is SHORT (w < 0, diff = -0.02) -> rate = diff + markup = -0.015
    # USDNOK is SHORT (w < 0, diff = -0.03) -> rate = diff + markup = -0.025
    bot._current_weights = {
        "NZDUSD=X": 0.25,
        "USDJPY=X": 0.25,
        "EURUSD=X": -0.25,
        "USDNOK=X": -0.25,
    }

    rates = bot.carry_rates(pd.Timestamp("2024-08-01"))

    assert rates["NZDUSD=X"] == pytest.approx(0.02 - markup)
    assert rates["USDJPY=X"] == pytest.approx(0.04 - markup)
    assert rates["EURUSD=X"] == pytest.approx(-0.02 + markup)
    assert rates["USDNOK=X"] == pytest.approx(-0.03 + markup)

    # In backtest:
    # Long holding (+100): 100 * price * (+0.015) > 0 (earns +1.5% net of markup)
    # Short holding (-100): (-100) * price * (-0.015) > 0 (earns +1.5% net of markup)
    price = 100.0
    long_accrual = 100.0 * price * rates["NZDUSD=X"]
    short_accrual = (-100.0) * price * rates["EURUSD=X"]
    assert long_accrual > 0
    assert short_accrual > 0
    assert long_accrual == pytest.approx(short_accrual)


# ------------------------------------------------------------------
# 8. backtest_bot integration
# ------------------------------------------------------------------


def test_backtest_bot_with_signedfxcarrybot(make_fx_carry_bot):
    """Verify SignedFxCarryBot executes cleanly inside backtest_bot with carry accrual."""
    from tradingbot.utils.backtest import backtest_bot

    bot = make_fx_carry_bot(k=2, gross=1.0, markup=0.0)
    bot.benchmark_tickers = []
    bot.symbol = None
    bot.data = None
    bot.params = {}
    bot.LIQUIDATE_UNTRACKED = False

    # 60 business days of synthetic data spanning a month roll
    datas = _make_daily_fx_data(end="2024-08-01", n_bars=60)

    res = backtest_bot(
        bot,
        data=datas,
        initial_capital=10_000.0,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=False,
        return_series=True,
    )
    assert res is not None
    assert "yearly_return" in res
    assert res["nrtrades"] > 0
    # Portfolio equity should be positive and close to initial capital
    assert res["equity_curve"][-1]["v"] > 5000.0
