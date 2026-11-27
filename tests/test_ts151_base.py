"""Tests for TS151 shared contracts (PLAN-ts151.md S0).

Verifies:
1. rebalance_due for D/W/M/Q.
2. The gate returns None mid-period when holding, and builds when flat.
3. long_only=True drops shorts and rescales.
4. universes.resolve handles a name, a tuple and an unknown name (raises).
5. Constructor kwargs override DEFAULTS and land as attributes; interval/period pass through.
6. replay_state on a hand-built enter/exit sequence equals a bar-by-bar loop, checked at mid and last bar.
7. Common helpers (rank_weights, topk_bottomk, dv01_neutral, ibs, pivot_levels, min_variance_weights).
8. Point-in-time readers (pit_latest, pit_quarterly, cot_asof).
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.botclass import Bot
from tradingbot.utils.ts151 import common, data_api, fred, universes
from tradingbot.utils.ts151.base import Ctx, TS151Bot


class DummyTS151Bot(TS151Bot):
    """Test subclass of TS151Bot."""

    BOT_NAME = "DummyTS151Bot"
    DEFAULT_UNIVERSE = ("SPY", "QQQ")
    ALLOW_SHORT = True
    MAX_GROSS = 2.0
    DEFAULTS: ClassVar[dict[str, Any]] = {
        "lookback": 20,
        "threshold": 0.5,
    }

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return {"SPY": 1.0, "QQQ": -1.0}


@pytest.fixture
def patch_bot_init(mocker):
    """Stub out Bot.__init__ to avoid database interactions during unit tests."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)


# ------------------------------------------------------------------
# 1. rebalance_due for D / W / M / Q
# ------------------------------------------------------------------


def test_rebalance_due_frequencies():
    # Empty index
    empty_idx = pd.DatetimeIndex([])
    assert common.rebalance_due(empty_idx, "D") is False
    assert common.rebalance_due(empty_idx, "W") is False
    assert common.rebalance_due(empty_idx, "M") is False
    assert common.rebalance_due(empty_idx, "Q") is False

    # Single-bar index
    single_idx = pd.DatetimeIndex(["2026-01-05"])  # Monday
    assert common.rebalance_due(single_idx, "D") is True
    assert common.rebalance_due(single_idx, "W") is False
    assert common.rebalance_due(single_idx, "M") is False
    assert common.rebalance_due(single_idx, "Q") is False

    # Same ISO week (Mon -> Tue)
    same_week_idx = pd.DatetimeIndex(["2026-01-05", "2026-01-06"])
    assert common.rebalance_due(same_week_idx, "D") is True
    assert common.rebalance_due(same_week_idx, "W") is False

    # Week rolled (Fri -> Mon)
    week_rolled_idx = pd.DatetimeIndex(["2026-01-09", "2026-01-12"])
    assert common.rebalance_due(week_rolled_idx, "W") is True

    # Same month vs Month rolled
    same_month_idx = pd.DatetimeIndex(["2026-01-15", "2026-01-16"])
    assert common.rebalance_due(same_month_idx, "M") is False
    month_rolled_idx = pd.DatetimeIndex(["2026-01-30", "2026-02-02"])
    assert common.rebalance_due(month_rolled_idx, "M") is True

    # Quarter rolled
    same_quarter_idx = pd.DatetimeIndex(["2026-01-30", "2026-02-02"])
    assert common.rebalance_due(same_quarter_idx, "Q") is False
    quarter_rolled_idx = pd.DatetimeIndex(["2026-03-31", "2026-04-01"])
    assert common.rebalance_due(quarter_rolled_idx, "Q") is True

    # Unknown frequency raises ValueError
    with pytest.raises(ValueError, match="unsupported freq"):
        common.rebalance_due(month_rolled_idx, "Y")


# ------------------------------------------------------------------
# 2. Rebalance gate: None mid-period when holding, builds when flat
# ------------------------------------------------------------------


def test_rebalance_gate(patch_bot_init, mocker):
    bot = DummyTS151Bot(rebalance="M")

    # Closes with two dates in the same month (no month roll)
    dates = pd.DatetimeIndex(["2026-01-15", "2026-01-16"])
    df = pd.DataFrame(
        {"timestamp": dates, "close": [100.0, 101.0]},
        index=dates,
    )
    bot.datas = {"SPY": df, "QQQ": df}

    # Live rows format (contains timestamp in Series index)
    live_rows = {
        "SPY": pd.Series({"timestamp": pd.Timestamp("2026-01-16"), "close": 101.0}),
        "QQQ": pd.Series({"timestamp": pd.Timestamp("2026-01-16"), "close": 101.0}),
    }

    # Case A: Live run, holding positions -> mid-period returns None
    mocker.patch("tradingbot.utils.ts151.base.live_holdings", return_value={"SPY": 0.5, "QQQ": -0.5})
    assert bot.targetWeights(live_rows) is None

    # Case B: Live run, flat book -> builds immediately even mid-period
    mocker.patch("tradingbot.utils.ts151.base.live_holdings", return_value={})
    weights_flat = bot.targetWeights(live_rows)
    assert weights_flat is not None
    assert weights_flat == {"SPY": 1.0, "QQQ": -1.0}

    # Case C: Month rolled bar -> rebalances even when holding
    dates_rolled = pd.DatetimeIndex(["2026-01-30", "2026-02-02"])
    df_rolled = pd.DataFrame(
        {"timestamp": dates_rolled, "close": [100.0, 102.0]},
        index=dates_rolled,
    )
    bot.datas = {"SPY": df_rolled, "QQQ": df_rolled}
    mocker.patch("tradingbot.utils.ts151.base.live_holdings", return_value={"SPY": 0.5, "QQQ": -0.5})
    weights_rolled = bot.targetWeights(live_rows)
    assert weights_rolled is not None
    assert weights_rolled == {"SPY": 1.0, "QQQ": -1.0}


# ------------------------------------------------------------------
# 3. long_only=True drops shorts and rescales
# ------------------------------------------------------------------


def test_long_only_drops_shorts_and_rescales(patch_bot_init, mocker):
    class CustomSignalBot(TS151Bot):
        ALLOW_SHORT = True
        MAX_GROSS = 2.0

        def signal(self, ctx: Ctx):
            return getattr(self, "_mock_signal", {})

    bot_ls = CustomSignalBot(long_only=False, universe=("SPY", "QQQ", "TLT"))
    bot_lo = CustomSignalBot(long_only=True, universe=("SPY", "QQQ", "TLT"))

    dates = pd.DatetimeIndex(["2026-01-05"])
    df = pd.DataFrame({"close": [100.0]}, index=dates)
    bot_ls.datas = {"SPY": df, "QQQ": df, "TLT": df}
    bot_lo.datas = {"SPY": df, "QQQ": df, "TLT": df}

    # Rebalance due on every daily bar
    bot_ls.rebalance = "D"
    bot_lo.rebalance = "D"
    rows = {"SPY": pd.Series({"close": 100.0})}

    # Signal with longs summing to 1.6 (> 1.0) and a short
    bot_ls._mock_signal = {"SPY": 0.8, "QQQ": 0.8, "TLT": -0.5}
    bot_lo._mock_signal = {"SPY": 0.8, "QQQ": 0.8, "TLT": -0.5}

    w_ls = bot_ls.targetWeights(rows)
    assert w_ls == {"SPY": 0.8, "QQQ": 0.8, "TLT": -0.5}

    w_lo = bot_lo.targetWeights(rows)
    assert "TLT" not in w_lo
    assert set(w_lo.keys()) == {"SPY", "QQQ"}
    # Longs rescaled to cap at 1.0
    assert pytest.approx(w_lo["SPY"], 1e-6) == 0.5
    assert pytest.approx(w_lo["QQQ"], 1e-6) == 0.5
    assert pytest.approx(sum(w_lo.values()), 1e-6) == 1.0

    # Signal with longs summing to 0.6 (<= 1.0)
    bot_lo._mock_signal = {"SPY": 0.3, "QQQ": 0.3, "TLT": -0.4}
    w_lo_small = bot_lo.targetWeights(rows)
    assert pytest.approx(w_lo_small["SPY"], 1e-6) == 0.3
    assert pytest.approx(w_lo_small["QQQ"], 1e-6) == 0.3
    assert pytest.approx(sum(w_lo_small.values()), 1e-6) == 0.6


# ------------------------------------------------------------------
# 4. universes.resolve handles name, tuple, and unknown name
# ------------------------------------------------------------------


def test_universes_resolve():
    # Named string
    sec = universes.resolve("sectors")
    assert sec == universes.SECTORS
    assert "XLK" in sec

    # Case-insensitive
    assert universes.resolve("SECTORS") == universes.SECTORS
    assert universes.resolve("crypto") == universes.CRYPTO

    # Tuple / list passed directly
    tup = universes.resolve(("AAPL", "MSFT"))
    assert tup == ("AAPL", "MSFT")
    lst = universes.resolve(["SPY", "GLD"])
    assert lst == ("SPY", "GLD")

    # Unknown name raises ValueError
    with pytest.raises(ValueError, match="Unknown universe"):
        universes.resolve("non_existent_universe")

    # Invalid type raises ValueError
    with pytest.raises(ValueError, match="Invalid universe"):
        universes.resolve(42)  # type: ignore


# ------------------------------------------------------------------
# 5. Constructor kwargs override DEFAULTS and land as attributes
# ------------------------------------------------------------------


def test_constructor_kwargs_and_defaults(patch_bot_init):
    bot = DummyTS151Bot(
        lookback=50,
        interval="1h",
        period="6mo",
        rebalance="W",
    )

    # Overridden kwarg
    assert bot.lookback == 50
    # Default kept
    assert bot.threshold == 0.5
    # Parameters passed through
    assert bot.interval == "1h"
    assert bot.period == "6mo"
    assert bot.rebalance == "W"
    # Stored in params dictionary
    assert bot.params["lookback"] == 50
    assert bot.params["threshold"] == 0.5
    assert bot.params["rebalance"] == "W"

    # Shortable set when ALLOW_SHORT and not long_only
    assert bot.SHORTABLE == tuple(bot.universe_list)


# ------------------------------------------------------------------
# 6. replay_state on enter/exit sequence equals prefix loop
# ------------------------------------------------------------------


def test_replay_state_exact_match():
    # Hand-built sequence across 10 bars
    # Bar 0: flat
    # Bar 1: long enter -> 1
    # Bar 2: held -> 1
    # Bar 3: long exit -> 0
    # Bar 4: short enter -> -1
    # Bar 5: held -> -1
    # Bar 6: short exit -> 0
    # Bar 7: long enter -> 1
    # Bar 8: NaN in signal -> resets to 0
    # Bar 9: short enter -> -1
    long_enter = [False, True, False, False, False, False, False, True, np.nan, False]
    long_exit = [False, False, False, True, False, False, False, False, False, False]
    short_enter = [False, False, False, False, True, False, False, False, False, True]
    short_exit = [False, False, False, False, False, False, True, False, False, False]

    expected_states = [0, 1, 1, 0, -1, -1, 0, 1, 0, -1]

    # Verify at every prefix from length 1 to 10
    for k in range(1, 11):
        res = common.replay_state(
            long_enter[:k],
            long_exit[:k],
            short_enter[:k],
            short_exit[:k],
        )
        assert res == expected_states[k - 1], f"Failed at bar index {k - 1}"

    # Explicit check at mid bar (bar 5 -> index 4: short entered)
    assert common.replay_state(
        long_enter[:5],
        long_exit[:5],
        short_enter[:5],
        short_exit[:5],
    ) == -1

    # Explicit check at mid bar after long exit (bar 4 -> index 3: long exited)
    assert common.replay_state(
        long_enter[:4],
        long_exit[:4],
        short_enter[:4],
        short_exit[:4],
    ) == 0

    # Explicit check at final bar
    assert common.replay_state(
        long_enter,
        long_exit,
        short_enter,
        short_exit,
    ) == -1


# ------------------------------------------------------------------
# 7. Common helpers unit tests
# ------------------------------------------------------------------


def test_common_helpers():
    # topk_bottomk signed
    scores = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0})
    tb_signed = common.topk_bottomk(scores, k=1, long_only=False, gross=1.0)
    assert tb_signed == {"D": 0.5, "A": -0.5}

    # topk_bottomk fractional k
    tb_frac = common.topk_bottomk(scores, k=0.5, long_only=True, gross=1.0)
    assert set(tb_frac.keys()) == {"C", "D"}
    assert pytest.approx(sum(tb_frac.values()), 1e-6) == 1.0

    # rank_weights
    rw = common.rank_weights(scores, gross=1.0)
    assert pytest.approx(sum(rw.values()), 1e-6) == 0.0
    assert pytest.approx(sum(abs(w) for w in rw.values()), 1e-6) == 1.0

    # inverse_vol
    idx = pd.date_range("2026-01-01", periods=10)
    closes = pd.DataFrame(
        {"A": [10, 11, 10, 11, 10, 11, 10, 11, 10, 11], "B": [10, 10.1, 10.2, 10.1, 10.2, 10.1, 10.2, 10.1, 10.2, 10.1]},
        index=idx,
    )
    iv = common.inverse_vol(closes, window=5)
    assert pytest.approx(iv.sum(), 1e-6) == 1.0
    # B has lower vol, so higher inverse vol weight
    assert iv["B"] > iv["A"]

    # trailing_return
    tr = common.trailing_return(closes, window=4, skip=1)
    expected_a = (closes["A"].iloc[-2] / closes["A"].iloc[-6]) - 1.0
    assert pytest.approx(tr["A"], 1e-6) == expected_a

    # dv01_neutral
    durations = {"IEF": 7.5, "SHY": 1.9, "TLT": 17.0}
    butterfly = common.dv01_neutral("IEF", ("SHY", "TLT"), durations)
    net_dv01 = (
        butterfly["IEF"] * durations["IEF"]
        + butterfly["SHY"] * durations["SHY"]
        + butterfly["TLT"] * durations["TLT"]
    )
    assert pytest.approx(net_dv01, 1e-6) == 0.0

    # ibs
    assert common.ibs({"high": 110, "low": 90, "close": 100}) == 0.5
    assert common.ibs({"high": 100, "low": 100, "close": 100}) == 0.5

    # pivot_levels
    c, r, s = common.pivot_levels(110, 90, 100)
    assert c == 100.0
    assert r == 110.0
    assert s == 90.0

    # min_variance_weights
    cov = pd.DataFrame(
        [[0.04, 0.01], [0.01, 0.02]],
        index=["A", "B"],
        columns=["A", "B"],
    )
    mv = common.min_variance_weights(cov)
    assert pytest.approx(mv.sum(), 1e-6) == 1.0
    assert mv["B"] > mv["A"]  # B has lower variance


# ------------------------------------------------------------------
# 8. Point-in-time readers tests (pit_latest, pit_quarterly, cot_asof)
# ------------------------------------------------------------------


def test_pit_readers():
    # Fundamentals frame
    fund_data = pd.DataFrame(
        [
            # Q1 filed on 2026-04-15
            {"ticker": "AAPL", "tag": "EPS", "period_end": "2026-03-31", "filed": "2026-04-15", "value": 1.5},
            # Restated Q1 filed later on 2026-08-01
            {"ticker": "AAPL", "tag": "EPS", "period_end": "2026-03-31", "filed": "2026-08-01", "value": 1.4},
            # Q2 filed on 2026-07-15
            {"ticker": "AAPL", "tag": "EPS", "period_end": "2026-06-30", "filed": "2026-07-15", "value": 1.6},
            # Facts filed after as_of 2026-05-01 (should be excluded)
            {"ticker": "AAPL", "tag": "Assets", "period_end": "2026-03-31", "filed": "2026-05-15", "value": 1000.0},
        ]
    )

    # as_of before late filing and before Q2
    as_of = "2026-05-01"
    latest = data_api.pit_latest(fund_data, as_of)
    assert "EPS" in latest.columns
    # Assets was filed 2026-05-15, so it must not be present at 2026-05-01
    assert "Assets" not in latest.columns
    assert latest.loc["AAPL", "EPS"] == 1.5

    # pit_quarterly up to as_of 2026-05-01 keeps the original 1.5, not restated 1.4
    q_early = data_api.pit_quarterly(fund_data, "AAPL", "EPS", "2026-05-01")
    assert len(q_early) == 1
    assert q_early.iloc[0] == 1.5

    # pit_quarterly after restatement filed sees the updated value 1.4 and Q2
    q_late = data_api.pit_quarterly(fund_data, "AAPL", "EPS", "2026-08-15")
    assert len(q_late) == 2
    assert q_late.loc["2026-03-31"] == 1.4
    assert q_late.loc["2026-06-30"] == 1.6

    # COT as_of: Tuesday report is released Friday
    cot_df = pd.DataFrame(
        [
            {"market_code": "067651", "report_date": "2026-01-06", "comm_long": 100.0},  # Tuesday
        ]
    )
    # Wednesday: report + 3 days > Wednesday -> hidden
    assert data_api.cot_asof(cot_df, "2026-01-07").empty
    # Thursday: hidden
    assert data_api.cot_asof(cot_df, "2026-01-08").empty
    # Friday: visible
    visible = data_api.cot_asof(cot_df, "2026-01-09")
    assert not visible.empty
    assert len(visible) == 1
