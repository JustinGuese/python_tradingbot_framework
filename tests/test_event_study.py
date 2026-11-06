"""
Tests for event study utilities (tradingbot/utils/event_study.py).
Focus: look-ahead guard, judge criteria, flat returns on cash days, and edge cases.
"""

from datetime import date, datetime, time

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.event_study import (
    abnormal_returns,
    event_strategy_returns,
    forward_returns,
    judge,
)


def test_entry_after_close_uses_next_session_look_ahead_guard():
    """
    Look-ahead guard:
    An event before the close enters at today's close.
    An event after the close MUST use the next session's close, not today.
    """
    # 2026-03-09 is Monday, 2026-03-10 is Tuesday, 2026-03-11 is Wednesday.
    # Regular NYSE close is 16:00 EDT (20:00 UTC).
    dates = pd.to_datetime(["2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12"])
    prices = pd.Series([100.0, 105.0, 110.0, 120.0], index=dates)

    # Event 1: Monday at 14:00 UTC (10:00 AM EDT, before close).
    # Entry must be Monday close ($100.0). Horizon 1 is Tuesday ($105.0) -> +5%.
    fwd_before = forward_returns(prices, pd.Timestamp("2026-03-09 14:00:00+00:00"), [1, 2])
    assert fwd_before[1] == pytest.approx(0.05)
    assert fwd_before[2] == pytest.approx(0.10)

    # Event 2: Monday at 21:00 UTC (17:00 EDT, after close).
    # Entry must be Tuesday close ($105.0), NOT Monday.
    # Horizon 1 is Wednesday ($110.0) -> (110 - 105) / 105 = 0.047619...
    fwd_after = forward_returns(prices, pd.Timestamp("2026-03-09 21:00:00+00:00"), [1, 2])
    assert fwd_after[1] == pytest.approx((110.0 - 105.0) / 105.0)
    assert fwd_after[2] == pytest.approx((120.0 - 105.0) / 105.0)


def test_forward_returns_not_enough_bars_returns_nan():
    dates = pd.to_datetime(["2026-03-09", "2026-03-10"])
    prices = pd.Series([100.0, 105.0], index=dates)

    # Entry at Monday close. Horizon 1 exists (Tuesday), but horizon 2 does not.
    fwd = forward_returns(prices, pd.Timestamp("2026-03-09 12:00:00+00:00"), [1, 2])
    assert fwd[1] == pytest.approx(0.05)
    assert np.isnan(fwd[2])


def test_event_strategy_returns_flat_zero_on_days_with_no_open_position():
    """
    Acceptance: event_strategy_returns is flat/zero on days with no open position.
    """
    # 5 trading days: Mon to Fri
    dates = pd.date_range("2026-03-09", periods=5, freq="B")
    # Day 0: 100, Day 1: 102 (+2%), Day 2: 105 (+2.94%), Day 3: 103, Day 4: 104
    prices = {"AAPL": pd.Series([100.0, 102.0, 105.0, 103.0, 104.0], index=dates)}

    # Signal on Day 0 before close with hold_days = 1.
    # Entry is Day 0 close. Held only during Day 1.
    # Day 2, 3, 4 must be exactly 0.0.
    signals = pd.DataFrame([{"ts": pd.Timestamp("2026-03-09 14:00:00+00:00"), "symbol": "AAPL"}])

    strat_rets = event_strategy_returns(signals, prices, hold_days=1)

    assert len(strat_rets) == 4  # Return dates: Day 1, Day 2, Day 3, Day 4
    # Day 1 return is Day 0->Day 1 move: (102 - 100) / 100 = 0.02
    assert strat_rets.iloc[0] == pytest.approx(0.02)
    # Days 2, 3, 4 are cash days -> must be flat 0.0
    assert strat_rets.iloc[1] == 0.0
    assert strat_rets.iloc[2] == 0.0
    assert strat_rets.iloc[3] == 0.0

    # Also verify empty signals yields all zeros
    empty_strat = event_strategy_returns(pd.DataFrame(), prices, hold_days=1)
    assert (empty_strat == 0.0).all()


def test_abnormal_returns_computes_raw_bench_and_abnormal():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    # Benchmark: upward drift
    bench_prices = pd.Series(np.linspace(100.0, 200.0, 300), index=dates)
    # Stock: moves with bench (beta ~ 1.2)
    stock_prices = pd.Series(np.linspace(50.0, 170.0, 300), index=dates)

    prices = {"SYM": stock_prices}
    ev_ts = dates[260] + pd.Timedelta(hours=14)  # during session
    events = pd.DataFrame([{"ts": ev_ts, "symbol": "SYM", "label": "test_event"}])

    res = abnormal_returns(events, prices, bench_prices, horizons=[1, 5])
    assert len(res) == 2
    assert set(res.columns) >= {"ts", "symbol", "horizon", "raw", "bench", "abnormal", "beta", "label"}
    for _, row in res.iterrows():
        assert not np.isnan(row["raw"])
        assert not np.isnan(row["bench"])
        assert row["abnormal"] == pytest.approx(row["raw"] - row["beta"] * row["bench"])


def test_weekend_event_enters_monday_and_beta_fallback():
    # 2026-03-07 is Saturday. Monday is 2026-03-09.
    dates = pd.date_range("2026-03-09", periods=5, freq="B")
    prices = {"SYM": pd.Series([100.0, 102.0, 104.0, 106.0, 108.0], index=dates)}
    bench = pd.Series([200.0, 201.0, 202.0, 203.0, 204.0], index=dates)

    # Event on Saturday 2026-03-07. Entry must be Monday close ($100.0).
    ev_ts = pd.Timestamp("2026-03-07 15:00:00+00:00")
    events = pd.DataFrame([{"ts": ev_ts, "symbol": "SYM"}])

    res = abnormal_returns(events, prices, bench, horizons=[1])
    assert len(res) == 1
    # Raw is Monday to Tuesday: (102 - 100) / 100 = 0.02
    assert res.iloc[0]["raw"] == pytest.approx(0.02)
    # Insufficient pre-event history (<20 bars) -> beta fallback to 1.0
    assert res.iloc[0]["beta"] == 1.0


def test_judge_returns_passes_false_when_halves_disagree_in_sign():
    """
    Acceptance: judge returns passes=False when halves disagree in sign.
    """
    rng = np.random.default_rng(42)
    n = 100
    dates = pd.date_range("2026-01-01", periods=n, freq="B")

    bench_rets = pd.Series(rng.normal(0.0005, 0.01, n), index=dates)

    # First half: strong positive alpha; Second half: negative alpha
    mid = n // 2
    p_h1 = 0.2 * bench_rets.iloc[:mid] + 0.01 + rng.normal(0, 0.002, mid)
    p_h2 = 0.2 * bench_rets.iloc[mid:] - 0.01 + rng.normal(0, 0.002, n - mid)
    strat_rets = pd.concat([p_h1, p_h2])

    res = judge(strat_rets, bench_rets)
    # Halves disagree in sign -> passes MUST be False
    assert res["passes"] is False
    assert res["alpha_h1"] is not None and res["alpha_h1"] > 0
    assert res["alpha_h2"] is not None and res["alpha_h2"] < 0


def test_judge_returns_passes_false_when_beta_high_or_t_low():
    rng = np.random.default_rng(99)
    n = 100
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    bench_rets = pd.Series(rng.normal(0.0005, 0.01, n), index=dates)

    # Beta = 0.8 (which is >= 0.5), even though t is high and alpha > 0
    strat_high_beta = 0.8 * bench_rets + 0.005 + pd.Series(rng.normal(0, 0.001, n), index=dates)
    res_high_beta = judge(strat_high_beta, bench_rets)
    assert res_high_beta["passes"] is False
    assert res_high_beta["beta"] >= 0.5


def test_judge_returns_passes_true_when_all_claude_md_conditions_met():
    """
    t >= 2, both halves alpha > 0, beta < 0.5 -> passes is True.
    """
    rng = np.random.default_rng(123)
    n = 200
    dates = pd.date_range("2026-01-01", periods=n, freq="B")

    bench_rets = pd.Series(rng.normal(0.0005, 0.01, n), index=dates)
    # Consistent positive alpha, low beta = 0.1, low residual noise so t is high
    strat_rets = 0.1 * bench_rets + 0.005 + pd.Series(rng.normal(0, 0.001, n), index=dates)

    res = judge(strat_rets, bench_rets)
    assert res["passes"] is True
    assert res["t"] >= 2.0
    assert res["beta"] < 0.5
    assert res["alpha_h1"] > 0.0
    assert res["alpha_h2"] > 0.0
