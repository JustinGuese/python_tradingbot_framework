"""Unit tests for TS151 ETF strategies (§4: 4.1 - 4.6).

Tests:
1. Pure signal functions in tradingbot.utils.ts151.signals_etfs:
   - sector_momentum (4.1)
   - alpha_rotation (4.2)
   - rsquared_rotation (4.3)
   - ibs_reversal (4.4)
   - leveraged_pair_rebalance (4.5)
   - multiasset_trend (4.6)
2. Bot class instantiations and signal calls for all 6 bots:
   - TS151SectorMomentumBot
   - TS151AlphaRotationBot
   - TS151RSquaredBot
   - TS151IBSBot
   - TS151LeveragedEtfBot
   - TS151MultiAssetTrendBot
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.ts151_alpharotationbot import TS151AlphaRotationBot
from tradingbot.ts151_ibsbot import TS151IBSBot
from tradingbot.ts151_leveragedetfbot import TS151LeveragedEtfBot
from tradingbot.ts151_multiassettrendbot import TS151MultiAssetTrendBot
from tradingbot.ts151_rsquaredbot import TS151RSquaredBot
from tradingbot.ts151_sectormomentumbot import TS151SectorMomentumBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.ts151 import signals_etfs, universes
from tradingbot.utils.ts151.base import Ctx


@pytest.fixture
def patch_bot_init(mocker):
    """Stub out Bot.__init__ to avoid DB connection during unit tests."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)


def _make_ctx(
    closes_dict: dict[str, Sequence[float]],
    dates: pd.DatetimeIndex | None = None,
    datas: dict[str, pd.DataFrame] | None = None,
    universe: list[str] | tuple[str, ...] | None = None,
) -> Ctx:
    n = len(next(iter(closes_dict.values())))
    if dates is None:
        dates = pd.date_range("2025-01-01", periods=n, freq="B")
    closes = pd.DataFrame(closes_dict, index=dates)
    u = universe if universe is not None else list(closes_dict.keys())
    d = datas if datas is not None else {}
    return Ctx(closes=closes, datas=d, as_of=dates[-1], universe=u, bot=None)


# --------------------------------------------------------------------------
# 4.1 Sector Momentum
# --------------------------------------------------------------------------


def test_sector_momentum_signal():
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    # 4 symbols with different upward trajectories
    data = {
        "XLB": np.linspace(100, 110, 300),  # +10%
        "XLE": np.linspace(100, 150, 300),  # +50%
        "XLF": np.linspace(100, 130, 300),  # +30%
        "XLK": np.linspace(100, 120, 300),  # +20%
    }
    ctx = _make_ctx(data, dates=dates, universe=["XLB", "XLE", "XLF", "XLK"])

    # Top 2 should be XLE (+50%) and XLF (+30%)
    w = signals_etfs.sector_momentum(ctx, window=100, skip=10, k=2, gross=1.0)
    assert w is not None
    assert set(w.keys()) == {"XLE", "XLF"}
    assert pytest.approx(w["XLE"], rel=1e-4) == 0.5
    assert pytest.approx(w["XLF"], rel=1e-4) == 0.5

    # Insufficient history returns None
    w_short = signals_etfs.sector_momentum(ctx, window=400, skip=10, k=2)
    assert w_short is None

    # Fewer valid names than k returns None
    w_too_few = signals_etfs.sector_momentum(ctx, window=100, skip=10, k=10)
    assert w_too_few is None


# --------------------------------------------------------------------------
# 4.2 Alpha Rotation
# --------------------------------------------------------------------------


def test_alpha_rotation_signal():
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    rng = np.random.default_rng(42)
    spy_ret = rng.normal(0.0005, 0.01, size=300)
    spy_close = 100.0 * np.exp(np.cumsum(spy_ret))

    # High alpha asset: market return + 0.002 daily excess
    high_alpha_ret = spy_ret + 0.002
    high_alpha_close = 100.0 * np.exp(np.cumsum(high_alpha_ret))

    # Low alpha asset: market return - 0.002 daily excess
    low_alpha_ret = spy_ret - 0.002
    low_alpha_close = 100.0 * np.exp(np.cumsum(low_alpha_ret))

    # Neutral asset: 0.5 * market return
    mid_ret = 0.5 * spy_ret
    mid_close = 100.0 * np.exp(np.cumsum(mid_ret))

    data = {
        "SPY": spy_close,
        "HIGH": high_alpha_close,
        "LOW": low_alpha_close,
        "MID": mid_close,
    }
    ctx = _make_ctx(data, dates=dates, universe=["HIGH", "LOW", "MID"])

    w = signals_etfs.alpha_rotation(ctx, window=250, k=2, benchmark="SPY", gross=1.0)
    assert w is not None
    assert "HIGH" in w
    assert "LOW" not in w
    assert pytest.approx(sum(w.values()), rel=1e-4) == 1.0

    # Missing benchmark returns None
    ctx_no_bench = _make_ctx(
        {"HIGH": high_alpha_close, "LOW": low_alpha_close},
        dates=dates,
        universe=["HIGH", "LOW"],
    )
    assert signals_etfs.alpha_rotation(ctx_no_bench, window=50, k=1, benchmark="SPY") is None


# --------------------------------------------------------------------------
# 4.3 R-squared Rotation
# --------------------------------------------------------------------------


def test_rsquared_rotation_signal():
    dates = pd.date_range("2024-01-01", periods=200, freq="B")
    rng = np.random.default_rng(123)
    spy_ret = rng.normal(0.0005, 0.01, size=200)
    spy_close = 100.0 * np.exp(np.cumsum(spy_ret))

    # Correlated with SPY (high R^2)
    s1_ret = 1.0 * spy_ret + rng.normal(0, 0.0001, size=200)
    # Uncorrelated with SPY (low R^2) and positive drift (high alpha)
    s2_ret = rng.normal(0.003, 0.01, size=200)
    # Uncorrelated with SPY (low R^2) and negative drift (low alpha)
    s3_ret = rng.normal(-0.003, 0.01, size=200)
    # Moderate correlation
    s4_ret = 0.8 * spy_ret + rng.normal(0, 0.005, size=200)

    data = {
        "SPY": spy_close,
        "S1": 100.0 * np.exp(np.cumsum(s1_ret)),
        "S2": 100.0 * np.exp(np.cumsum(s2_ret)),
        "S3": 100.0 * np.exp(np.cumsum(s3_ret)),
        "S4": 100.0 * np.exp(np.cumsum(s4_ret)),
    }
    ctx = _make_ctx(data, dates=dates, universe=["S1", "S2", "S3", "S4"])

    # Among frac=0.5 (2 lowest R^2), S2 and S3 have lowest correlation to SPY.
    # Between S2 and S3, S2 has much higher alpha.
    w = signals_etfs.rsquared_rotation(
        ctx, window=100, frac=0.5, k=1, benchmark="SPY", gross=1.0
    )
    assert w is not None
    assert "S2" in w
    assert pytest.approx(w["S2"], rel=1e-4) == 1.0


# --------------------------------------------------------------------------
# 4.4 Internal Bar Strength (IBS)
# --------------------------------------------------------------------------


def test_ibs_reversal_signal():
    dates = pd.date_range("2025-01-01", periods=10, freq="B")

    # Bar 0-7: normal IBS (0.5)
    # Bar 8: close near low -> IBS = (101 - 100) / (110 - 100) = 0.1 < 0.2 (enter long)
    # Bar 9: IBS = 0.5 (holds state 1)
    df_spy = pd.DataFrame(
        {
            "high": [110.0] * 10,
            "low": [100.0] * 10,
            "close": [105.0] * 8 + [101.0, 105.0],
        },
        index=dates,
    )

    # QQQ: Bar 8 IBS = 0.5, Bar 9 IBS = (109 - 100) / (110 - 100) = 0.9 > 0.8 (exit)
    df_qqq = pd.DataFrame(
        {
            "high": [110.0] * 10,
            "low": [100.0] * 10,
            "close": [105.0] * 8 + [105.0, 109.0],
        },
        index=dates,
    )

    datas = {"SPY": df_spy, "QQQ": df_qqq}
    ctx = _make_ctx(
        {"SPY": df_spy["close"], "QQQ": df_qqq["close"]},
        dates=dates,
        datas=datas,
        universe=["SPY", "QQQ"],
    )

    w = signals_etfs.ibs_reversal(ctx, low=0.2, high=0.8, gross=1.0)
    assert w is not None
    assert set(w.keys()) == {"SPY"}
    assert pytest.approx(w["SPY"], rel=1e-4) == 1.0

    # If both exit:
    df_spy_exit = df_spy.copy()
    df_spy_exit.loc[dates[-1], "close"] = 109.0  # exits
    datas_exit = {"SPY": df_spy_exit, "QQQ": df_qqq}
    ctx_exit = _make_ctx(
        {"SPY": df_spy_exit["close"], "QQQ": df_qqq["close"]},
        dates=dates,
        datas=datas_exit,
        universe=["SPY", "QQQ"],
    )
    w_empty = signals_etfs.ibs_reversal(ctx_exit, low=0.2, high=0.8)
    assert w_empty == {}  # flat


# --------------------------------------------------------------------------
# 4.5 Leveraged ETF Pairs
# --------------------------------------------------------------------------


def test_leveraged_pair_rebalance_signal():
    # Test 1: divergence on the final bar triggers rebalance
    dates = pd.date_range("2025-01-06", periods=20, freq="B")  # strictly within January 2025
    p1 = [100.0] * 19 + [150.0]
    p2 = [100.0] * 19 + [70.0]

    data = {"TQQQ": p1, "SQQQ": p2}
    ctx = _make_ctx(data, dates=dates, universe=["TQQQ", "SQQQ"])

    # On final day, drift between legs > band (0.1) triggers rebalance
    w = signals_etfs.leveraged_pair_rebalance(
        ctx, pairs=[("TQQQ", "SQQQ")], band=0.1, gross=1.0
    )
    assert w is not None
    assert set(w.keys()) == {"TQQQ", "SQQQ"}
    assert pytest.approx(w["TQQQ"], rel=1e-4) == -0.5
    assert pytest.approx(w["SQQQ"], rel=1e-4) == -0.5

    # Test 2: intermediate bar with no monthly roll and small drift (< band) returns None (hold)
    data_small_drift = {"TQQQ": [100.0] * 19 + [101.0], "SQQQ": [100.0] * 20}
    ctx_nodrift = _make_ctx(
        data_small_drift, dates=dates, universe=["TQQQ", "SQQQ"]
    )
    w_none = signals_etfs.leveraged_pair_rebalance(
        ctx_nodrift, pairs=[("TQQQ", "SQQQ")], band=0.1
    )
    assert w_none is None


# --------------------------------------------------------------------------
# 4.6 Multi-asset Trend
# --------------------------------------------------------------------------


def test_multiasset_trend_signal():
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    # Asset 1: positive trend, low vol
    a1 = np.linspace(100, 150, 300)
    # Asset 2: positive trend, higher vol (step changes)
    a2 = np.linspace(100, 140, 300)
    a2[::2] += 5.0
    # Asset 3: negative trend
    a3 = np.linspace(100, 70, 300)

    data = {"A1": a1, "A2": a2, "A3": a3}
    ctx = _make_ctx(data, dates=dates, universe=["A1", "A2", "A3"])

    w = signals_etfs.multiasset_trend(ctx, window=250, vol_window=50, gross=1.0)
    assert w is not None
    assert "A1" in w
    assert "A2" in w
    assert "A3" not in w  # filtered out by negative return
    assert w["A1"] > w["A2"]  # lower vol gets higher inverse-vol weight
    assert pytest.approx(sum(w.values()), rel=1e-4) == 1.0

    # If all negative trend -> returns {}
    data_all_down = {"A1": a3, "A2": a3}
    ctx_down = _make_ctx(data_all_down, dates=dates, universe=["A1", "A2"])
    w_flat = signals_etfs.multiasset_trend(ctx_down, window=250, vol_window=50)
    assert w_flat == {}


# --------------------------------------------------------------------------
# Bot Class Verification
# --------------------------------------------------------------------------


def test_ts151_sector_momentum_bot(patch_bot_init):
    bot = TS151SectorMomentumBot(window=120, skip=10, k=2)
    assert bot.BOT_NAME == "ts151_SectorMomentumBot"
    assert bot.DEFAULT_UNIVERSE == "sectors"
    assert bot.rebalance == "M"
    assert bot.long_only is True
    assert bot.window == 120
    assert bot.skip == 10
    assert bot.k == 2
    assert bot.param_grid is not None


def test_ts151_alpha_rotation_bot(patch_bot_init):
    bot = TS151AlphaRotationBot(window=200, k=3, benchmark="SPY")
    assert bot.BOT_NAME == "ts151_AlphaRotationBot"
    assert bot.BENCHMARKS == ("SPY",)
    assert bot.rebalance == "M"
    assert bot.window == 200
    assert bot.k == 3
    assert bot.param_grid is not None


def test_ts151_rsquared_bot(patch_bot_init):
    bot = TS151RSquaredBot(window=100, frac=0.4, k=2)
    assert bot.BOT_NAME == "ts151_RSquaredBot"
    assert bot.BENCHMARKS == ("SPY",)
    assert bot.window == 100
    assert bot.frac == 0.4
    assert bot.k == 2
    assert bot.param_grid is not None


def test_ts151_ibs_bot(patch_bot_init):
    bot = TS151IBSBot(low=0.15, high=0.85)
    assert bot.BOT_NAME == "ts151_IBSBot"
    assert bot.DEFAULT_UNIVERSE == ("SPY", "QQQ")
    assert bot.rebalance == "D"
    assert bot.low == 0.15
    assert bot.high == 0.85
    assert bot.param_grid is not None


def test_ts151_leveraged_etf_bot(patch_bot_init):
    bot = TS151LeveragedEtfBot(band=0.08)
    assert bot.BOT_NAME == "ts151_LeveragedEtfBot"
    assert bot.DEFAULT_UNIVERSE == "leveraged_pairs"
    assert bot.ALLOW_SHORT is True
    assert bot.long_only is False
    assert bot.band == 0.08
    assert bot.rebalance == "D"
    assert bot.param_grid is not None


def test_ts151_multiasset_trend_bot(patch_bot_init):
    bot = TS151MultiAssetTrendBot(window=200, vol_window=40)
    assert bot.BOT_NAME == "ts151_MultiAssetTrendBot"
    assert bot.DEFAULT_UNIVERSE == "multi_asset"
    assert bot.rebalance == "M"
    assert bot.window == 200
    assert bot.vol_window == 40
    assert bot.param_grid is not None
