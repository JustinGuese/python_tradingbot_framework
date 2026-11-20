"""
Tests for FX carry utilities (tradingbot/utils/fx_carry.py).

Covers PLAN-carryblend.md S0 shared contract:
- signed_topk_weights:
    - sum of longs = gross / 2
    - sum of shorts = -gross / 2
    - gross sum = gross
    - net sum = 0.0 (dollar-neutral)
    - empty dict if fewer than 2*k valid scores
- rate_differentials:
    - foreign minus USD rate for requested currencies
    - error on missing USD or requested currency
- daily_carry_accrual:
    - accrual sign for long vs short positions
    - exact math: sum(w_i * diff_i) / 252
    - empty weights or missing keys handling
- load_fred_rates:
    - no-lookahead lag (shift by 25 days)
    - decimal rates format
    - canonical 10 G10 columns
    - raise on HTTP error or missing series (no silent drop)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.fx_carry import (
    FRED_RATE_SERIES,
    G10_CURRENCIES,
    daily_carry_accrual,
    load_fred_rates,
    rate_differentials,
    signed_topk_weights,
)


class TestSignedTopKWeights:
    def test_basic_properties_k2_gross1(self):
        scores = pd.Series(
            {
                "EUR": 0.02,
                "GBP": 0.01,
                "AUD": 0.04,
                "NZD": 0.05,
                "JPY": -0.02,
                "CHF": -0.01,
            }
        )
        k = 2
        gross = 1.0
        w = signed_topk_weights(scores, k=k, gross=gross)

        longs = {k: v for k, v in w.items() if v > 0}
        shorts = {k: v for k, v in w.items() if v < 0}

        # Longs: top 2 are NZD (0.05) and AUD (0.04)
        assert set(longs.keys()) == {"NZD", "AUD"}
        # Shorts: bottom 2 are JPY (-0.02) and CHF (-0.01)
        assert set(shorts.keys()) == {"JPY", "CHF"}

        # sum of longs = gross / 2
        assert sum(longs.values()) == pytest.approx(gross / 2.0)
        # sum of shorts = -gross / 2
        assert sum(shorts.values()) == pytest.approx(-gross / 2.0)
        # gross sum = gross
        assert sum(abs(v) for v in w.values()) == pytest.approx(gross)
        # net sum = 0.0
        assert sum(w.values()) == pytest.approx(0.0)

    def test_properties_k3_gross1_5(self):
        scores = pd.Series(
            {
                "A": 10.0,
                "B": 8.0,
                "C": 6.0,
                "D": 4.0,
                "E": 2.0,
                "F": 0.0,
                "G": -2.0,
                "H": -4.0,
            }
        )
        k = 3
        gross = 1.5
        w = signed_topk_weights(scores, k=k, gross=gross)

        longs = {k: v for k, v in w.items() if v > 0}
        shorts = {k: v for k, v in w.items() if v < 0}

        assert len(longs) == 3
        assert len(shorts) == 3
        assert set(longs.keys()) == {"A", "B", "C"}
        assert set(shorts.keys()) == {"F", "G", "H"}

        assert sum(longs.values()) == pytest.approx(gross / 2.0)
        assert sum(shorts.values()) == pytest.approx(-gross / 2.0)
        assert sum(abs(v) for v in w.values()) == pytest.approx(gross)
        assert sum(w.values()) == pytest.approx(0.0)

        # Equal weight within side: each long = gross / (2 * k) = 1.5 / 6 = 0.25
        for v in longs.values():
            assert v == pytest.approx(0.25)
        for v in shorts.values():
            assert v == pytest.approx(-0.25)

    def test_empty_if_fewer_than_2k(self):
        # 5 assets with k=3 (needs at least 6) -> returns {}
        scores = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0})
        assert signed_topk_weights(scores, k=3) == {}

        # Exactly 2*k -> returns valid weights
        assert signed_topk_weights(scores, k=2) != {}

        # 6 assets but 2 are NaN -> only 4 valid < 6 -> returns {}
        scores_nan = pd.Series({"A": 1.0, "B": np.nan, "C": 3.0, "D": np.nan, "E": 5.0, "F": 6.0})
        assert signed_topk_weights(scores_nan, k=3) == {}

    def test_invalid_k_returns_empty(self):
        scores = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0})
        assert signed_topk_weights(scores, k=0) == {}
        assert signed_topk_weights(scores, k=-1) == {}


class TestRateDifferentials:
    def test_rate_differentials_calculation(self):
        dates = pd.date_range("2024-01-01", periods=3, freq="D")
        rates = pd.DataFrame(
            {
                "USD": [0.04, 0.04, 0.04],
                "EUR": [0.03, 0.035, 0.04],
                "JPY": [0.00, 0.005, 0.01],
                "AUD": [0.05, 0.05, 0.055],
            },
            index=dates,
        )

        diffs = rate_differentials(rates, ["EUR", "JPY", "AUD"])
        assert list(diffs.columns) == ["EUR", "JPY", "AUD"]

        # EUR - USD: 0.03 - 0.04 = -0.01
        assert diffs.loc["2024-01-01", "EUR"] == pytest.approx(-0.01)
        # JPY - USD: 0.00 - 0.04 = -0.04
        assert diffs.loc["2024-01-01", "JPY"] == pytest.approx(-0.04)
        # AUD - USD: 0.05 - 0.04 = +0.01
        assert diffs.loc["2024-01-01", "AUD"] == pytest.approx(+0.01)

    def test_missing_usd_raises(self):
        rates = pd.DataFrame({"EUR": [0.03], "GBP": [0.04]})
        with pytest.raises(ValueError, match="must contain 'USD'"):
            rate_differentials(rates, ["EUR"])

    def test_missing_requested_currency_raises(self):
        rates = pd.DataFrame({"USD": [0.04], "EUR": [0.03]})
        with pytest.raises(ValueError, match="missing from rates"):
            rate_differentials(rates, ["EUR", "CHF"])


class TestDailyCarryAccrual:
    def test_accrual_sign_long_and_short(self):
        # Case 1: Long high-yield currency (diff > 0) -> positive accrual
        w_long_pos = {"AUD": 0.5}
        diff_pos = pd.Series({"AUD": 0.02})
        assert daily_carry_accrual(w_long_pos, diff_pos) > 0.0
        assert daily_carry_accrual(w_long_pos, diff_pos) == pytest.approx((0.5 * 0.02) / 252.0)

        # Case 2: Long low-yield currency (diff < 0) -> negative accrual (pays carry)
        w_long_neg = {"JPY": 0.5}
        diff_neg = pd.Series({"JPY": -0.03})
        assert daily_carry_accrual(w_long_neg, diff_neg) < 0.0
        assert daily_carry_accrual(w_long_neg, diff_neg) == pytest.approx((0.5 * -0.03) / 252.0)

        # Case 3: Short low-yield currency (diff < 0) -> positive accrual (earns carry!)
        w_short_neg = {"JPY": -0.5}
        assert daily_carry_accrual(w_short_neg, diff_neg) > 0.0
        assert daily_carry_accrual(w_short_neg, diff_neg) == pytest.approx((-0.5 * -0.03) / 252.0)

        # Case 4: Short high-yield currency (diff > 0) -> negative accrual (pays carry!)
        w_short_pos = {"AUD": -0.5}
        assert daily_carry_accrual(w_short_pos, diff_pos) < 0.0
        assert daily_carry_accrual(w_short_pos, diff_pos) == pytest.approx((-0.5 * 0.02) / 252.0)

    def test_canonical_carry_trade_earns_positive(self):
        # Long top-1 (AUD diff +2%) / Short bottom-1 (JPY diff -3%)
        weights = {"AUD": 0.5, "JPY": -0.5}
        diffs = pd.Series({"AUD": 0.02, "JPY": -0.03})
        accrual = daily_carry_accrual(weights, diffs)

        expected = (0.5 * 0.02 + (-0.5) * (-0.03)) / 252.0  # (0.01 + 0.015) / 252 = 0.025 / 252
        assert accrual > 0.0
        assert accrual == pytest.approx(expected)

    def test_reverse_carry_trade_pays_negative(self):
        # Reverse carry: Long JPY / Short AUD
        weights = {"AUD": -0.5, "JPY": 0.5}
        diffs = pd.Series({"AUD": 0.02, "JPY": -0.03})
        accrual = daily_carry_accrual(weights, diffs)

        expected = (-0.5 * 0.02 + 0.5 * (-0.03)) / 252.0
        assert accrual < 0.0
        assert accrual == pytest.approx(expected)

    def test_empty_weights_or_zero_weights(self):
        diffs = pd.Series({"AUD": 0.02, "JPY": -0.03})
        assert daily_carry_accrual({}, diffs) == 0.0
        assert daily_carry_accrual({"AUD": 0.0}, diffs) == 0.0

    def test_missing_held_asset_raises_keyerror(self):
        weights = {"AUD": 0.5, "CHF": -0.5}
        diffs = pd.Series({"AUD": 0.02})
        with pytest.raises(KeyError, match="Missing differential for held asset 'CHF'"):
            daily_carry_accrual(weights, diffs)


class TestLoadFredRates:
    def test_load_fred_rates_lag_shift_25(self):
        """Verify no-lookahead lag: rates(lag=25) is shifted by exactly 25 days relative to rates(lag=0)."""
        rates_0 = load_fred_rates(lag_days=0)
        rates_25 = load_fred_rates(lag_days=25)

        # Both have same columns
        assert list(rates_0.columns) == G10_CURRENCIES
        assert list(rates_25.columns) == G10_CURRENCIES
        assert rates_0.index.equals(rates_25.index)

        # First 25 rows of lagged series must be NaN
        assert rates_25.iloc[:25].isna().all().all()

        # From day 25 onwards, rates_25[t] must equal rates_0[t - 25]
        np.testing.assert_allclose(
            rates_25.iloc[25:].values,
            rates_0.iloc[:-25].values,
            rtol=1e-7,
            atol=1e-7,
        )

    def test_load_fred_rates_columns_and_decimals(self):
        """Rates must be in decimal form (e.g. 0.04 for 4%) across all G10 currencies."""
        rates = load_fred_rates(lag_days=25)
        expected_cols = ["EUR", "GBP", "AUD", "NZD", "JPY", "CAD", "CHF", "NOK", "SEK", "USD"]
        assert list(rates.columns) == expected_cols
        assert list(FRED_RATE_SERIES.keys()) == expected_cols

        # Check tail non-NaN rates are reasonable decimals (between -0.05 and 0.25)
        tail_rates = rates.dropna().tail(100)
        assert (tail_rates > -0.05).all().all()
        assert (tail_rates < 0.25).all().all()

    def test_load_fred_rates_raises_on_fetch_failure(self, monkeypatch, tmp_path):
        """If fetch fails or series missing and cache is empty, must raise (no synthetic fallback)."""
        import requests

        class FailingResponse:
            status_code = 500
            text = "Internal Server Error"

        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: FailingResponse())

        with pytest.raises(RuntimeError, match="FRED returned HTTP 500"):
            load_fred_rates(cache_dir=tmp_path, refresh=True)
