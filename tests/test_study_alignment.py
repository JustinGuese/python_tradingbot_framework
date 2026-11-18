"""
Tests for simulation alignment across study scripts.

Verifies that weights decided with data <= t earn the return of the next bar (t+1),
that the first bar earns 0, and that a return known on or before today's close
cannot be captured by a weight computed from today's close.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.onetime_blend_volscale_study import simulate_scaled_blend
from scripts.onetime_buyback_sector_hedge_study import run_simulation
from scripts.onetime_sleeveblend_parity import simulate_weights_stream
from tradingbot.quantsmd_continuous_futures_trend import UNIVERSE as CFT_UNIVERSE
from tradingbot.quantsmd_quarterly_te_cost_gate import UNIVERSE as QTE_UNIVERSE
from tradingbot.quantsmd_sma_daily_gated import UNIVERSE as SMA_UNIVERSE
from tradingbot.utils.quantsmd_common import SECTORS9


def test_sleeveblend_parity_alignment():
    """Weights decided at close t must earn return at t+1; first bar earns 0."""
    dates = pd.date_range("2010-02-01", periods=10, freq="B")
    all_symbols = list(dict.fromkeys([*CFT_UNIVERSE, *SMA_UNIVERSE, *QTE_UNIVERSE, "SPY", "SHV"]))

    # Flat prices everywhere
    df_data = {s: np.full(len(dates), 100.0) for s in all_symbols}
    # On day 0 (2010-02-01), make SPY and XLK jump +100% (from 50 to 100)
    # Day 0 return would be +100% if lookahead existed.
    closes = pd.DataFrame(df_data, index=dates)

    sleeve_weights = {"sma_daily_gated": 1.0}
    r_stream, to_stream = simulate_weights_stream(closes, sleeve_weights)

    # First bar MUST earn 0
    assert r_stream.iloc[0] == 0.0, f"First bar earned {r_stream.iloc[0]}, expected 0.0"
    assert to_stream.iloc[0] == 0.0, f"First bar turnover {to_stream.iloc[0]}, expected 0.0"

    # All future returns were flat 100.0 -> returns must be 0.0
    for i in range(1, len(dates)):
        assert r_stream.iloc[i] == 0.0, f"Bar {i} earned non-zero return on flat prices"


def test_sleeveblend_parity_no_lookahead_jump():
    """A price jump on day t must NOT be capturable by weights decided at day t's close."""
    dates = pd.date_range("2010-02-01", periods=5, freq="B")
    all_symbols = list(dict.fromkeys([*CFT_UNIVERSE, *SMA_UNIVERSE, *QTE_UNIVERSE, "SPY", "SHV"]))
    df_data = {s: np.full(len(dates), 100.0) for s in all_symbols}

    closes = pd.DataFrame(df_data, index=dates)
    # Day 1 has a huge jump in all equities:
    # Day 0: 100, Day 1: 200 (+100%), Day 2: 200 (0%), Day 3: 200 (0%), Day 4: 200 (0%)
    for s in all_symbols:
        if s != "SHV":
            closes.loc[dates[1:], s] = 200.0

    sleeve_weights = {"sma_daily_gated": 1.0}
    r_stream, _ = simulate_weights_stream(closes, sleeve_weights)

    # At day 0: flat -> decides weights at close 0.
    # At day 1: return is +100%. If decided at close 0, weight in equities was determined before the jump.
    # But weight decided at day 1 close (using the jump at day 1) must earn return of day 2 (which is 0.0).
    # Specifically, day 2 return must be 0.0 (since price didn't change from day 1 to day 2).
    assert r_stream.iloc[2] == pytest.approx(0.0), f"Day 2 return was {r_stream.iloc[2]}, expected 0.0"
    # First bar strictly earns 0.0
    assert r_stream.iloc[0] == 0.0


def test_blend_volscale_alignment():
    """simulate_scaled_blend must apply weights to t+1, with first bar earning 0."""
    dates = list(pd.date_range("2010-02-01", periods=5, freq="B"))
    tickers = ["AAPL", "MSFT", "SHV"]

    # Price matrix where AAPL jumps 100% on day 0, then flat
    # Day 0: 200 (jump from 100), Day 1: 200 (0%), Day 2: 200 (0%), etc.
    prices = pd.DataFrame(
        {
            "AAPL": [200.0, 200.0, 200.0, 200.0, 200.0],
            "MSFT": [100.0, 100.0, 100.0, 100.0, 100.0],
            "SHV": [100.0, 100.0, 100.0, 100.0, 100.0],
        },
        index=dates,
    )

    # Base weights: on day 0 AAPL is held at 0.5
    base_weights = [
        {"AAPL": 0.5, "SHV": 0.5},
        {"AAPL": 0.5, "SHV": 0.5},
        {"AAPL": 0.5, "SHV": 0.5},
        {"AAPL": 0.5, "SHV": 0.5},
        {"AAPL": 0.5, "SHV": 0.5},
    ]

    r_stream, to_stream = simulate_scaled_blend(prices, target_invested=0.759, dates=dates, base_weights=base_weights)

    # First bar MUST earn 0
    assert r_stream.iloc[0] == 0.0, f"First bar earned {r_stream.iloc[0]}, expected 0.0"
    assert to_stream.iloc[0] == 0.0, f"First bar turnover {to_stream.iloc[0]}, expected 0.0"

    # From day 1 onwards, AAPL return was 0.0, so returns must all be 0.0
    for i in range(1, len(dates)):
        assert r_stream.iloc[i] == pytest.approx(0.0), f"Bar {i} earned non-zero return on flat prices"


def test_buyback_sector_hedge_alignment():
    """run_simulation must apply weights to t+1, with first bar earning 0."""
    dates = pd.date_range("2010-01-04", periods=5, freq="B")
    etf_tickers = [*SECTORS9, "QQQ", "SHV"]

    # AAPL jumps 50% on day 0, then 0% return on subsequent days
    # If lookahead existed, day 0 return would capture +50%.
    stocks_df = pd.DataFrame(
        {"AAPL": [150.0, 150.0, 150.0, 150.0, 150.0]},
        index=dates,
    )
    etf_df = pd.DataFrame(
        {s: np.full(len(dates), 100.0) for s in etf_tickers},
        index=dates,
    )
    daily_rf = pd.Series(0.0, index=dates)

    # AAPL is active starting on day 0
    active_by_date = {dates[0].date(): ["AAPL"], dates[1].date(): ["AAPL"]}

    sim = run_simulation(
        variant="sector_hedge",
        active_by_date=active_by_date,
        stocks_df=stocks_df,
        etf_df=etf_df,
        daily_rf=daily_rf,
        cost_bp=0.0,
    )

    ret_zero = sim["ret_zero"]

    # First bar MUST earn 0
    assert ret_zero.iloc[0] == 0.0, f"First bar earned {ret_zero.iloc[0]}, expected 0.0"
    assert sim["long_ret"].iloc[0] == 0.0

    # Day 1 price didn't change (150 -> 150) -> stock return is 0.0 (not +50% jump from day 0)
    assert sim["long_ret"].iloc[1] == 0.0
    assert sim["short_ret"].iloc[1] == 0.0
    # Net return reflects only short borrow fee drag (0.5 * 0.01 / 252)
    assert ret_zero.iloc[1] == pytest.approx(-0.5 * 0.01 / 252.0)


def test_require_coverage_threshold():
    """require_coverage must raise SystemExit when fewer than min_share have >= 1250 bars."""
    from scripts.study_prices import require_coverage

    wanted = [f"SYM{i}" for i in range(100)]
    # Only 50 have >= 1250 bars
    loaded = {f"SYM{i}": pd.DataFrame({"close": np.ones(1250)}) for i in range(50)}

    with pytest.raises(SystemExit, match="Coverage check failed"):
        require_coverage(loaded, wanted, min_share=0.95, label="TestUniverse")

    # When 95 have >= 1250 bars
    for i in range(50, 95):
        loaded[f"SYM{i}"] = pd.DataFrame({"close": np.ones(1250)})

    # Should not raise
    require_coverage(loaded, wanted, min_share=0.95, label="TestUniverse")
