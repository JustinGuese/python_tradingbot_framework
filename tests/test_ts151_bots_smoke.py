"""TS151 Bot Registry Smoke & Verification Tests (PLAN-ts151.md S11).

Verifies:
1. Every bot file in tradingbot/ts151_*bot.py defines a TS151Bot subclass.
2. Every bot file has a registered entry in helm/tradingbots/values.yaml with suspend: false.
3. Every bot instantiates and executes targetWeights({}) without raising, returning
   None or a valid target weights dictionary conforming to all constraints.
4. Unit checks for key signals:
   - IBS enters below 0.2 and exits above 0.8
   - Low-volatility longs the lowest-volatility asset
   - DV01-neutral butterfly achieves net DV01 ≈ 0
   - KNN classifier does not look ahead into future bars (handles NaN-poisoned future)
5. Backtestability check:
   - Runs ts151_sectormomentumbot (long-only) and ts151_lowvolatilitybot (long/short)
     through backtest_bot on synthetic data with benchmark_close.
   - Asserts finite alpha, alpha_t, and beta, and verifies short exposure occurred.
"""

from __future__ import annotations

import glob
import importlib
import inspect
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml

from tradingbot.utils.botclass import Bot
from tradingbot.utils.ts151 import common, data_api, fred, signals_commod, signals_macro
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.universes import SP500, SP500_SECTOR

REPO_ROOT = Path(__file__).resolve().parent.parent
VALUES_YAML_PATH = REPO_ROOT / "helm" / "tradingbots" / "values.yaml"
BOT_FILES = sorted(glob.glob(str(REPO_ROOT / "tradingbot" / "ts151_*bot.py")))

# 20 names for stock bot universe override
STOCK_UNIVERSE = tuple(SP500[:20])
DATES_1500 = pd.bdate_range(end="2024-04-01", periods=1500)


# Mock implementations
def _mock_load_fundamentals(tickers=None, tags=None):
    t_list = list(tickers) if tickers else list(STOCK_UNIVERSE)
    tag_list = (
        list(tags)
        if tags
        else [
            "StockholdersEquity",
            "EntityCommonStockSharesOutstanding",
            "NetIncomeLoss",
            "Assets",
            "Liabilities",
            "EarningsPerShareDiluted",
        ]
    )
    rows = []
    for t in t_list:
        for tag in tag_list:
            for p_end, filed in [("2023-09-30", "2023-11-05"), ("2023-12-31", "2024-02-15")]:
                rows.append({
                    "cik": 1000,
                    "ticker": t,
                    "tag": tag,
                    "unit": "USD",
                    "period_end": pd.to_datetime(p_end),
                    "filed": pd.to_datetime(filed),
                    "value": 100.0,
                    "form": "10-Q",
                    "fy": 2023,
                    "fp": "Q3",
                })
    return pd.DataFrame(rows)


def _mock_load_cot(codes=None):
    from tradingbot.utils.ts151.universes import COT_CODES

    rows = []
    for m_code in (codes or list(COT_CODES.values())):
        for d in pd.date_range("2023-01-01", "2024-04-01", freq="W-TUE"):
            rows.append({
                "market_code": m_code,
                "report_date": d,
                "market_name": "Test",
                "open_interest": 100000.0,
                "comm_long": 40000.0,
                "comm_short": 30000.0,
                "noncomm_long": 20000.0,
                "noncomm_short": 10000.0,
                "nonrep_long": 5000.0,
                "nonrep_short": 5000.0,
            })
    return pd.DataFrame(rows)


def _mock_load_merger_events(start=None):
    return pd.DataFrame([
        {
            "accession": "_HEARTBEAT",
            "cik": 0,
            "ticker": "_HB",
            "company_name": "Heartbeat",
            "form": "8-K",
            "filed_at": pd.to_datetime("2024-04-01"),
            "created_at": pd.to_datetime("2024-04-01"),
        },
        {
            "accession": "0001-23-456",
            "cik": 1000,
            "ticker": "AAPL",
            "company_name": "Apple",
            "form": "SC 13D",
            "filed_at": pd.to_datetime("2024-03-15"),
            "created_at": pd.to_datetime("2024-03-15"),
        },
    ])


def _mock_fred_series(series_id, lag_days=None, start="1990-01-01", **kwargs):
    return pd.Series(4.0, index=DATES_1500)


def _mock_load_fred_rates(lag_days=25, **kwargs):
    cols = ["EUR", "GBP", "AUD", "NZD", "JPY", "CAD", "CHF", "NOK", "SEK", "USD"]
    return pd.DataFrame({c: 0.04 for c in cols}, index=DATES_1500)


@contextmanager
def _mock_db_session():
    yield MagicMock()


# Pre-patch loaders before module imports
data_api.load_fundamentals = _mock_load_fundamentals
data_api.load_cot = _mock_load_cot
data_api.load_merger_events = _mock_load_merger_events
data_api.get_db_session = _mock_db_session
fred.fred_series = _mock_fred_series
signals_macro.fetch_crypto_fng = lambda **kwargs: pd.Series(50.0, index=DATES_1500)

import tradingbot.utils.fx_carry as fx_carry
fx_carry.load_fred_rates = _mock_load_fred_rates

import tradingbot.utils.db as db_mod
db_mod.get_db_session = _mock_db_session

# Pre-discover all bot classes for test parametrization
DISCOVERED_BOTS: list[tuple[str, type[TS151Bot]]] = []
for file_path in BOT_FILES:
    stem = Path(file_path).stem
    mod_name = f"tradingbot.{stem}"
    mod = importlib.import_module(mod_name)
    classes = [
        cls
        for _, cls in inspect.getmembers(mod, inspect.isclass)
        if issubclass(cls, TS151Bot) and cls is not TS151Bot and cls.__module__ == mod_name
    ]
    for cls in classes:
        DISCOVERED_BOTS.append((stem, cls))


@pytest.fixture(autouse=True)
def patch_all_external_dependencies(monkeypatch):
    """Patch database, network, FRED, and external data sources for smoke tests."""
    # 1. Patch Bot.__init__ to avoid DB interactions
    monkeypatch.setattr(Bot, "__init__", lambda self, *args, **kwargs: None)

    # 2. Patch get_db_session across db module and data_api
    monkeypatch.setattr(db_mod, "get_db_session", _mock_db_session)
    monkeypatch.setattr(data_api, "get_db_session", _mock_db_session)

    # 3. Patch data_api loaders
    monkeypatch.setattr(data_api, "load_fundamentals", _mock_load_fundamentals)
    monkeypatch.setattr(data_api, "load_cot", _mock_load_cot)
    monkeypatch.setattr(data_api, "load_merger_events", _mock_load_merger_events)

    # 4. Patch FRED series and rates
    monkeypatch.setattr(fred, "fred_series", _mock_fred_series)
    monkeypatch.setattr(fx_carry, "load_fred_rates", _mock_load_fred_rates)

    # 5. Patch alternative.me crypto sentiment
    monkeypatch.setattr(signals_macro, "fetch_crypto_fng", lambda **kwargs: pd.Series(50.0, index=DATES_1500))

    # 6. Patch 9.1 roll yield live contract fetcher to return None (backtest mode)
    import tradingbot.ts151_rollyieldbot as ryb
    monkeypatch.setattr(ryb, "fetch_live_contracts", lambda: None)

    # 7. Patch implied volatility bot surface cache loader
    import tradingbot.ts151_impliedvolatilitybot as ivb

    def _mock_get_vol_surface(self):
        return pd.DataFrame([
            {
                "underlying": t,
                "snapshot_date": pd.to_datetime("2024-04-01"),
                "atm_iv_30": 0.20 + 0.005 * i,
                "iv_25c_30": 0.21,
                "iv_25p_30": 0.22,
            }
            for i, t in enumerate(self.universe_list)
        ])

    monkeypatch.setattr(ivb.TS151ImpliedVolatilityBot, "_get_vol_surface", _mock_get_vol_surface)
    monkeypatch.setattr(ivb, "get_db_session", _mock_db_session)

    # 8. Ensure bot module local references are also patched
    for stem, cls in DISCOVERED_BOTS:
        mod = inspect.getmodule(cls)
        if mod is not None:
            if hasattr(mod, "load_fundamentals"):
                monkeypatch.setattr(mod, "load_fundamentals", _mock_load_fundamentals)
            if hasattr(mod, "load_cot"):
                monkeypatch.setattr(mod, "load_cot", _mock_load_cot)
            if hasattr(mod, "load_merger_events"):
                monkeypatch.setattr(mod, "load_merger_events", _mock_load_merger_events)
            if hasattr(mod, "fetch_live_contracts"):
                monkeypatch.setattr(mod, "fetch_live_contracts", lambda: None)
            if hasattr(mod, "get_db_session"):
                monkeypatch.setattr(mod, "get_db_session", _mock_db_session)


# ---------------------------------------------------------------------------
# 1. Values.yaml registration check
# ---------------------------------------------------------------------------


def test_values_yaml_registration():
    """Assert every bot file has an entry in helm/tradingbots/values.yaml with suspend: false."""
    assert len(BOT_FILES) > 0, "No ts151 bot files discovered"
    assert VALUES_YAML_PATH.exists(), f"values.yaml not found at {VALUES_YAML_PATH}"

    with open(VALUES_YAML_PATH) as f:
        data = yaml.safe_load(f)

    bots_in_helm = {b["name"]: b for b in data.get("bots", []) if "name" in b}

    missing_bots = []
    suspended_bots = []

    for file_path in BOT_FILES:
        stem = Path(file_path).stem
        if stem not in bots_in_helm:
            missing_bots.append(stem)
        elif bots_in_helm[stem].get("suspend") is not False:
            suspended_bots.append(stem)

    assert not missing_bots, f"Bot files missing from values.yaml: {missing_bots}"
    assert not suspended_bots, f"Bot files with suspend!=false in values.yaml: {suspended_bots}"


# ---------------------------------------------------------------------------
# 2. Bot Discovery and Subclass Verification
# ---------------------------------------------------------------------------


def test_all_discovered_bot_classes_are_ts151_subclasses():
    """Discover every tradingbot/ts151_*bot.py and assert each defines a TS151Bot subclass."""
    assert len(BOT_FILES) >= 60, f"Expected at least 60 bot files, found {len(BOT_FILES)}"
    assert len(DISCOVERED_BOTS) >= len(BOT_FILES)

    for stem, cls in DISCOVERED_BOTS:
        assert issubclass(cls, TS151Bot), f"{cls.__name__} in {stem} is not a subclass of TS151Bot"
        assert hasattr(cls, "BOT_NAME"), f"{cls.__name__} missing BOT_NAME"
        assert hasattr(cls, "DEFAULT_UNIVERSE"), f"{cls.__name__} missing DEFAULT_UNIVERSE"
        assert hasattr(cls, "ALLOW_SHORT"), f"{cls.__name__} missing ALLOW_SHORT"
        assert hasattr(cls, "MAX_GROSS"), f"{cls.__name__} missing MAX_GROSS"


# ---------------------------------------------------------------------------
# 3. Target Weights Contract Smoke Test across All Bots
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem,bot_cls", DISCOVERED_BOTS)
def test_bot_target_weights_smoke(stem: str, bot_cls: type[TS151Bot]):
    """Construct each bot with synthetic data and call targetWeights({}).

    Asserts:
    - Result is None or a dict.
    - Keys are a subset of tradeable tickers (+ SHV if cash parking).
    - All weight values are finite numbers.
    - Total gross weight <= MAX_GROSS + 1e-6.
    - Negative weights appear only if ALLOW_SHORT and the symbol is in SHORTABLE.
    """
    # Use universe override <= 20 names for stock bots
    raw_u = getattr(bot_cls, "DEFAULT_UNIVERSE", None)
    is_stock_bot = raw_u in ("sp500", "sp100") or (
        isinstance(raw_u, (list, tuple)) and len(raw_u) > 20
    )
    kwargs: dict[str, Any] = {"universe": STOCK_UNIVERSE} if is_stock_bot else {}

    bot = bot_cls(**kwargs)

    # Generate synthetic random-walk OHLCV for all bot tickers
    np.random.seed(hash(stem) % (2**32))
    datas = {}
    for t in bot.tickers:
        rets = np.random.normal(0.0005, 0.012, len(DATES_1500))
        prices = 100.0 * np.exp(np.cumsum(rets))
        datas[t] = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "close": prices,
                "volume": 10000.0,
            },
            index=DATES_1500,
        )

    bot.datas = datas
    bot.datasettings = (bot.interval, bot.period)

    # Call targetWeights with empty rows (representing flat initial state)
    weights = bot.targetWeights({})

    if weights is not None:
        assert isinstance(weights, dict), f"{bot_cls.__name__}: targetWeights returned {type(weights)}"
        allowed_keys = set(bot.tickers) | {"SHV"}
        assert set(weights.keys()).issubset(
            allowed_keys
        ), f"{bot_cls.__name__}: returned keys {set(weights.keys()) - allowed_keys} not in universe"

        for k, v in weights.items():
            assert np.isfinite(v), f"{bot_cls.__name__}: non-finite weight for {k}: {v}"
            if v < 0:
                assert bot.ALLOW_SHORT, f"{bot_cls.__name__}: negative weight {v} for {k} with ALLOW_SHORT=False"
                assert k in getattr(bot, "SHORTABLE", ()), f"{bot_cls.__name__}: symbol {k} not in SHORTABLE"

        gross = sum(abs(v) for v in weights.values())
        max_gross = float(getattr(bot, "MAX_GROSS", 1.0))
        assert gross <= max_gross + 1e-6, f"{bot_cls.__name__}: gross {gross} exceeds MAX_GROSS {max_gross}"


# ---------------------------------------------------------------------------
# 4. Signal Unit Checks
# ---------------------------------------------------------------------------


def test_signal_ibs_hysteresis():
    """Verify IBS signal enters below 0.2 and exits above 0.8."""
    from tradingbot.utils.ts151.signals_etfs import ibs_reversal

    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    highs = [10.0, 10.0, 10.0, 10.0, 10.0]
    lows = [0.0, 0.0, 0.0, 0.0, 0.0]
    closes = [5.0, 1.0, 5.0, 9.0, 5.0]

    # Bar 0: IBS = 0.5 (neutral) -> flat ({})
    df0 = pd.DataFrame({"high": highs[:1], "low": lows[:1], "close": closes[:1]}, index=dates[:1])
    ctx0 = Ctx(closes=pd.DataFrame({"ETF": closes[:1]}, index=dates[:1]), datas={"ETF": df0}, as_of=dates[0], universe=("ETF",), bot=None)
    assert ibs_reversal(ctx0, low=0.2, high=0.8) == {}

    # Bar 1: IBS = 0.1 (<0.2) -> enters long
    df1 = pd.DataFrame({"high": highs[:2], "low": lows[:2], "close": closes[:2]}, index=dates[:2])
    ctx1 = Ctx(closes=pd.DataFrame({"ETF": closes[:2]}, index=dates[:2]), datas={"ETF": df1}, as_of=dates[1], universe=("ETF",), bot=None)
    assert ibs_reversal(ctx1, low=0.2, high=0.8) == {"ETF": 1.0}

    # Bar 2: IBS = 0.5 (between 0.2 and 0.8) -> holds long
    df2 = pd.DataFrame({"high": highs[:3], "low": lows[:3], "close": closes[:3]}, index=dates[:3])
    ctx2 = Ctx(closes=pd.DataFrame({"ETF": closes[:3]}, index=dates[:3]), datas={"ETF": df2}, as_of=dates[2], universe=("ETF",), bot=None)
    assert ibs_reversal(ctx2, low=0.2, high=0.8) == {"ETF": 1.0}

    # Bar 3: IBS = 0.9 (>0.8) -> exits to flat
    df3 = pd.DataFrame({"high": highs[:4], "low": lows[:4], "close": closes[:4]}, index=dates[:4])
    ctx3 = Ctx(closes=pd.DataFrame({"ETF": closes[:4]}, index=dates[:4]), datas={"ETF": df3}, as_of=dates[3], universe=("ETF",), bot=None)
    assert ibs_reversal(ctx3, low=0.2, high=0.8) == {}

    # Bar 4: IBS = 0.5 (between 0.2 and 0.8) -> remains flat
    df4 = pd.DataFrame({"high": highs[:5], "low": lows[:5], "close": closes[:5]}, index=dates[:5])
    ctx4 = Ctx(closes=pd.DataFrame({"ETF": closes[:5]}, index=dates[:5]), datas={"ETF": df4}, as_of=dates[4], universe=("ETF",), bot=None)
    assert ibs_reversal(ctx4, low=0.2, high=0.8) == {}


def test_signal_low_volatility_longs_lowest_vol():
    """Verify low-volatility ranking longs the lowest volatility asset."""
    from tradingbot.utils.ts151.signals_stocks import compute_low_volatility_weights

    dates = pd.bdate_range("2020-01-01", periods=300)
    np.random.seed(42)

    rets_low = np.random.normal(0.0001, 0.004, len(dates))
    rets_med = np.random.normal(0.0001, 0.015, len(dates))
    rets_high = np.random.normal(0.0001, 0.050, len(dates))

    closes = pd.DataFrame(
        {
            "LOW_VOL": 100.0 * np.exp(np.cumsum(rets_low)),
            "MED_VOL": 100.0 * np.exp(np.cumsum(rets_med)),
            "HIGH_VOL": 100.0 * np.exp(np.cumsum(rets_high)),
        },
        index=dates,
    )

    # Long-only test
    w_lo = compute_low_volatility_weights(closes, window=252, frac=0.34, long_only=True, gross=1.0)
    assert w_lo is not None
    assert "LOW_VOL" in w_lo and w_lo["LOW_VOL"] > 0
    assert "HIGH_VOL" not in w_lo

    # Long/short test
    w_ls = compute_low_volatility_weights(closes, window=252, frac=0.34, long_only=False, gross=2.0)
    assert w_ls is not None
    assert w_ls["LOW_VOL"] > 0
    assert w_ls["HIGH_VOL"] < 0


def test_signal_dv01_neutral_butterfly():
    """Verify DV01-neutral butterfly weights produce net DV01 ≈ 0."""
    from tradingbot.utils.ts151.signals_rates import compute_butterfly_weights

    durations = {"IEF": 7.5, "SHY": 1.9, "TLT": 17.0}

    # 1. Direct common.dv01_neutral
    w_raw = common.dv01_neutral("IEF", ("SHY", "TLT"), durations)
    net_raw = sum(w_raw[k] * durations[k] for k in w_raw)
    assert math.isclose(net_raw, 0.0, abs_tol=1e-8)

    # 2. compute_butterfly_weights fifty_fifty
    w_norm = compute_butterfly_weights("IEF", ("SHY", "TLT"), durations, weighting="fifty_fifty", gross=1.0)
    net_norm = sum(w_norm[k] * durations[k] for k in w_norm)
    assert math.isclose(net_norm, 0.0, abs_tol=1e-8)


def test_signal_knn_no_future_lookahead():
    """Verify KNN features never use bars after as_of with NaN-poisoned future bars."""
    from tradingbot.utils.ts151.signals_stocks import compute_knn_weights

    dates = pd.bdate_range("2020-01-01", periods=600)
    np.random.seed(42)

    universe = ["XLK", "XLF", "XLE"]
    data = {}
    for t in universe:
        rets = np.random.normal(0.0005, 0.015, len(dates))
        data[t] = 100.0 * np.exp(np.cumsum(rets))

    closes = pd.DataFrame(data, index=dates)

    # as_of is at bar index 550
    as_of = dates[550]
    closes_poisoned = closes.copy()
    # Poison all future bars after bar 550 with NaN
    closes_poisoned.iloc[551:] = np.nan

    weights = compute_knn_weights(
        closes_poisoned,
        universe=universe,
        as_of=as_of,
        k=10,
        train=252,
        horizon=5,
    )

    assert weights is not None
    assert all(np.isfinite(v) for v in weights.values())
    assert all(k in universe for k in weights.keys())


# ---------------------------------------------------------------------------
# 5. Backtestability Check
# ---------------------------------------------------------------------------


def test_backtestability_sector_momentum_and_low_vol():
    """Run TS151SectorMomentumBot and TS151LowVolatilityBot through backtest_bot.

    Asserts:
    - Both runs complete and return finite alpha, alpha_t, and beta.
    - Low-volatility long/short run actually held a negative position at some bar.
    """
    from tradingbot.ts151_lowvolatilitybot import TS151LowVolatilityBot
    from tradingbot.ts151_sectormomentumbot import TS151SectorMomentumBot
    from tradingbot.utils.backtest import backtest_bot

    dates = pd.bdate_range("2022-01-01", periods=350)
    np.random.seed(42)

    bench = pd.Series(
        100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, len(dates)))),
        index=dates,
    )

    # 1. SectorMomentumBot (Long-Only)
    bot_sec = TS151SectorMomentumBot()
    data_sec = {}
    for t in bot_sec.tickers:
        rets = np.random.normal(0.0005, 0.015, len(dates))
        prices = 100.0 * np.exp(np.cumsum(rets))
        data_sec[t] = pd.DataFrame(
            {"close": prices, "open": prices, "high": prices, "low": prices, "volume": 1000},
            index=dates,
        )

    res_sec = backtest_bot(
        bot_sec,
        data=data_sec,
        benchmark_close=bench,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=False,
        return_series=True,
    )

    assert res_sec["alpha"] is not None and np.isfinite(res_sec["alpha"])
    assert res_sec["alpha_t"] is not None and np.isfinite(res_sec["alpha_t"])
    assert res_sec["beta"] is not None and np.isfinite(res_sec["beta"])

    # 2. LowVolatilityBot (Long/Short, 20 synthetic tickers)
    synth_universe = tuple(f"STK{i:02d}" for i in range(20))
    bot_vol = TS151LowVolatilityBot(universe=synth_universe, long_only=False, window=126)
    data_vol = {}
    for i, t in enumerate(bot_vol.tickers):
        vol = 0.005 + 0.003 * i
        rets = np.random.normal(0.0002, vol, len(dates))
        prices = 100.0 * np.exp(np.cumsum(rets))
        data_vol[t] = pd.DataFrame(
            {"close": prices, "open": prices, "high": prices, "low": prices, "volume": 1000},
            index=dates,
        )

    res_vol = backtest_bot(
        bot_vol,
        data=data_vol,
        benchmark_close=bench,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=False,
        return_series=True,
    )

    assert res_vol["alpha"] is not None and np.isfinite(res_vol["alpha"])
    assert res_vol["alpha_t"] is not None and np.isfinite(res_vol["alpha_t"])
    assert res_vol["beta"] is not None and np.isfinite(res_vol["beta"])

    # Verify that the long/short run held a negative position at some bar
    short_trades = [tr for tr in res_vol["trades"] if tr["side"] == "short"]
    assert len(short_trades) > 0, "LowVolatilityBot long/short backtest did not execute any short positions"
