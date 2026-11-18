"""
Tests for InstitutionalFlowBot turnover hysteresis (Step C1).

Validates:
1. hold_rank=None output identical to legacy code on a fixed fixture.
2. With hold_rank set (e.g. 30), a name ranked 21-30 that was already held is kept,
   and fills drop on a synthetic rank series.
3. A held name ranking beyond hold_rank is dropped and open slots fill from the top.
4. Mandate filter failure forces an exit even within hold_rank.
5. Market gate trigger flushes holdings to cash.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.institutionalflowbot import BENCHMARK, InstitutionalFlowBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.institutional_ta import accumulation_score, passes_mandate


@pytest.fixture(autouse=True)
def stub_bot_init(monkeypatch):
    """Bypass Bot.__init__ database connections in unit tests."""

    def _fake_init(self, name, *args, tickers=None, benchmark_tickers=None, **kwargs):
        self.bot_name = name
        self.tickers = list(tickers or [])
        self.benchmark_tickers = list(benchmark_tickers or [])
        self.datas = {}
        self.interval = kwargs.get("interval", "1d")
        self.period = kwargs.get("period", "2y")
        self.params = kwargs.copy()

    monkeypatch.setattr(Bot, "__init__", _fake_init)


def _make_stock_row(
    close: float = 50.0,
    adv: float = 100e6,
    trend_ok: float = 1.0,
    rs: float = 0.5,
    ud_vol: float = 1.2,
    cmf: float = 0.1,
    obv_slope: float = 0.5,
    accum_net: float = 2.0,
    vwap_dist: float = 0.02,
    ret20: float = 0.05,
    timestamp: str | pd.Timestamp = "2024-01-05",
) -> pd.Series:
    return pd.Series(
        {
            "close": close,
            "inst_adv_usd": adv,
            "inst_trend_ok": trend_ok,
            "inst_rs": rs,
            "inst_ud_vol_ratio50": ud_vol,
            "volume_cmf": cmf,
            "inst_obv_slope20": obv_slope,
            "inst_accum_net25": accum_net,
            "inst_vwap_qtd_dist": vwap_dist,
            "inst_ret20": ret20,
            "timestamp": pd.Timestamp(timestamp),
        }
    )


def _make_benchmark_row(
    close: float = 400.0,
    sma200: float = 380.0,
    timestamp: str | pd.Timestamp = "2024-01-05",
) -> pd.Series:
    return pd.Series(
        {
            "close": close,
            "inst_sma200": sma200,
            "timestamp": pd.Timestamp(timestamp),
        }
    )


def test_hold_rank_none_identical_to_legacy():
    """hold_rank=None must produce output identical to legacy un-hysteresed code on fixed fixture."""
    fri = pd.Timestamp("2024-01-05")  # Friday
    tickers = [f"STK_{i:02d}" for i in range(30)]

    rows: dict[str, pd.Series] = {BENCHMARK: _make_benchmark_row(timestamp=fri)}
    for i, t in enumerate(tickers):
        # Stagger features so scores vary deterministically
        rows[t] = _make_stock_row(
            close=20.0 + i,
            rs=float(i) / 10.0,
            obv_slope=float(i) / 5.0,
            timestamp=fri,
        )

    # Legacy computation
    eligible = {t: rows[t] for t in tickers if passes_mandate(rows[t])}
    legacy_scores = accumulation_score(eligible)
    legacy_picks = sorted(legacy_scores, key=lambda t: legacy_scores[t], reverse=True)[:20]
    legacy_weights = dict.fromkeys(legacy_picks, 1.0 / 20)

    # Bot with hold_rank=None
    bot = InstitutionalFlowBot(top_n=20, hold_rank=None)
    bot.tickers = [*tickers, BENCHMARK]
    bot.benchmark_tickers = [BENCHMARK]
    bot_weights = bot.targetWeights(rows)

    assert bot_weights is not None
    assert bot_weights == legacy_weights
    assert len(bot_weights) == 20


def test_hold_rank_hysteresis_keeps_name_ranked_21_30():
    """
    With hold_rank=30, a name ranked 21-30 already held is kept and fills drop
    on a synthetic rank series.
    """
    fri1 = pd.Timestamp("2024-01-05")  # Friday 1
    fri2 = pd.Timestamp("2024-01-12")  # Friday 2
    tickers = [f"STK_{i:02d}" for i in range(35)]

    # Friday 1: STK_00..STK_19 have top scores (ranks 1-20), STK_20..STK_34 have lower scores
    # STK_15 is in top 20 (rank 16)
    # Friday 2: STK_15 drops to rank 25. A new stock STK_21 jumps to rank 10.
    # All other STK_00..STK_19 (except STK_15) stay in top 19.

    dfs: dict[str, pd.DataFrame] = {}

    # Benchmark frame
    dfs[BENCHMARK] = pd.DataFrame(
        [
            _make_benchmark_row(timestamp=fri1),
            _make_benchmark_row(timestamp=fri2),
        ]
    ).set_index("timestamp")

    for i, t in enumerate(tickers):
        # Week 1 score component
        rs_w1 = float(35 - i)  # STK_00 has 35 (highest), STK_15 has 20, STK_34 has 1

        # Week 2 score component
        if t == "STK_15":
            # Rank ~25 on week 2
            rs_w2 = 10.5
        elif t == "STK_21":
            # Jumps to high rank on week 2
            rs_w2 = 28.5
        else:
            rs_w2 = float(35 - i)

        r1 = _make_stock_row(rs=rs_w1, obv_slope=rs_w1, timestamp=fri1)
        r2 = _make_stock_row(rs=rs_w2, obv_slope=rs_w2, timestamp=fri2)
        dfs[t] = pd.DataFrame([r1, r2]).set_index("timestamp")

    # Rows at Friday 2
    rows_w2 = {t: dfs[t].loc[fri2] for t in tickers}
    rows_w2[BENCHMARK] = dfs[BENCHMARK].loc[fri2]

    # 1. Run without hysteresis (hold_rank=None)
    bot_no_hyst = InstitutionalFlowBot(top_n=20, hold_rank=None)
    bot_no_hyst.tickers = [*tickers, BENCHMARK]
    bot_no_hyst.benchmark_tickers = [BENCHMARK]
    bot_no_hyst.datas = dfs
    weights_no_hyst = bot_no_hyst.targetWeights(rows_w2)
    assert weights_no_hyst is not None

    # STK_15 ranked ~25, so with top_n=20 and no hysteresis, it must NOT be held
    assert "STK_15" not in weights_no_hyst
    # STK_21 jumped to rank ~10, so it must be held
    assert "STK_21" in weights_no_hyst

    # 2. Run with hysteresis (hold_rank=30)
    bot_hyst = InstitutionalFlowBot(top_n=20, hold_rank=30)
    bot_hyst.tickers = [*tickers, BENCHMARK]
    bot_hyst.benchmark_tickers = [BENCHMARK]
    bot_hyst.datas = dfs
    weights_hyst = bot_hyst.targetWeights(rows_w2)
    assert weights_hyst is not None

    # STK_15 was held on Friday 1, still passes mandate, and ranks <= 30 on Friday 2
    # So with hold_rank=30, STK_15 MUST be kept!
    assert "STK_15" in weights_hyst

    # Since STK_15 was kept and all other 19 held names also ranked <= 30,
    # all 20 slots were preserved by survivors.
    # Therefore, 0 open slots existed, and STK_21 could NOT enter!
    assert "STK_21" not in weights_hyst

    # Fills comparison:
    # On Friday 1, held names were the same top 20
    rows_w1 = {t: dfs[t].loc[fri1] for t in tickers}
    rows_w1[BENCHMARK] = dfs[BENCHMARK].loc[fri1]
    bot_w1 = InstitutionalFlowBot(top_n=20, hold_rank=None)
    bot_w1.tickers = [*tickers, BENCHMARK]
    bot_w1.benchmark_tickers = [BENCHMARK]
    weights_w1 = bot_w1.targetWeights(rows_w1)

    held_w1 = set(weights_w1.keys())

    # Fills with no hysteresis: names in w2 but not in w1
    fills_no_hyst = set(weights_no_hyst.keys()) - held_w1
    assert len(fills_no_hyst) == 1  # STK_21 filled
    assert "STK_21" in fills_no_hyst

    # Fills with hysteresis: names in w2 but not in w1
    fills_hyst = set(weights_hyst.keys()) - held_w1
    assert len(fills_hyst) == 0  # 0 fills! Fills dropped from 1 to 0!


def test_hold_rank_drops_name_beyond_hold_rank():
    """A held name that falls beyond hold_rank (e.g. rank 32 with hold_rank=30) is dropped."""
    fri1 = pd.Timestamp("2024-01-05")
    fri2 = pd.Timestamp("2024-01-12")
    tickers = [f"STK_{i:02d}" for i in range(35)]

    dfs: dict[str, pd.DataFrame] = {}
    dfs[BENCHMARK] = pd.DataFrame(
        [
            _make_benchmark_row(timestamp=fri1),
            _make_benchmark_row(timestamp=fri2),
        ]
    ).set_index("timestamp")

    for i, t in enumerate(tickers):
        rs_w1 = float(35 - i)
        # Week 2: STK_15 plunges to bottom (rank 34 > hold_rank 30)
        if t == "STK_15":
            rs_w2 = 0.5
        elif t == "STK_21":
            rs_w2 = 28.5
        else:
            rs_w2 = float(35 - i)

        dfs[t] = pd.DataFrame(
            [
                _make_stock_row(rs=rs_w1, obv_slope=rs_w1, timestamp=fri1),
                _make_stock_row(rs=rs_w2, obv_slope=rs_w2, timestamp=fri2),
            ]
        ).set_index("timestamp")

    rows_w2 = {t: dfs[t].loc[fri2] for t in tickers}
    rows_w2[BENCHMARK] = dfs[BENCHMARK].loc[fri2]

    bot = InstitutionalFlowBot(top_n=20, hold_rank=30)
    bot.tickers = [*tickers, BENCHMARK]
    bot.benchmark_tickers = [BENCHMARK]
    bot.datas = dfs
    weights = bot.targetWeights(rows_w2)

    assert weights is not None
    # STK_15 dropped beyond rank 30, so it must NOT be kept
    assert "STK_15" not in weights
    # The 1 open slot is filled from top by STK_21
    assert "STK_21" in weights
    assert len(weights) == 20


def test_mandate_failure_forces_exit_under_hysteresis():
    """A held stock that fails the mandate filter exits even if it ranked within hold_rank."""
    fri1 = pd.Timestamp("2024-01-05")
    fri2 = pd.Timestamp("2024-01-12")
    tickers = [f"STK_{i:02d}" for i in range(25)]

    dfs: dict[str, pd.DataFrame] = {}
    dfs[BENCHMARK] = pd.DataFrame(
        [
            _make_benchmark_row(timestamp=fri1),
            _make_benchmark_row(timestamp=fri2),
        ]
    ).set_index("timestamp")

    for i, t in enumerate(tickers):
        rs_w1 = float(25 - i)
        rs_w2 = float(25 - i)
        # STK_05 breaks trend filter on week 2
        trend_w2 = 0.0 if t == "STK_05" else 1.0

        dfs[t] = pd.DataFrame(
            [
                _make_stock_row(rs=rs_w1, obv_slope=rs_w1, trend_ok=1.0, timestamp=fri1),
                _make_stock_row(rs=rs_w2, obv_slope=rs_w2, trend_ok=trend_w2, timestamp=fri2),
            ]
        ).set_index("timestamp")

    rows_w2 = {t: dfs[t].loc[fri2] for t in tickers}
    rows_w2[BENCHMARK] = dfs[BENCHMARK].loc[fri2]

    bot = InstitutionalFlowBot(top_n=20, hold_rank=30)
    bot.tickers = [*tickers, BENCHMARK]
    bot.benchmark_tickers = [BENCHMARK]
    bot.datas = dfs
    weights = bot.targetWeights(rows_w2)

    assert weights is not None
    # STK_05 failed mandate, so it cannot be kept
    assert "STK_05" not in weights


def test_market_gate_clears_holdings():
    """When QQQ falls below 200d MA, hysteresis book goes 100% cash."""
    fri = pd.Timestamp("2024-01-05")
    tickers = [f"STK_{i:02d}" for i in range(10)]

    rows: dict[str, pd.Series] = {
        BENCHMARK: _make_benchmark_row(close=350.0, sma200=400.0, timestamp=fri)  # below SMA200
    }
    for t in tickers:
        rows[t] = _make_stock_row(timestamp=fri)

    bot = InstitutionalFlowBot(top_n=5, hold_rank=30, market_gate=True)
    bot.tickers = [*tickers, BENCHMARK]
    bot.benchmark_tickers = [BENCHMARK]
    weights = bot.targetWeights(rows)
    assert weights == {}
