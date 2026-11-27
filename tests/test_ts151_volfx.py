"""Unit tests for TS151 Volatility, Index, and FX strategies (PLAN-ts151 S7).

Tests §6.5, 7.2, 7.3, 7.6, 8.1-8.4:
- Volatility Targeting (ts151_voltargetbot)
- VIX Basis (ts151_vixbasisbot)
- Volatility Carry (ts151_volcarrybot)
- Variance Swap / VRP (ts151_varianceswapbot)
- FX Moving Average (ts151_fxmabot)
- FX Carry (ts151_fxcarrybot)
- Dollar Carry (ts151_dollarcarrybot)
- FX Momentum + Carry Blend (ts151_fxmomcarrybot)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.ts151_dollarcarrybot import TS151DollarCarryBot
from tradingbot.ts151_fxcarrybot import TS151FxCarryBot
from tradingbot.ts151_fxmabot import TS151FxMaBot
from tradingbot.ts151_fxmomcarrybot import TS151FxMomCarryBot
from tradingbot.ts151_varianceswapbot import TS151VarianceSwapBot
from tradingbot.ts151_vixbasisbot import TS151VixBasisBot
from tradingbot.ts151_volcarrybot import TS151VolCarryBot
from tradingbot.ts151_voltargetbot import TS151VolTargetBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.ts151.base import Ctx
from tradingbot.utils.ts151.signals_volfx import (
    dollar_carry_signal,
    fx_carry_signal,
    fx_ma_signal,
    fx_mom_carry_signal,
    variance_swap_signal,
    vix_basis_signal,
    vol_carry_signal,
    vol_target_signal,
)


@pytest.fixture(autouse=True)
def patch_bot_init(mocker):
    """Avoid live database calls during bot instantiation in tests."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)


# ---------------------------------------------------------------------------
# §6.5 Volatility Targeting
# ---------------------------------------------------------------------------


def test_vol_target_signal_and_bot():
    dates = pd.date_range("2026-01-01", periods=100, freq="B")
    # Low-vol series (~0.05 annualized vol)
    np.random.seed(42)
    daily_rets = np.random.normal(0.0005, 0.005, size=100)
    prices = 100.0 * np.exp(np.cumsum(daily_rets))
    closes = pd.DataFrame({"SPY": prices}, index=dates)

    ctx = Ctx(closes=closes, datas={}, as_of=dates[-1], universe=["SPY"], bot=None)
    w = vol_target_signal(ctx, target=0.15, window=21, cap=1.0, band=0.1)
    assert w is not None
    assert "SPY" in w
    # Target 0.15 with ~0.08 realized vol caps at 1.0
    assert 0.0 < w["SPY"] <= 1.0

    # High-vol series
    daily_rets_high = np.random.normal(0.0005, 0.03, size=100)
    prices_high = 100.0 * np.exp(np.cumsum(daily_rets_high))
    closes_high = pd.DataFrame({"SPY": prices_high}, index=dates)
    ctx_high = Ctx(closes=closes_high, datas={}, as_of=dates[-1], universe=["SPY"], bot=None)
    w_high = vol_target_signal(ctx_high, target=0.15, window=21, cap=1.0, band=0.01)
    assert w_high is not None
    # Annualized vol is ~0.48, so target 0.15 / 0.48 ~ 0.31
    assert w_high["SPY"] < 0.6

    # Test Bot class
    bot = TS151VolTargetBot()
    assert bot.BOT_NAME == "ts151_VolTargetBot"
    assert bot.DEFAULT_UNIVERSE == ("SPY",)
    assert bot.rebalance == "D"
    sig = bot.signal(ctx)
    assert sig is not None
    assert "SPY" in sig


# ---------------------------------------------------------------------------
# §7.2 VIX Basis
# ---------------------------------------------------------------------------


def test_vix_basis_signal_and_bot():
    dates = pd.date_range("2026-01-01", periods=50, freq="B")

    # 1. Contango case (^VIX / ^VIX3M < 0.95)
    vix = pd.Series(15.0, index=dates)
    vix3m = pd.Series(20.0, index=dates)  # ratio = 0.75 < 0.95
    vixy = pd.Series(10.0, index=dates)
    svxy = pd.Series(50.0, index=dates)
    closes_contango = pd.DataFrame({"^VIX": vix, "^VIX3M": vix3m, "VIXY": vixy, "SVXY": svxy}, index=dates)

    ctx_c = Ctx(closes=closes_contango, datas={}, as_of=dates[-1], universe=["VIXY", "SVXY"], bot=None)
    # LS mode -> short VIXY
    w_c = vix_basis_signal(ctx_c, contango=0.95, backwardation=1.05, weight=0.3, long_only=False)
    assert w_c == {"VIXY": -0.3}

    # LO mode -> long SVXY
    w_c_lo = vix_basis_signal(ctx_c, contango=0.95, backwardation=1.05, weight=0.3, long_only=True)
    assert w_c_lo == {"SVXY": 0.3}

    # 2. Backwardation case (^VIX / ^VIX3M > 1.05)
    vix_b = pd.Series(30.0, index=dates)
    vix3m_b = pd.Series(25.0, index=dates)  # ratio = 1.20 > 1.05
    closes_back = pd.DataFrame({"^VIX": vix_b, "^VIX3M": vix3m_b, "VIXY": vixy, "SVXY": svxy}, index=dates)
    ctx_b = Ctx(closes=closes_back, datas={}, as_of=dates[-1], universe=["VIXY", "SVXY"], bot=None)
    w_b = vix_basis_signal(ctx_b, contango=0.95, backwardation=1.05, weight=0.3, long_only=False)
    assert w_b == {"VIXY": 0.3}

    # 3. Flat case (0.95 <= ratio <= 1.05)
    vix_f = pd.Series(20.0, index=dates)
    vix3m_f = pd.Series(20.0, index=dates)  # ratio = 1.0
    closes_flat = pd.DataFrame({"^VIX": vix_f, "^VIX3M": vix3m_f, "VIXY": vixy, "SVXY": svxy}, index=dates)
    ctx_f = Ctx(closes=closes_flat, datas={}, as_of=dates[-1], universe=["VIXY", "SVXY"], bot=None)
    w_f = vix_basis_signal(ctx_f, contango=0.95, backwardation=1.05, weight=0.3)
    assert w_f == {}

    # Test Bot class
    bot = TS151VixBasisBot()
    assert bot.BOT_NAME == "ts151_VixBasisBot"
    assert bot.BENCHMARKS == ("^VIX", "^VIX3M")
    assert bot.ALLOW_SHORT is True


# ---------------------------------------------------------------------------
# §7.3 Volatility Carry
# ---------------------------------------------------------------------------


def test_vol_carry_signal_and_bot():
    dates = pd.date_range("2026-01-01", periods=100, freq="B")
    np.random.seed(42)

    # In contango: ^VIX / ^VIX3M = 0.8 < 0.95
    vix = pd.Series(16.0, index=dates)
    vix3m = pd.Series(20.0, index=dates)

    # SPY random walk
    spy_ret = np.random.normal(0.0005, 0.01, size=100)
    spy = 400.0 * np.exp(np.cumsum(spy_ret))

    # VIXY with negative beta to SPY: beta ~ -3.0
    vixy_ret = -3.0 * spy_ret + np.random.normal(0, 0.005, size=100)
    vixy = 20.0 * np.exp(np.cumsum(vixy_ret))

    closes = pd.DataFrame({"^VIX": vix, "^VIX3M": vix3m, "VIXY": vixy, "SPY": spy}, index=dates)
    ctx = Ctx(closes=closes, datas={}, as_of=dates[-1], universe=["VIXY", "SPY"], bot=None)

    w = vol_carry_signal(ctx, contango=0.95, weight=0.3, window=63)
    assert w is not None
    assert "VIXY" in w and "SPY" in w
    assert w["VIXY"] == -0.3
    # With beta ~ -3.0, hedge is -beta * weight ~ 0.9 (long SPY)
    assert w["SPY"] > 0.0

    # Test Bot class
    bot = TS151VolCarryBot()
    assert bot.BOT_NAME == "ts151_VolCarryBot"
    assert bot.ALLOW_SHORT is True
    assert bot.MAX_GROSS == 2.0


# ---------------------------------------------------------------------------
# §7.6 Variance Swap / VRP
# ---------------------------------------------------------------------------


def test_variance_swap_signal_and_bot():
    dates = pd.date_range("2026-01-01", periods=100, freq="B")
    np.random.seed(42)

    # Low realized vol: daily std 0.005 -> annual vol ~ 0.08 -> RV^2 ~ 0.0064
    spy_ret = np.random.normal(0.0005, 0.005, size=100)
    spy = 400.0 * np.exp(np.cumsum(spy_ret))

    # High implied vol: VIX = 20 -> IV^2 = (0.2)^2 = 0.04
    # Spread = 0.04 - 0.0064 = 0.0336 > entry=0.01
    vix = pd.Series(20.0, index=dates)
    vixy = pd.Series(15.0, index=dates)

    closes = pd.DataFrame({"^VIX": vix, "SPY": spy, "VIXY": vixy}, index=dates)
    ctx = Ctx(closes=closes, datas={}, as_of=dates[-1], universe=["VIXY"], bot=None)

    w = variance_swap_signal(ctx, entry=0.01, scale=0.04, cap=0.5, window=21)
    assert w is not None
    assert "VIXY" in w
    # Short VIXY: -min(0.5, spread / 0.04)
    assert -0.5 <= w["VIXY"] < 0.0

    # When spread is negative (high RV, low VIX), should exit / be flat
    spy_high_ret = np.random.normal(0.0, 0.03, size=100)  # ann vol ~ 0.48, RV^2 ~ 0.23
    spy_high = 400.0 * np.exp(np.cumsum(spy_high_ret))
    vix_low = pd.Series(12.0, index=dates)  # IV^2 = 0.0144 << 0.23
    closes_neg = pd.DataFrame({"^VIX": vix_low, "SPY": spy_high, "VIXY": vixy}, index=dates)
    ctx_neg = Ctx(closes=closes_neg, datas={}, as_of=dates[-1], universe=["VIXY"], bot=None)
    w_neg = variance_swap_signal(ctx_neg, entry=0.01, scale=0.04, cap=0.5, window=21)
    assert w_neg == {}

    # Test Bot class
    bot = TS151VarianceSwapBot()
    assert bot.BOT_NAME == "ts151_VarianceSwapBot"
    assert bot.ALLOW_SHORT is True


# ---------------------------------------------------------------------------
# §8.1 FX Moving Average Crossover
# ---------------------------------------------------------------------------


def test_fx_ma_signal_and_bot():
    dates = pd.date_range("2026-01-01", periods=250, freq="B")
    fx_universe = ["FXE", "FXB", "FXA", "FXC", "FXF", "FXY"]

    # Trend up for FXE, trend down for FXY, flat for others
    data = {}
    for sym in fx_universe:
        if sym == "FXE":
            data[sym] = np.linspace(100.0, 150.0, len(dates))  # SMA fast > slow -> long
        elif sym == "FXY":
            data[sym] = np.linspace(150.0, 100.0, len(dates))  # SMA fast < slow -> short
        else:
            data[sym] = np.full(len(dates), 100.0)

    closes = pd.DataFrame(data, index=dates)
    ctx = Ctx(closes=closes, datas={}, as_of=dates[-1], universe=fx_universe, bot=None)

    w = fx_ma_signal(ctx, fast=50, slow=200, gross=1.0)
    assert w is not None
    assert len(w) == len(fx_universe)
    assert w["FXE"] > 0.0  # long
    assert w["FXY"] < 0.0  # short

    # Gross leverage sum of abs weights == 1.0
    gross = sum(abs(v) for v in w.values())
    assert pytest.approx(gross, rel=1e-5) == 1.0

    # Test Bot class
    bot = TS151FxMaBot()
    assert bot.BOT_NAME == "ts151_FxMaBot"
    assert bot.DEFAULT_UNIVERSE == "fx_etfs"
    assert bot.ALLOW_SHORT is True


# ---------------------------------------------------------------------------
# §8.2 FX Carry
# ---------------------------------------------------------------------------


def test_fx_carry_signal_and_bot():
    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    fx_universe = ["FXE", "FXB", "FXA", "FXC", "FXF", "FXY"]
    # Currencies: EUR, GBP, AUD, CAD, CHF, JPY
    # High rates: AUD (5%), GBP (4%) -> top 2
    # Low rates: CHF (1%), JPY (0.5%) -> bottom 2
    # USD: 3%
    rates_data = {
        "USD": [0.03] * len(dates),
        "AUD": [0.05] * len(dates),
        "GBP": [0.04] * len(dates),
        "CAD": [0.03] * len(dates),
        "EUR": [0.02] * len(dates),
        "CHF": [0.01] * len(dates),
        "JPY": [0.005] * len(dates),
    }
    rates = pd.DataFrame(rates_data, index=dates)
    closes = pd.DataFrame(100.0, index=dates, columns=fx_universe)
    ctx = Ctx(closes=closes, datas={}, as_of=dates[-1], universe=fx_universe, bot=None)

    w = fx_carry_signal(ctx, k=2, gross=1.0, rates=rates)
    assert w is not None
    # Top 2 (FXA, FXB) should be long
    assert w["FXA"] > 0.0
    assert w["FXB"] > 0.0
    # Bottom 2 (FXY, FXF) should be short
    assert w["FXY"] < 0.0
    assert w["FXF"] < 0.0
    assert "FXC" not in w and "FXE" not in w

    # Net sum should be dollar-neutral
    assert pytest.approx(sum(w.values()), abs=1e-6) == 0.0
    assert pytest.approx(sum(abs(v) for v in w.values()), rel=1e-5) == 1.0

    # Test Bot class
    bot = TS151FxCarryBot()
    assert bot.BOT_NAME == "ts151_FxCarryBot"
    assert bot.NEEDS_NETWORK is True
    assert bot.rebalance == "M"


# ---------------------------------------------------------------------------
# §8.3 Dollar Carry
# ---------------------------------------------------------------------------


def test_dollar_carry_signal_and_bot():
    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    fx_universe = ["FXE", "FXB", "FXA", "FXC", "FXF", "FXY"]

    # 1. Foreign rates higher than USD on average -> long all FX ETFs
    rates_bull = pd.DataFrame(
        {
            "USD": [0.02] * len(dates),
            "EUR": [0.03] * len(dates),
            "GBP": [0.04] * len(dates),
            "AUD": [0.05] * len(dates),
            "CAD": [0.03] * len(dates),
            "CHF": [0.025] * len(dates),
            "JPY": [0.01] * len(dates),
        },
        index=dates,
    )
    closes = pd.DataFrame(100.0, index=dates, columns=fx_universe)
    ctx = Ctx(closes=closes, datas={}, as_of=dates[-1], universe=fx_universe, bot=None)

    w_long = dollar_carry_signal(ctx, gross=1.0, rates=rates_bull)
    assert w_long is not None
    assert all(v > 0.0 for v in w_long.values())
    assert pytest.approx(sum(w_long.values()), rel=1e-5) == 1.0

    # 2. Foreign rates lower than USD on average -> short all FX ETFs
    rates_bear = pd.DataFrame(
        {
            "USD": [0.06] * len(dates),
            "EUR": [0.02] * len(dates),
            "GBP": [0.03] * len(dates),
            "AUD": [0.04] * len(dates),
            "CAD": [0.03] * len(dates),
            "CHF": [0.01] * len(dates),
            "JPY": [0.005] * len(dates),
        },
        index=dates,
    )
    w_short = dollar_carry_signal(ctx, gross=1.0, rates=rates_bear)
    assert w_short is not None
    assert all(v < 0.0 for v in w_short.values())
    assert pytest.approx(sum(w_short.values()), rel=1e-5) == -1.0

    # Test Bot class
    bot = TS151DollarCarryBot()
    assert bot.BOT_NAME == "ts151_DollarCarryBot"
    assert bot.NEEDS_NETWORK is True


# ---------------------------------------------------------------------------
# §8.4 FX Momentum + Carry Blend
# ---------------------------------------------------------------------------


def test_fx_mom_carry_signal_and_bot():
    dates = pd.date_range("2026-01-01", periods=80, freq="B")
    fx_universe = ["FXE", "FXB", "FXA", "FXC", "FXF", "FXY"]

    # Build price trends: FXA and FXB strong up, FXY and FXF strong down
    data = {}
    for sym in fx_universe:
        if sym in ("FXA", "FXB"):
            data[sym] = np.linspace(100.0, 140.0, len(dates))
        elif sym in ("FXY", "FXF"):
            data[sym] = np.linspace(140.0, 100.0, len(dates))
        else:
            data[sym] = np.full(len(dates), 100.0)
    closes = pd.DataFrame(data, index=dates)

    # Rates: FXA and FXB also high yield, FXY and FXF low yield
    rates = pd.DataFrame(
        {
            "USD": [0.03] * len(dates),
            "AUD": [0.05] * len(dates),
            "GBP": [0.04] * len(dates),
            "CAD": [0.03] * len(dates),
            "EUR": [0.02] * len(dates),
            "CHF": [0.01] * len(dates),
            "JPY": [0.005] * len(dates),
        },
        index=dates,
    )

    ctx = Ctx(closes=closes, datas={}, as_of=dates[-1], universe=fx_universe, bot=None)
    w = fx_mom_carry_signal(ctx, window=63, w_mom=0.5, k=2, gross=1.0, rates=rates)
    assert w is not None
    assert w["FXA"] > 0.0
    assert w["FXB"] > 0.0
    assert w["FXY"] < 0.0
    assert w["FXF"] < 0.0
    assert pytest.approx(sum(w.values()), abs=1e-6) == 0.0

    # Test Bot class
    bot = TS151FxMomCarryBot()
    assert bot.BOT_NAME == "ts151_FxMomCarryBot"
    assert bot.NEEDS_NETWORK is True
    assert bot.param_grid is not None
    assert "w_mom" in bot.param_grid
