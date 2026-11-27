"""Tests for TS151 S9 bots (Real Estate, Crypto, Macro §16.2–16.5, 18.2, 18.3, 19.2, 19.3, 19.5)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.ts151_cryptoannbot import TS151CryptoANNBot
from tradingbot.ts151_cryptosentimentbot import TS151CryptoSentimentBot
from tradingbot.ts151_econannouncementbot import TS151EconAnnouncementBot
from tradingbot.ts151_fundamentalmacrobot import TS151FundamentalMacroBot
from tradingbot.ts151_globalinflationbot import TS151GlobalInflationBot
from tradingbot.ts151_intraassetdivbot import TS151IntraAssetDivBot
from tradingbot.ts151_realestatediversitybot import TS151RealEstateDiversityBot
from tradingbot.ts151_realestateinflationbot import TS151RealEstateInflationBot
from tradingbot.ts151_realestatemomentumbot import TS151RealEstateMomentumBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.ts151.signals_macro import (
    crypto_ann_weights,
    crypto_sentiment_weights,
    econ_announcement_weights,
    fundamental_macro_weights,
    global_inflation_weights,
    intra_asset_div_weights,
    real_estate_diversity_weights,
    real_estate_inflation_weights,
    real_estate_momentum_weights,
)


@pytest.fixture(autouse=True)
def mock_bot_init(monkeypatch):
    """Bypass base Bot init which touches DB/Portfolio."""
    monkeypatch.setattr(Bot, "__init__", lambda *args, **kwargs: None)


# -------------------------------------------------------------------------
# Test Contract & Param Grid for all 9 bots
# -------------------------------------------------------------------------

BOT_CLASSES = [
    TS151RealEstateDiversityBot,
    TS151IntraAssetDivBot,
    TS151RealEstateMomentumBot,
    TS151RealEstateInflationBot,
    TS151CryptoANNBot,
    TS151CryptoSentimentBot,
    TS151FundamentalMacroBot,
    TS151GlobalInflationBot,
    TS151EconAnnouncementBot,
]


@pytest.mark.parametrize("bot_cls", BOT_CLASSES)
def test_bot_contract_and_param_grid(bot_cls):
    # Verify DEFAULTS keys are all in param_grid
    defaults = getattr(bot_cls, "DEFAULTS", {})
    param_grid = getattr(bot_cls, "param_grid", {})
    assert param_grid is not None, f"{bot_cls.__name__} has no param_grid"
    for k in defaults:
        assert k in param_grid, f"{bot_cls.__name__}: key {k!r} from DEFAULTS missing in param_grid"

    # Instantiate bot and test attributes
    bot = bot_cls()
    for k, v in defaults.items():
        assert hasattr(bot, k)
        assert getattr(bot, k) == v


# -------------------------------------------------------------------------
# 16.2 Real Estate Diversity (Mean-Variance)
# -------------------------------------------------------------------------


def test_real_estate_diversity():
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    reits = ["VNQ", "IYR", "RWR", "ICF"]
    rets = np.random.normal(0.0005, 0.01, size=(300, len(reits)))
    prices = 100.0 * np.exp(np.cumsum(rets, axis=0))
    closes = pd.DataFrame(prices, index=dates, columns=reits)

    w = real_estate_diversity_weights(closes, window=252, min_weight=0.05, max_weight=0.5, max_vol=0.25)
    assert len(w) == len(reits)
    assert pytest.approx(sum(w.values()), 1e-4) == 1.0
    for weight in w.values():
        assert 0.049 <= weight <= 0.501

    # Test vol cap scaling down
    w_low_vol = real_estate_diversity_weights(closes, window=252, min_weight=0.05, max_weight=0.5, max_vol=0.05)
    assert sum(w_low_vol.values()) < 1.0  # scaled down due to vol cap


# -------------------------------------------------------------------------
# 16.3 Intra-Asset Diversification (ERC)
# -------------------------------------------------------------------------


def test_intra_asset_div():
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    reits = ["VNQ", "IYR", "RWR", "ICF"]
    # Give ICF much higher volatility
    rets = np.random.normal(0.0005, 0.01, size=(300, len(reits)))
    rets[:, 3] *= 3.0
    prices = 100.0 * np.exp(np.cumsum(rets, axis=0))
    closes = pd.DataFrame(prices, index=dates, columns=reits)

    w = intra_asset_div_weights(closes, window=252)
    assert len(w) == len(reits)
    assert pytest.approx(sum(w.values()), 1e-4) == 1.0
    # Higher vol asset should receive lower weight in ERC
    assert w["ICF"] < w["VNQ"]


# -------------------------------------------------------------------------
# 16.4 Real Estate Momentum
# -------------------------------------------------------------------------


def test_real_estate_momentum():
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    # A has highest return across all horizons
    closes = pd.DataFrame(
        {
            "A": np.linspace(100, 200, 300),
            "B": np.linspace(100, 150, 300),
            "C": np.linspace(100, 120, 300),
            "D": np.linspace(100, 80, 300),
        },
        index=dates,
    )
    w = real_estate_momentum_weights(closes, lookbacks=(21, 63, 126, 252), k=2)
    assert set(w.keys()) == {"A", "B"}
    assert pytest.approx(w["A"], 1e-4) == 0.5
    assert pytest.approx(w["B"], 1e-4) == 0.5


# -------------------------------------------------------------------------
# 16.5 Real Estate Inflation Hedge
# -------------------------------------------------------------------------


def test_real_estate_inflation():
    dates = pd.date_range("2020-01-01", periods=24, freq="MS")
    # Accelerating inflation: rising YoY
    cpi_rising = pd.Series(100.0 * np.exp(np.linspace(0, 0.5, 24) ** 2), index=dates)
    w_rising = real_estate_inflation_weights(as_of=dates[-1], cpi_series=cpi_rising, window=3)
    assert w_rising == {"VNQ": 1.0}

    # Decelerating inflation: falling YoY
    cpi_falling = pd.Series(np.linspace(100, 200, 24), index=dates)
    w_falling = real_estate_inflation_weights(as_of=dates[-1], cpi_series=cpi_falling, window=3)
    assert w_falling == {"IEF": 1.0}


# -------------------------------------------------------------------------
# 18.2 Crypto ANN
# -------------------------------------------------------------------------


def test_crypto_ann():
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=800, freq="D")
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.001, 0.02, 800)))
    closes = pd.DataFrame({"BTC-USD": prices}, index=dates)

    # Threshold set low to ensure a trigger
    w = crypto_ann_weights(
        closes,
        as_of=dates[-1],
        lags=(1, 3, 7, 14, 30),
        horizon=1,
        train=500,
        threshold=0.30,
    )
    assert "BTC-USD" in w
    assert pytest.approx(w["BTC-USD"], 1e-4) == 1.0

    # High threshold yields empty dict
    w_empty = crypto_ann_weights(
        closes,
        as_of=dates[-1],
        lags=(1, 3, 7, 14, 30),
        horizon=1,
        train=500,
        threshold=0.99,
    )
    assert w_empty == {}


# -------------------------------------------------------------------------
# 18.3 Crypto Sentiment
# -------------------------------------------------------------------------


def test_crypto_sentiment():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    fng = pd.Series([20, 25, 45, 55, 60, 70, 65, 50, 40, 20], index=dates)

    # as_of date is 2026-01-06 (index 5). Latest available date D < as_of is 2026-01-05 (val=60)
    w_mom = crypto_sentiment_weights(as_of=dates[5], fng_series=fng, long_above=50.0, contrarian=False)
    assert w_mom == {"BTC-USD": 1.0}

    w_mom_below = crypto_sentiment_weights(as_of=dates[3], fng_series=fng, long_above=50.0, contrarian=False)
    # At index 3 (2026-01-04), prev day D is index 2 (val=45 <= 50)
    assert w_mom_below == {}

    # Contrarian mode at index 1 (2026-01-02), prev day D is index 0 (val=20 < 25)
    w_contra = crypto_sentiment_weights(as_of=dates[1], fng_series=fng, buy_below=25.0, contrarian=True)
    assert w_contra == {"BTC-USD": 1.0}


# -------------------------------------------------------------------------
# 19.2 Fundamental Macro
# -------------------------------------------------------------------------


def test_fundamental_macro():
    dates = pd.date_range("2020-01-01", periods=30, freq="MS")
    # Growth up (accelerating INDPRO YoY), infl down (decelerating CPI YoY)
    indpro_up = pd.Series(100.0 * np.exp(np.linspace(0, 0.5, 30) ** 2), index=dates)
    cpi_down = pd.Series(np.linspace(100, 200, 30), index=dates)

    w = fundamental_macro_weights(dates[-1], indpro_up, cpi_down, window=3)
    assert set(w.keys()) == {"SPY", "EFA"}
    assert pytest.approx(w["SPY"], 1e-4) == 0.5


# -------------------------------------------------------------------------
# 19.3 Global Inflation
# -------------------------------------------------------------------------


def test_global_inflation():
    dates = pd.date_range("2018-01-01", periods=72, freq="MS")
    cpi_high = pd.Series(np.linspace(100, 200, 72), index=dates)  # High inflation YoY > 3.0

    closes = pd.DataFrame(
        {
            "TIP": np.linspace(100, 150, 72),
            "GLD": np.linspace(100, 160, 72),
            "DBC": np.linspace(100, 170, 72),
            "VNQ": np.linspace(100, 140, 72),
            "XLE": np.linspace(100, 180, 72),
            "SPY": np.linspace(100, 150, 72),
            "IEF": np.linspace(100, 110, 72),
        },
        index=dates,
    )

    w = global_inflation_weights(closes, as_of=dates[-1], cpi_series=cpi_high, threshold=3.0, window=36)
    assert pytest.approx(sum(w.values()), 1e-4) == 1.0
    for k in w:
        assert k in ("TIP", "GLD", "DBC", "VNQ", "XLE")

    # Low stable inflation -> defaults to 60/40 benchmark
    cpi_low = pd.Series([100.0] * 72, index=dates)
    w_low = global_inflation_weights(closes, as_of=dates[-1], cpi_series=cpi_low, threshold=3.0)
    assert pytest.approx(w_low["SPY"], 1e-4) == 0.60
    assert pytest.approx(w_low["IEF"], 1e-4) == 0.40


# -------------------------------------------------------------------------
# 19.5 Economic Announcement
# -------------------------------------------------------------------------


def test_econ_announcement():
    fomc_dates = [pd.Timestamp("2026-03-18"), pd.Timestamp("2026-05-06")]

    # 1 business day before FOMC
    w_day_before = econ_announcement_weights(
        as_of=pd.Timestamp("2026-03-17"),
        event_dates=fomc_dates,
        days_before=1,
        days_after=0,
    )
    assert w_day_before == {"SPY": 1.0}

    # Day of FOMC
    w_day_of = econ_announcement_weights(
        as_of=pd.Timestamp("2026-03-18"),
        event_dates=fomc_dates,
        days_before=1,
        days_after=0,
    )
    assert w_day_of == {"SPY": 1.0}

    # Day after FOMC (days_after=0, so flat)
    w_day_after = econ_announcement_weights(
        as_of=pd.Timestamp("2026-03-19"),
        event_dates=fomc_dates,
        days_before=1,
        days_after=0,
    )
    assert w_day_after == {}
