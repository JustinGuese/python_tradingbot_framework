import numpy as np
import pandas as pd
import pytest

import scripts.onetime_gate_other_markets as study
from tradingbot import quantsmd_sma_daily as base
from tradingbot.countrytrendgatebot import (
    CAP,
    FAST,
    SLOW,
    UNIVERSE,
    CountryTrendGateBot,
    compute_weights,
    ew_index,
    gate_factor,
)


def _frame(cols: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=pd.bdate_range("2020-01-01", periods=n))


def _trend(n: int, drift: float) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def test_gate_matches_the_study_simulator_on_random_walks():
    # The bot must trade exactly the rule that produced the documented backtest numbers.
    rng = np.random.default_rng(5)
    n = 520
    closes = pd.DataFrame(
        {t: 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n))) for t in UNIVERSE[:7]},
        index=pd.bdate_range("2018-01-01", periods=n),
    )
    ref = study.book_weights(closes, study.gate_series(study.ew_index(closes)))
    for pos in (230, 300, 380, 450, n - 1):
        got = compute_weights(closes.iloc[: pos + 1], FAST, SLOW, CAP)
        want = {k: v for k, v in ref.iloc[pos].items() if v > 0}
        assert got.keys() == want.keys(), pos
        for k in want:
            assert got[k] == pytest.approx(want[k]), (pos, k)


def test_gate_off_when_universe_index_falls_below_its_sma200():
    closes = _frame({t: _trend(300, -0.002) for t in UNIVERSE[:5]})
    assert gate_factor(closes) == 0.0
    assert compute_weights(closes) == {}


def test_gate_on_leaves_the_original_book_untouched(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", lambda *a, **k: {"EWJ": 0.25, "EWG": 0.25})
    closes = _frame({t: _trend(300, 0.002) for t in UNIVERSE[:5]})
    assert gate_factor(closes) == 1.0
    assert compute_weights(closes) == {"EWJ": 0.25, "EWG": 0.25}


def test_half_off_scale_halves_the_book(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", lambda *a, **k: {"EWJ": 0.25, "EWG": 0.25})
    closes = _frame({t: _trend(300, -0.002) for t in UNIVERSE[:5]})
    assert compute_weights(closes, off_scale=0.5) == {"EWJ": pytest.approx(0.125), "EWG": pytest.approx(0.125)}


def test_short_history_fails_open():
    closes = _frame({t: _trend(150, -0.002) for t in UNIVERSE[:5]})
    assert gate_factor(closes) == 1.0


def test_a_member_with_missing_bars_does_not_break_the_index():
    closes = _frame({t: _trend(300, 0.001) for t in UNIVERSE[:4]})
    closes.loc[closes.index[100:130], "EWJ"] = np.nan  # a gap, as yfinance sometimes returns
    idx = ew_index(closes)
    assert idx.notna().all() and (idx > 0).all()


def test_universe_is_twenty_unique_tradeable_tickers_and_bot_is_untuned():
    assert len(UNIVERSE) == 20 and len(set(UNIVERSE)) == 20
    assert not any(t.startswith("^") for t in UNIVERSE)
    assert CountryTrendGateBot.BACKTEST_PERIOD == "max"
    assert not getattr(CountryTrendGateBot, "param_grid", None)
