"""Institutional TA features, universe hygiene and InstitutionalFlowBot's book logic."""

import numpy as np
import pandas as pd
import pytest

from tradingbot.institutionalflowbot import BENCHMARK, InstitutionalFlowBot
from tradingbot.utils.institutional_ta import (
    accumulation_score,
    add_institutional_features,
    cross_sectional_z,
    net_accumulation_days,
    passes_mandate,
    quarter_anchored_vwap,
    rolling_vwap,
    up_down_volume_ratio,
)
from tradingbot.utils.universes import SP100, SP100_EXCLUDED


def _ohlcv(close, volume=None, start="2024-01-01"):
    close = np.asarray(close, dtype=float)
    volume = np.full(len(close), 1_000_000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame(
        {
            "timestamp": pd.bdate_range(start, periods=len(close)),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volume,
        }
    )


# ---------------------------------------------------------------- features


def test_rolling_vwap_weights_by_volume():
    df = _ohlcv([10.0, 20.0], volume=[1.0, 3.0])
    df["high"] = df["low"] = df["close"]  # typical price == close
    assert rolling_vwap(df, 2).iloc[-1] == pytest.approx((10 * 1 + 20 * 3) / 4)
    assert np.isnan(rolling_vwap(df, 2).iloc[0])  # warmup stays NaN


def test_quarter_vwap_resets_on_the_first_bar_of_a_quarter():
    df = _ohlcv(np.linspace(100, 130, 70), start="2024-02-01")
    df["high"] = df["low"] = df["close"]
    vwap = quarter_anchored_vwap(df)
    first_q2 = df.index[df["timestamp"] >= "2024-04-01"][0]
    assert vwap.iloc[first_q2] == pytest.approx(df["close"].iloc[first_q2])
    # ...and is cumulative inside the quarter.
    assert vwap.iloc[first_q2 + 1] == pytest.approx(df["close"].iloc[first_q2 : first_q2 + 2].mean())


def test_up_down_volume_ratio():
    # Alternating up (vol 3) / down (vol 1) days.
    close = [100, 101, 100, 101, 100, 101]
    volume = [1, 3, 1, 3, 1, 3]
    ratio = up_down_volume_ratio(_ohlcv(close, volume), window=4)
    assert ratio.iloc[-1] == pytest.approx(6 / 2)


def test_net_accumulation_days_counts_moves_on_rising_volume():
    close = [100, 101, 102, 101, 101.05]
    volume = [10, 20, 15, 30, 40]
    # bar1 up+heavier = acc, bar2 up but lighter = none, bar3 down+heavier = dist,
    # bar4 +0.05% (< 0.2%) on heavier volume = none.
    net = net_accumulation_days(_ohlcv(close, volume), window=4)
    assert net.iloc[-1] == pytest.approx(0.0)
    net = net_accumulation_days(_ohlcv(close[:3], volume[:3]), window=2)
    assert net.iloc[-1] == pytest.approx(1.0)


def test_features_have_no_look_ahead():
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 400))
    volume = rng.uniform(5e5, 2e6, 400)
    full = add_institutional_features(_ohlcv(close, volume))
    cut = add_institutional_features(_ohlcv(close[:300], volume[:300]))
    cols = [c for c in full.columns if c.startswith("inst_")]
    pd.testing.assert_frame_equal(full.loc[:299, cols], cut[cols])


def test_warmup_is_nan_not_a_pass():
    feats = add_institutional_features(_ohlcv(np.linspace(50, 100, 150)))
    assert np.isnan(feats["inst_sma200"].iloc[-1])
    assert not passes_mandate(feats.iloc[-1], min_adv_usd=0)


def test_mandate_passes_a_liquid_uptrend_and_fails_an_illiquid_one():
    close = np.linspace(50, 150, 300)
    row = add_institutional_features(_ohlcv(close, np.full(300, 1e6))).iloc[-1]
    assert passes_mandate(row, min_adv_usd=50e6)  # ~$150M/day
    assert not passes_mandate(row, min_adv_usd=500e6)
    down = add_institutional_features(_ohlcv(close[::-1], np.full(300, 1e6))).iloc[-1]
    assert not passes_mandate(down, min_adv_usd=0)


def test_cross_sectional_z_clips_and_neutralises_missing():
    z = cross_sectional_z({"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0, "e": 1000.0, "f": None}, clip=1.5)
    assert z["e"] == pytest.approx(1.5)
    assert z["f"] == 0.0
    assert cross_sectional_z({"a": 2.0, "b": 2.0}) == {"a": 0.0, "b": 0.0}


def test_accumulation_score_prefers_the_accumulated_name():
    base = {
        "close": 100.0,
        "inst_rs": 0.1,
        "inst_ud_vol_ratio50": 1.0,
        "volume_cmf": 0.0,
        "inst_obv_slope20": 0.0,
        "inst_accum_net25": 0.0,
        "inst_vwap_qtd_dist": 0.0,
        "inst_ret20": 0.02,
    }
    strong = dict(base, inst_ud_vol_ratio50=2.0, volume_cmf=0.2, inst_obv_slope20=0.5, inst_accum_net25=5)
    rows = {"FLAT": pd.Series(base), "ACC": pd.Series(strong), "MID": pd.Series(dict(base, volume_cmf=0.1))}
    scores = accumulation_score(rows)
    assert max(scores, key=scores.get) == "ACC"


# ---------------------------------------------------------------- universe


def test_sp100_symbols_are_copier_tradeable_and_unique():
    """Collective2 rejects '.', '-' and '^' symbols (see test_taregimemultiassetbot)."""
    assert len(SP100) == len(set(SP100)) >= 95
    assert not [s for s in SP100 if any(c in s for c in ".-^")]
    assert not set(SP100) & set(SP100_EXCLUDED)
    assert BENCHMARK not in SP100


# ---------------------------------------------------------------- bot


def _bot(**params):
    bot = InstitutionalFlowBot.__new__(InstitutionalFlowBot)
    bot.bot_name = "InstitutionalFlowBot"
    bot.tickers = ["AAA", "BBB", "CCC", BENCHMARK]
    bot.benchmark_tickers = [BENCHMARK]
    bot.top_n = params.get("top_n", 2)
    bot.rebalance_weekday = params.get("rebalance_weekday", 4)
    bot.market_gate = params.get("market_gate", True)
    bot.min_adv_usd = 50e6
    bot.min_price = 10.0
    return bot


def _row(ts, close=150.0, sma200=100.0, trend=1.0, cmf=0.0, **extra):
    return pd.Series(
        {
            "close": close,
            "inst_sma200": sma200,
            "inst_trend_ok": trend,
            "inst_adv_usd": 1e9,
            "inst_rs": 0.2,
            "inst_ud_vol_ratio50": 1.2,
            "volume_cmf": cmf,
            "inst_obv_slope20": 0.1,
            "inst_accum_net25": 1.0,
            "inst_vwap_qtd_dist": 0.02,
            "inst_ret20": 0.03,
            **extra,
        },
        name=pd.Timestamp(ts),  # backtest shape: timestamp is the index label
    )


FRIDAY, THURSDAY = "2026-09-18", "2026-09-17"


def test_bot_holds_off_rebalance_day():
    rows = {t: _row(THURSDAY) for t in ["AAA", "BBB", "CCC", BENCHMARK]}
    assert _bot().targetWeights(rows) is None


def test_bot_reads_the_live_timestamp_column():
    live = {t: _row(FRIDAY).rename(0) for t in ["AAA", "BBB", "CCC", BENCHMARK]}
    for r in live.values():
        r["timestamp"] = pd.Timestamp(THURSDAY)
    assert _bot().targetWeights(live) is None


def test_bot_goes_to_cash_below_the_benchmark_200dma():
    rows = {t: _row(FRIDAY) for t in ["AAA", "BBB", "CCC"]}
    rows[BENCHMARK] = _row(FRIDAY, close=90.0, sma200=100.0)
    assert _bot().targetWeights(rows) == {}
    assert _bot(market_gate=False).targetWeights(rows) != {}


def test_bot_picks_top_n_at_one_over_n_and_leaves_the_rest_in_cash():
    rows = {
        "AAA": _row(FRIDAY, cmf=0.3),
        "BBB": _row(FRIDAY, cmf=0.1),
        "CCC": _row(FRIDAY, cmf=-0.2),
        BENCHMARK: _row(FRIDAY),
    }
    assert _bot(top_n=2).targetWeights(rows) == {"AAA": 0.5, "BBB": 0.5}
    # Only two pass the trend mandate: 2 x 1/4, half the book in cash.
    rows["CCC"] = _row(FRIDAY, trend=0.0)
    assert _bot(top_n=4).targetWeights(rows) == {"AAA": 0.25, "BBB": 0.25}


def test_bot_daily_variant_rebalances_any_weekday():
    rows = {t: _row(THURSDAY) for t in ["AAA", "BBB", "CCC", BENCHMARK]}
    assert _bot(rebalance_weekday=None).targetWeights(rows)
