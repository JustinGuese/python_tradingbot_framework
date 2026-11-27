"""Unit tests for TS151 commodities and futures strategies (§9.1-9.5, 10.1, 10.3, 10.4).

Tests pure signals in signals_commod.py and bot classes in tradingbot/.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tradingbot.ts151_commoddiversificationbot import TS151CommodDiversificationBot
from tradingbot.ts151_commodvaluebot import TS151CommodValueBot
from tradingbot.ts151_contrarianbot import TS151ContrarianBot
from tradingbot.ts151_hedgedholdingbot import TS151HedgedHoldingBot
from tradingbot.ts151_hedgingpressurebot import TS151HedgingPressureBot
from tradingbot.ts151_rollyieldbot import TS151RollYieldBot
from tradingbot.ts151_skewnessbot import TS151SkewnessBot
from tradingbot.ts151_trendfollowingbot import TS151TrendFollowingBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.ts151 import signals_commod
from tradingbot.utils.ts151.base import Ctx


@pytest.fixture
def patch_bot_init(mocker):
    """Stub out Bot.__init__ to avoid database interactions during unit tests."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)


# ---------------------------------------------------------------------------
# Pure signal tests
# ---------------------------------------------------------------------------


def test_roll_yield():
    # Backwardation: F1 > F2 -> positive roll yield
    ry_pos = signals_commod.roll_yield(100.0, 95.0, months_apart=1.0)
    assert ry_pos > 0.0
    expected = math.log(100.0 / 95.0) * 12.0
    assert pytest.approx(expected, rel=1e-5) == ry_pos

    # Contango: F1 < F2 -> negative roll yield
    ry_neg = signals_commod.roll_yield(90.0, 100.0, months_apart=2.0)
    assert ry_neg < 0.0

    # Invalid cases
    assert math.isnan(signals_commod.roll_yield(-10.0, 100.0, 1.0))
    assert math.isnan(signals_commod.roll_yield(100.0, 0.0, 1.0))
    assert math.isnan(signals_commod.roll_yield(100.0, 100.0, 0.0))


def test_roll_yield_scores():
    quotes = {
        "USO": (80.0, 78.0, 1.0),  # backwardation: high roll yield
        "UNG": (3.0, 3.5, 1.0),  # contango: low roll yield
        "GLD": (200.0, 201.0, 2.0),
    }
    scores = signals_commod.roll_yield_scores(quotes)
    assert len(scores) == 3
    assert scores["USO"] > scores["GLD"] > scores["UNG"]


def test_hedging_pressure_signal():
    dates = pd.date_range("2026-01-01", periods=5, freq="W")
    records = []
    # Commodity A (code 001): commercial hedgers heavily short -> low HP -> buy
    for d in dates:
        records.append(
            {
                "market_code": "001",
                "report_date": d,
                "comm_long": 10.0,
                "comm_short": 90.0,
            }
        )
    # Commodity B (code 002): commercial hedgers heavily long -> high HP -> sell
    for d in dates:
        records.append(
            {
                "market_code": "002",
                "report_date": d,
                "comm_long": 90.0,
                "comm_short": 10.0,
            }
        )
    # Commodity C (code 003): balanced
    for d in dates:
        records.append(
            {
                "market_code": "003",
                "report_date": d,
                "comm_long": 50.0,
                "comm_short": 50.0,
            }
        )
    # Commodity D (code 004): slightly short
    for d in dates:
        records.append(
            {
                "market_code": "004",
                "report_date": d,
                "comm_long": 30.0,
                "comm_short": 70.0,
            }
        )

    cot_df = pd.DataFrame(records)
    etf_to_code = {"ETFA": "001", "ETFB": "002", "ETFC": "003", "ETFD": "004"}

    weights = signals_commod.hedging_pressure_signal(cot_df, etf_to_code, weeks=4, k=1, gross=1.0, long_only=False)
    # k=1: ETFA has lowest HP -> long; ETFB has highest HP -> short
    assert "ETFA" in weights and weights["ETFA"] > 0
    assert "ETFB" in weights and weights["ETFB"] < 0
    assert pytest.approx(weights["ETFA"], rel=1e-5) == 0.5
    assert pytest.approx(weights["ETFB"], rel=1e-5) == -0.5


def test_commod_diversification_weights():
    np.random.seed(42)
    n = 300
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    ret_spy = np.random.normal(0.0005, 0.01, n)
    ret_ief = np.random.normal(0.0001, 0.005, n)
    ret_gld = np.random.normal(0.0003, 0.008, n)
    ret_dbc = np.random.normal(0.0002, 0.012, n)

    df_closes = pd.DataFrame(
        {
            "SPY": 100.0 * np.exp(np.cumsum(ret_spy)),
            "IEF": 100.0 * np.exp(np.cumsum(ret_ief)),
            "GLD": 100.0 * np.exp(np.cumsum(ret_gld)),
            "DBC": 100.0 * np.exp(np.cumsum(ret_dbc)),
        },
        index=dates,
    )

    # Minvar
    w_minvar = signals_commod.commod_diversification_weights(df_closes, window=252, method="minvar")
    assert not w_minvar.empty
    assert pytest.approx(w_minvar.sum(), rel=1e-5) == 1.0
    assert (w_minvar >= -1e-6).all()

    # ERC
    w_erc = signals_commod.commod_diversification_weights(df_closes, window=252, method="erc")
    assert not w_erc.empty
    assert pytest.approx(w_erc.sum(), rel=1e-5) == 1.0
    assert (w_erc > 0.0).all()


def test_commod_value_scores():
    dates = pd.date_range("2020-01-01", periods=1500, freq="D")
    # Asset A was high 5 years ago, now crashed -> undervalued -> high score
    prices_a = np.ones(1500) * 100.0
    prices_a[-1] = 50.0

    # Asset B was low 5 years ago, now surged -> overvalued -> low score
    prices_b = np.ones(1500) * 50.0
    prices_b[-1] = 100.0

    closes = pd.DataFrame({"A": prices_a, "B": prices_b}, index=dates)
    scores = signals_commod.commod_value_scores(closes, lag_start=1386, lag_end=1134)
    assert len(scores) == 2
    assert scores["A"] > scores["B"]
    assert scores["A"] > 0.0  # log(100) - log(50) > 0
    assert scores["B"] < 0.0  # log(50) - log(100) < 0


def test_skewness_scores():
    np.random.seed(42)
    n = 300
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    # Asset A has negative skew (crash risk) -> should get high score (-skew is positive)
    r_neg_skew = np.random.normal(0, 0.01, n)
    r_neg_skew[10] = -0.15
    r_neg_skew[50] = -0.12

    # Asset B has positive skew (lottery payoffs) -> should get low score (-skew is negative)
    r_pos_skew = np.random.normal(0, 0.01, n)
    r_pos_skew[20] = 0.15
    r_pos_skew[60] = 0.12

    closes = pd.DataFrame(
        {
            "A": 100.0 * np.exp(np.cumsum(r_neg_skew)),
            "B": 100.0 * np.exp(np.cumsum(r_pos_skew)),
        },
        index=dates,
    )

    scores = signals_commod.skewness_scores(closes, window=252)
    assert not scores.empty
    assert scores["A"] > scores["B"]


def test_hedged_holding_weights():
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    mkt = np.random.normal(0, 0.01, n)
    # Asset has beta ~ 1.5 vs mkt
    asset_ret = 1.5 * mkt + np.random.normal(0, 0.002, n)

    closes = pd.DataFrame(
        {
            "XLK": 100.0 * np.exp(np.cumsum(asset_ret)),
            "SPY": 100.0 * np.exp(np.cumsum(mkt)),
        },
        index=dates,
    )

    weights = signals_commod.hedged_holding_weights(closes, asset="XLK", hedge="SPY", window=63)
    assert weights["XLK"] == 1.0
    assert weights["SPY"] < 0.0  # short hedge
    assert -1.8 < weights["SPY"] < -1.2


def test_contrarian_weights():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    # A surged, B crashed, C stayed flat over last 5 bars
    closes = pd.DataFrame(
        {
            "A": [100, 100, 100, 100, 100, 105, 110, 115, 120, 125],
            "B": [100, 100, 100, 100, 100, 95, 90, 85, 80, 75],
            "C": [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
        },
        index=dates,
    )

    # Insufficient bars (< window + 1)
    w_short = signals_commod.contrarian_weights(closes.iloc[:4], window=5, gross=2.0)
    assert not w_short

    # Valid window
    w = signals_commod.contrarian_weights(closes, window=5, gross=2.0)
    assert w["B"] > 0  # B crashed -> buy
    assert w["A"] < 0  # A surged -> sell
    assert pytest.approx(sum(w.values()), abs=1e-5) == 0.0
    assert pytest.approx(sum(abs(x) for x in w.values()), rel=1e-5) == 2.0


def test_trend_following_weights():
    dates = pd.date_range("2025-01-01", periods=300, freq="D")
    # A trended up, B trended down
    ret_a = np.ones(300) * 0.001
    ret_b = np.ones(300) * -0.001
    closes = pd.DataFrame(
        {
            "A": 100.0 * np.exp(np.cumsum(ret_a)),
            "B": 100.0 * np.exp(np.cumsum(ret_b)),
        },
        index=dates,
    )

    w = signals_commod.trend_following_weights(closes, window=252, vol_window=63, target_vol=0.4, max_gross=2.0)
    assert w.get("A", 0.0) != 0.0
    assert w["A"] > 0.0
    assert w["B"] < 0.0
    assert sum(abs(x) for x in w.values()) <= 2.0001


# ---------------------------------------------------------------------------
# Bot class tests
# ---------------------------------------------------------------------------


def test_all_bot_attributes_and_signals(patch_bot_init, mocker):
    # 1. RollYieldBot
    bot_ry = TS151RollYieldBot(k=2, gross=1.0)
    assert bot_ry.BOT_NAME == "ts151_RollYieldBot"
    assert bot_ry.NEEDS_NETWORK is True
    # In backtest / without live contracts, returns None
    mocker.patch("tradingbot.ts151_rollyieldbot.fetch_live_contracts", return_value={})
    ctx_dummy = Ctx(closes=pd.DataFrame(), datas={}, as_of=pd.Timestamp.now(), universe=[], bot=bot_ry)
    assert bot_ry.signal(ctx_dummy) is None

    # 2. HedgingPressureBot
    bot_hp = TS151HedgingPressureBot(weeks=4, k=2)
    assert bot_hp.BOT_NAME == "ts151_HedgingPressureBot"
    assert bot_hp.NEEDS_DB is True
    assert bot_hp.rebalance == "W"

    # 3. CommodDiversificationBot
    bot_cd = TS151CommodDiversificationBot(window=252, method="minvar")
    assert bot_cd.BOT_NAME == "ts151_CommodDiversificationBot"
    assert bot_cd.long_only is True

    # 4. CommodValueBot
    bot_cv = TS151CommodValueBot(k=2)
    assert bot_cv.BOT_NAME == "ts151_CommodValueBot"
    assert bot_cv.LIVE_PERIOD == "10y"

    # 5. SkewnessBot
    bot_sk = TS151SkewnessBot(window=252, k=2)
    assert bot_sk.BOT_NAME == "ts151_SkewnessBot"
    assert bot_sk.ALLOW_SHORT is True

    # 6. HedgedHoldingBot
    bot_hh = TS151HedgedHoldingBot(window=63)
    assert bot_hh.BOT_NAME == "ts151_HedgedHoldingBot"
    assert bot_hh.rebalance == "W"

    # 7. ContrarianBot
    bot_ct = TS151ContrarianBot(window=5, gross=2.0)
    assert bot_ct.BOT_NAME == "ts151_ContrarianBot"
    assert bot_ct.rebalance == "W"

    # 8. TrendFollowingBot
    bot_tf = TS151TrendFollowingBot(window=252, vol_window=63)
    assert bot_tf.BOT_NAME == "ts151_TrendFollowingBot"
    assert bot_tf.rebalance == "M"
