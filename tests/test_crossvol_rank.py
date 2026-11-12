"""Unit tests for CrossVol ranking filters and structure variants (C1).

Tests use synthetic in-memory data, no DB.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.utils.crossvol_rank import (
    calc_idiosyncratic_vol,
    calc_vol_of_vol,
    make_crossvol_decide,
    rank_name_vols,
    select_candidates_ranked,
)
from tradingbot.utils.option_decide import Hedge, Open
from tradingbot.utils.option_rules import CrossVolRules, NameVol, cross_vol_candidates


def test_default_crossvol_rules_unchanged():
    """Default CrossVolRules() has default-None new fields and identical prior defaults."""
    rules = CrossVolRules()
    assert rules.target_dte == 35
    assert rules.min_gap == 0.05
    assert rules.short_delta == 0.10
    assert rules.width_pct == 0.10
    assert rules.max_positions == 5
    assert rules.risk_per_name_pct == 0.04
    assert rules.take_profit == 0.50
    assert rules.stop_loss == 2.0
    assert rules.exit_dte == 7
    assert rules.max_vix == 35.0
    assert rules.shortlist == 12
    assert rules.rank_metric is None
    assert rules.structure is None


def test_default_candidate_selection_unchanged():
    """With default rules, select_candidates_ranked behaves identically to cross_vol_candidates."""
    names = [
        NameVol("AAPL", iv=0.30, fair=0.22, earnings_clear=True),  # gap = 0.08
        NameVol("MSFT", iv=0.25, fair=0.23, earnings_clear=True),  # gap = 0.02 (< min_gap)
        NameVol("NVDA", iv=0.45, fair=0.35, earnings_clear=True),  # gap = 0.10
        NameVol("TSLA", iv=0.50, fair=0.40, earnings_clear=False),  # earnings not clear
        NameVol("AMZN", iv=0.32, fair=0.25, earnings_clear=True),  # gap = 0.07
    ]
    held = {"AMZN"}
    rules = CrossVolRules()

    orig = cross_vol_candidates(names, held, rules)
    # Orig should select NVDA (0.10), AAPL (0.08)
    assert [n.underlying for n in orig] == ["NVDA", "AAPL"]

    ranked = select_candidates_ranked(names, held, rules)
    assert [n.underlying for n in ranked] == [n.underlying for n in orig]


def test_calc_vol_of_vol():
    """vol_of_vol is std(daily ATM IV changes), returns None when < min_history."""
    # Too few observations
    short_series = pd.Series([0.20, 0.21, 0.20, 0.22])
    assert calc_vol_of_vol(short_series, min_history=20) is None

    # Deterministic and exact on 25 points
    np.random.seed(42)
    iv_vals = [0.20 + 0.01 * i + 0.005 * (i % 3) for i in range(25)]
    vov = calc_vol_of_vol(iv_vals, min_history=20)
    assert vov is not None
    assert vov > 0.0
    # Calling again gives identical result
    assert calc_vol_of_vol(iv_vals, min_history=20) == vov


def test_calc_idiosyncratic_vol():
    """idiosyncratic_vol is residual std from regression on market benchmark."""
    # Too few observations
    short_s = pd.Series([0.01, -0.01, 0.02])
    short_m = pd.Series([0.005, -0.005, 0.01])
    assert calc_idiosyncratic_vol(short_s, short_m, min_history=20) is None

    # Sufficient observations
    np.random.seed(42)
    mkt = np.random.normal(0.0005, 0.01, 50)
    stock = 1.2 * mkt + np.random.normal(0, 0.008, 50)
    idio = calc_idiosyncratic_vol(pd.Series(stock), pd.Series(mkt), min_history=20)
    assert idio is not None
    assert idio > 0.0
    # Deterministic
    assert calc_idiosyncratic_vol(pd.Series(stock), pd.Series(mkt), min_history=20) == idio

    # Zero market variance handles safely
    zero_mkt = pd.Series([0.0] * 30)
    assert calc_idiosyncratic_vol(pd.Series(stock[:30]), zero_mkt, min_history=20) is None


def test_ranking_drops_names_with_insufficient_history():
    """rank_name_vols drops names with insufficient history and sorts deterministically."""
    names = [
        NameVol("A", iv=0.30, fair=0.20, earnings_clear=True),
        NameVol("B", iv=0.30, fair=0.20, earnings_clear=True),
        NameVol("C", iv=0.30, fair=0.20, earnings_clear=True),
    ]

    # Name A has 30 days of IV (sufficient)
    # Name B has 5 days of IV (insufficient)
    # Name C has 30 days of IV (sufficient, higher vol of vol)
    iv_histories = {
        "A": [0.20 + 0.001 * (i % 2) for i in range(30)],
        "B": [0.20, 0.22, 0.21, 0.23, 0.20],  # only 5 days
        "C": [0.20 + 0.02 * (i % 2) for i in range(30)],
    }

    ranked = rank_name_vols(names, rank_metric="vol_of_vol", iv_histories=iv_histories, min_history=20)
    # Name B must be dropped!
    underlyings = [nv.underlying for nv, _ in ranked]
    assert "B" not in underlyings
    # C has higher vol-of-vol than A
    assert underlyings == ["C", "A"]


def test_ranking_deterministic_tie_breaking():
    """Ties in score are broken deterministically by ticker symbol."""
    names = [
        NameVol("ZZZ", iv=0.30, fair=0.20, earnings_clear=True),  # gap = 0.10
        NameVol("AAA", iv=0.30, fair=0.20, earnings_clear=True),  # gap = 0.10
        NameVol("MMM", iv=0.30, fair=0.20, earnings_clear=True),  # gap = 0.10
    ]
    ranked = rank_name_vols(names, rank_metric="gap")
    underlyings = [nv.underlying for nv, _ in ranked]
    # Alphabetical tie break AAA, MMM, ZZZ
    assert underlyings == ["AAA", "MMM", "ZZZ"]


def test_make_crossvol_decide_delta_hedged_call():
    """make_crossvol_decide with delta_hedged_call emits Open and Hedge actions."""
    from datetime import date

    from tradingbot.utils.options import StructurePick

    class DummyView:
        expiry = date(2024, 2, 16)
        live = True
        spot = 100.0

    class DummyChainMarket:
        today = date(2024, 1, 15)

        def vol_index(self, _):
            return 18.0

        def scan_scores(self):
            return {"AAPL": 2.5}

        def ohlc(self, names):
            return {n: pd.DataFrame({"close": [100.0, 101.0]}) for n in names}

        def name_vol(self, u, target_dte, ohlc, min_obs=60):
            return NameVol(u, iv=0.30, fair=0.20, earnings_clear=True), DummyView()

    class DummyHoldings:
        def underlyings(self):
            return set()

        def equity(self):
            return 100_000.0

    rules = CrossVolRules(structure="delta_hedged_call", min_gap=0.05)
    decide = make_crossvol_decide(rules, ["AAPL"], min_history=1)

    # Monkeypatch select_vertical to return a fake pick
    import tradingbot.utils.options as opt

    orig_select = opt.select_vertical
    try:
        opt.select_vertical = lambda *args, **kwargs: StructurePick(
            "AAPL", date(2024, 2, 16), (("AAPL240216C00105000", -1), ("AAPL240216C00115000", 1)), True, 100.0
        )
        actions = decide(DummyChainMarket(), DummyHoldings())
        assert len(actions) == 2
        assert isinstance(actions[0], Open)
        assert isinstance(actions[1], Hedge)
        assert actions[1].underlying == "AAPL"
    finally:
        opt.select_vertical = orig_select
