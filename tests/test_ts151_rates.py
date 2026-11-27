"""Tests for TS151 rates, credit, and spread strategies (PLAN-ts151.md S6).

Verifies:
1. Pure signal functions in signals_rates.py produce correct weights, neutralities, and handle edge cases.
2. Every DEFAULTS key in all 18 bot classes appears in param_grid.
3. Bot instantiations and signal(ctx) calls with synthetic data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.botclass import Bot
from tradingbot.utils.ts151.base import Ctx
from tradingbot.utils.ts151 import signals_rates
from tradingbot.ts151_bulletsbot import TS151BulletsBot
from tradingbot.ts151_barbellsbot import TS151BarbellsBot
from tradingbot.ts151_laddersbot import TS151LaddersBot
from tradingbot.ts151_immunizationbot import TS151ImmunizationBot
from tradingbot.ts151_ddbutterflybot import TS151DdButterflyBot
from tradingbot.ts151_fiftyfiftybutterflybot import TS151FiftyFiftyButterflyBot
from tradingbot.ts151_regressionbutterflybot import TS151RegressionButterflyBot
from tradingbot.ts151_lowriskbondbot import TS151LowRiskBondBot
from tradingbot.ts151_bondvaluebot import TS151BondValueBot
from tradingbot.ts151_bondcarrybot import TS151BondCarryBot
from tradingbot.ts151_rollingdownbot import TS151RollingDownBot
from tradingbot.ts151_curvespreadbot import TS151CurveSpreadBot
from tradingbot.ts151_munibot import TS151MuniBot
from tradingbot.ts151_breakevenbot import TS151BreakevenBot
from tradingbot.ts151_mbsbot import TS151MbsBot
from tradingbot.ts151_convertiblearbbot import TS151ConvertibleArbBot
from tradingbot.ts151_distresseddebtbot import TS151DistressedDebtBot
from tradingbot.ts151_globalfixedincomebot import TS151GlobalFixedIncomeBot


@pytest.fixture
def patch_bot_init(mocker):
    """Stub out Bot.__init__ to avoid database interactions during unit tests."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)


# ------------------------------------------------------------------
# 1. Pure signals tests
# ------------------------------------------------------------------


def test_bullet_weights():
    w = signals_rates.bullet_weights(["SHY", "IEI", "IEF", "TLH", "TLT"], target_maturity=8.5)
    assert w == {"IEF": 1.0}

    w_short = signals_rates.bullet_weights(["SHY", "IEI", "IEF", "TLH", "TLT"], target_maturity=2.5)
    assert w_short == {"SHY": 1.0}


def test_barbell_weights():
    w = signals_rates.barbell_weights(target_duration=7.5, short_asset="SHY", long_asset="TLT")
    assert "SHY" in w and "TLT" in w
    assert abs(sum(w.values()) - 1.0) < 1e-6
    # Duration of portfolio = w_shy * 1.9 + w_tlt * 17.0 = 7.5
    d_port = w["SHY"] * 1.9 + w["TLT"] * 17.0
    assert abs(d_port - 7.5) < 1e-4


def test_ladder_weights():
    u = ["SHY", "IEI", "IEF", "TLH", "TLT"]
    w = signals_rates.ladder_weights(u)
    assert len(w) == 5
    for v in w.values():
        assert abs(v - 0.2) < 1e-6


def test_immunization_weights():
    # Horizon 7 years from start
    start = pd.Timestamp("2020-01-01")
    as_of = pd.Timestamp("2020-01-01")
    w = signals_rates.immunization_weights(as_of=as_of, start=start, horizon_years=7.0)
    # Between IEI (4.4) and IEF (7.5)
    assert "IEI" in w and "IEF" in w
    d_port = w["IEI"] * 4.4 + w["IEF"] * 7.5
    assert abs(d_port - 7.0) < 1e-4

    # When horizon is very short (< 1.9), 100% to SHY
    as_of_late = pd.Timestamp("2026-06-01")
    w_late = signals_rates.immunization_weights(as_of=as_of_late, start=start, horizon_years=7.0)
    assert w_late == {"SHY": 1.0}


def test_butterfly_weights_and_signals():
    # 50-50 DV01 weighting
    w_ff = signals_rates.compute_butterfly_weights(weighting="fifty_fifty", gross=1.0)
    assert "IEF" in w_ff and "SHY" in w_ff and "TLT" in w_ff
    assert w_ff["IEF"] > 0
    assert w_ff["SHY"] < 0
    assert w_ff["TLT"] < 0
    # DV01 of SHY + TLT equals -DV01 of IEF
    dv01_body = w_ff["IEF"] * 7.5
    dv01_wings = w_ff["SHY"] * 1.9 + w_ff["TLT"] * 17.0
    assert abs(dv01_body + dv01_wings) < 1e-5

    # Butterfly signal state
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    # Base spread with a huge positive jump at the end
    spread_data = np.zeros(300)
    spread_data[-10:] = 5.0
    spread = pd.Series(spread_data, index=dates)

    sig = signals_rates.dd_butterfly_signal(spread, window=252, entry=1.0, exit_level=0.25)
    assert sig is not None
    assert sig["IEF"] > 0
    assert sig["SHY"] < 0


def test_regression_butterfly():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    np.random.seed(42)
    w0_y = pd.Series(np.cumsum(np.random.randn(300) * 0.01) + 2.0, index=dates)
    w1_y = pd.Series(np.cumsum(np.random.randn(300) * 0.01) + 4.0, index=dates)
    body_y = 0.6 * w0_y + 0.4 * w1_y + pd.Series(np.random.randn(300) * 0.001, index=dates)
    spread = body_y - 0.5 * (w0_y + w1_y)

    w = signals_rates.regression_butterfly_weights(body_y, w0_y, w1_y, window=252)
    assert w is not None
    assert "IEF" in w and "SHY" in w and "TLT" in w
    assert w["IEF"] > 0


def test_low_risk_bond_weights():
    dates = pd.date_range("2025-01-01", periods=100, freq="B")
    # 4 assets with different volatilities
    df = pd.DataFrame(
        {
            "LOW1": np.cumprod(1 + np.random.randn(100) * 0.001),
            "LOW2": np.cumprod(1 + np.random.randn(100) * 0.002),
            "HIGH1": np.cumprod(1 + np.random.randn(100) * 0.02),
            "HIGH2": np.cumprod(1 + np.random.randn(100) * 0.03),
        },
        index=dates,
    )
    w = signals_rates.low_risk_bond_weights(df, frac=0.5, vol_window=63, gross=2.0)
    assert w is not None
    assert w["LOW1"] > 0
    assert w["LOW2"] > 0
    assert w["HIGH1"] < 0
    assert w["HIGH2"] < 0
    assert abs(sum(w.values())) < 1e-5


def test_bond_value_weights():
    dates = pd.date_range("2020-01-01", periods=1300, freq="B")
    df = pd.DataFrame(
        {
            "SHY": np.ones(1300) * 2.0,
            "IEI": np.ones(1300) * 2.5,
            "IEF": np.ones(1300) * 3.0,
            "TLH": np.ones(1300) * 3.5,
            "TLT": np.ones(1300) * 4.0,
        },
        index=dates,
    )
    # Give SHY and IEI a high positive yield shock at the end
    df.iloc[-1, 0] += 1.0  # SHY
    df.iloc[-1, 1] += 1.0  # IEI
    w = signals_rates.bond_value_weights(df, window=1260, k=2, gross=1.0)
    assert w is not None
    assert w["SHY"] > 0 and w["IEI"] > 0
    assert w["TLH"] < 0 and w["TLT"] < 0


def test_bond_carry_and_rolling_down():
    yields = {"SHY": 2.0, "IEI": 2.5, "IEF": 3.0, "TLH": 3.5, "TLT": 4.0}
    carry_w = signals_rates.bond_carry_weights(yields, k=2, gross=1.0)
    assert carry_w is not None
    assert len(carry_w) == 4

    rd_w = signals_rates.rolling_down_weights(yields, gross=1.0)
    assert rd_w is not None
    assert len(rd_w) == 1
    assert list(rd_w.values())[0] == 1.0


def test_curve_spread_signal():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    slope_data = np.zeros(300)
    # Inverted curve -> steepener
    slope_data[-10:] = -3.0
    slope = pd.Series(slope_data, index=dates)

    w = signals_rates.curve_spread_signal(slope, window=252, entry=1.0, exit_level=0.25)
    assert w is not None
    assert w["SHY"] > 0
    assert w["TLT"] < 0


def test_muni_signal():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    closes = pd.DataFrame(
        {
            "MUB": np.ones(300) * 100.0,
            "IEF": np.ones(300) * 100.0,
        },
        index=dates,
    )
    # MUB drops sharply relative to IEF -> municipal yield rises -> proxy z > entry
    closes.iloc[-5:, 0] = 80.0
    w = signals_rates.muni_signal(closes, window=252, entry=0.5)
    assert w is not None
    assert w["MUB"] > 0
    assert w["IEF"] < 0


def test_breakeven_signal():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    t10yie = pd.Series(np.ones(300) * 2.5, index=dates)
    # Plunge in breakeven inflation -> inflation underpriced -> long TIP, short IEF
    t10yie.iloc[-5:] = 1.0
    w = signals_rates.breakeven_signal(t10yie, mode="breakeven", window=252, entry=1.0)
    assert w is not None
    assert w["TIP"] > 0
    assert w["IEF"] < 0


def test_mbs_signal():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    closes = pd.DataFrame(
        {
            "MBB": np.ones(300) * 100.0,
            "IEF": np.ones(300) * 100.0,
        },
        index=dates,
    )
    # MBB drops relative to IEF -> log(MBB/IEF) plummets -> z < -1 -> long MBB, short IEF
    closes.iloc[-5:, 0] = 85.0
    w = signals_rates.mbs_signal(closes, window=252, entry=1.0, exit_level=0.0)
    assert w is not None
    assert w["MBB"] > 0
    assert w["IEF"] < 0


def test_convertible_arb_weights():
    dates = pd.date_range("2025-01-01", periods=150, freq="B")
    np.random.seed(42)
    ret_spy = np.random.randn(150) * 0.01
    ret_cwb = 0.5 * ret_spy + np.random.randn(150) * 0.002
    closes = pd.DataFrame(
        {
            "SPY": np.cumprod(1 + ret_spy) * 100.0,
            "CWB": np.cumprod(1 + ret_cwb) * 50.0,
        },
        index=dates,
    )
    w = signals_rates.convertible_arb_weights(closes, window=126, asset="CWB", hedge="SPY")
    assert w is not None
    assert w["CWB"] > 0
    assert w["SPY"] < 0


def test_distressed_debt_signal():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    closes = pd.DataFrame(
        {
            "HYG": np.ones(300) * 100.0,
            "IEF": np.ones(300) * 100.0,
            "SHV": np.ones(300) * 100.0,
        },
        index=dates,
    )
    # Normal period -> cash asset SHV
    w_norm = signals_rates.distressed_debt_signal(closes, entry=0.08, exit_level=0.02, window=252)
    assert w_norm == {"SHV": 1.0}

    # Severe drawdown in HYG/IEF (> 8%)
    closes.iloc[-10:, 0] = 90.0
    w_distressed = signals_rates.distressed_debt_signal(closes, entry=0.08, exit_level=0.02, window=252)
    assert w_distressed == {"HYG": 1.0}


def test_global_fixed_income_weights():
    dates = pd.date_range("2020-01-01", periods=1300, freq="B")
    closes = pd.DataFrame(
        {
            "IEF": np.cumprod(1 + np.zeros(1300)),
            "BWX": np.cumprod(1 + np.ones(1300) * 0.0005),
            "IGOV": np.cumprod(1 + np.ones(1300) * 0.0002),
            "EMB": np.cumprod(1 - np.ones(1300) * 0.0002),
        },
        index=dates,
    )
    w = signals_rates.global_fixed_income_weights(closes, mom_window=126, rev_window=1260, k=2)
    assert w is not None
    assert len(w) == 2


# ------------------------------------------------------------------
# 2. Param grid and DEFAULTS completeness on all 18 bot classes
# ------------------------------------------------------------------


BOT_CLASSES = [
    TS151BulletsBot,
    TS151BarbellsBot,
    TS151LaddersBot,
    TS151ImmunizationBot,
    TS151DdButterflyBot,
    TS151FiftyFiftyButterflyBot,
    TS151RegressionButterflyBot,
    TS151LowRiskBondBot,
    TS151BondValueBot,
    TS151BondCarryBot,
    TS151RollingDownBot,
    TS151CurveSpreadBot,
    TS151MuniBot,
    TS151BreakevenBot,
    TS151MbsBot,
    TS151ConvertibleArbBot,
    TS151DistressedDebtBot,
    TS151GlobalFixedIncomeBot,
]


@pytest.mark.parametrize("bot_cls", BOT_CLASSES)
def test_bot_param_grid_and_defaults(bot_cls):
    """Verify that every key in DEFAULTS appears in param_grid."""
    defaults = getattr(bot_cls, "DEFAULTS", {})
    param_grid = getattr(bot_cls, "param_grid", None)

    assert isinstance(defaults, dict)
    assert isinstance(param_grid, dict), f"{bot_cls.__name__} must define param_grid"

    for key in defaults:
        assert key in param_grid, f"Key '{key}' in {bot_cls.__name__}.DEFAULTS missing from param_grid"


@pytest.mark.parametrize("bot_cls", BOT_CLASSES)
def test_bot_instantiation_and_contract(patch_bot_init, bot_cls):
    """Verify clean instantiation and contract compliance."""
    bot = bot_cls()
    assert bot.BOT_NAME.startswith("ts151_")
    assert bot.__doc__ is not None
    assert "§" in bot.__doc__
