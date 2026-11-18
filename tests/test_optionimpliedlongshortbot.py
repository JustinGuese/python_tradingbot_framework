"""Unit tests for OptionImpliedLongShortBot and long/short portfolio sizing (Step E1)."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import pandas as pd
import pytest

from tradingbot.optionimpliedlongshortbot import (
    CASH_PROXY,
    DEFAULT_UNIVERSE,
    ImpliedLongShortRules,
    OptionImpliedLongShortBot,
    compute_long_short_portfolio_weights,
)
from tradingbot.utils.botclass import Bot
from tradingbot.utils.cash_park import park_idle_cash


@pytest.fixture
def make_bot(mocker):
    """Create an OptionImpliedLongShortBot without database connection."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _factory(rules=None, park_cash=True):
        bot = OptionImpliedLongShortBot(rules=rules, park_cash=park_cash)
        bot.bot_name = "OptionImpliedLongShortBot"
        bot.tickers = list(dict.fromkeys((*DEFAULT_UNIVERSE, CASH_PROXY)))
        bot.tradeable_universe = list(DEFAULT_UNIVERSE)
        bot.rules = rules or OptionImpliedLongShortBot.RULES
        bot.park_cash = park_cash
        return bot

    return _factory


def test_bot_initialization(make_bot):
    bot = make_bot()
    assert bot.ALLOW_SHORT is True
    assert bot.MAX_GROSS == 1.8
    assert bot.CASH_PROXY == "SHV"
    assert "SHV" in bot.tickers
    assert "SHV" not in bot.tradeable_universe
    assert set(DEFAULT_UNIVERSE).issubset(set(bot.tickers))
    assert bot.tradeable_universe == list(DEFAULT_UNIVERSE)


def test_compute_long_short_portfolio_weights_basic():
    # 10 names, top_n = 5, gross_limit = 1.8
    ranks = pd.Series(
        {
            "A": 1.0,
            "B": 0.9,
            "C": 0.8,
            "D": 0.7,
            "E": 0.6,
            "F": 0.5,
            "G": 0.4,
            "H": 0.3,
            "I": 0.2,
            "J": 0.1,
        }
    )
    weights = compute_long_short_portfolio_weights(ranks, top_n=5, gross_limit=1.8)

    # Top 5 long (+0.18 each), bottom 5 short (-0.18 each)
    for sym in ["A", "B", "C", "D", "E"]:
        assert pytest.approx(weights[sym], abs=1e-6) == 0.18

    for sym in ["F", "G", "H", "I", "J"]:
        assert pytest.approx(weights[sym], abs=1e-6) == -0.18

    assert pytest.approx(weights.sum(), abs=1e-6) == 0.0
    assert pytest.approx(weights.abs().sum(), abs=1e-6) == 1.8


def test_compute_long_short_portfolio_weights_selection():
    # 20 names, top_n = 5
    ranks = pd.Series({f"SYM_{i}": float(i) for i in range(20)})
    weights = compute_long_short_portfolio_weights(ranks, top_n=5, gross_limit=1.8)

    # Top 5: SYM_19 .. SYM_15
    for i in range(15, 20):
        assert pytest.approx(weights[f"SYM_{i}"], abs=1e-6) == 0.18

    # Bottom 5: SYM_0 .. SYM_4
    for i in range(5):
        assert pytest.approx(weights[f"SYM_{i}"], abs=1e-6) == -0.18

    # Middle 10 receive 0.0
    for i in range(5, 15):
        assert pytest.approx(weights[f"SYM_{i}"], abs=1e-6) == 0.0

    assert pytest.approx(weights.sum(), abs=1e-6) == 0.0
    assert pytest.approx(weights.abs().sum(), abs=1e-6) == 1.8


def test_compute_long_short_portfolio_weights_small_universe_capped():
    # Fewer names than 2 * top_n (e.g. 6 names, top_n=5)
    # Must cap side count to 6 // 2 = 3 names so longs and shorts do not overlap
    ranks = pd.Series({"A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}, dtype=float)
    weights = compute_long_short_portfolio_weights(ranks, top_n=5, gross_limit=1.8)

    # Top 3 long (+0.18 each)
    assert pytest.approx(weights["A"], abs=1e-6) == 0.18
    assert pytest.approx(weights["B"], abs=1e-6) == 0.18
    assert pytest.approx(weights["C"], abs=1e-6) == 0.18

    # Bottom 3 short (-0.18 each)
    assert pytest.approx(weights["D"], abs=1e-6) == -0.18
    assert pytest.approx(weights["E"], abs=1e-6) == -0.18
    assert pytest.approx(weights["F"], abs=1e-6) == -0.18

    assert pytest.approx(weights.sum(), abs=1e-6) == 0.0
    assert pytest.approx(weights.abs().sum(), abs=1e-6) == 1.08


def test_compute_long_short_portfolio_weights_nans():
    ranks = pd.Series({"A": 1.0, "B": float("nan"), "C": 0.5, "D": float("nan"), "E": 0.1})
    weights = compute_long_short_portfolio_weights(ranks, top_n=5, gross_limit=1.8)

    # Only 3 valid names -> k = 3 // 2 = 1 name
    assert weights["B"] == 0.0
    assert weights["D"] == 0.0
    assert pytest.approx(weights["A"], abs=1e-6) == 0.18
    assert pytest.approx(weights["E"], abs=1e-6) == -0.18
    assert pytest.approx(weights["C"], abs=1e-6) == 0.0


def test_cash_parking_integration():
    # Sizing for 6 names: gross = 1.08, net = 0.0
    ranks = pd.Series({"A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}, dtype=float)
    weights_series = compute_long_short_portfolio_weights(ranks, top_n=5, gross_limit=1.8)
    raw_weights = {str(k): float(v) for k, v in weights_series.items() if abs(v) > 1e-6}

    parked = park_idle_cash(raw_weights, proxy=CASH_PROXY, max_gross=1.8)

    # idle = min(1.0 - 0.0, 1.8 - 1.08) = 0.72
    assert pytest.approx(parked[CASH_PROXY], abs=1e-6) == 0.72
    # Total gross = 1.08 + 0.72 = 1.80 <= 1.8
    gross = sum(abs(w) for w in parked.values())
    assert pytest.approx(gross, abs=1e-6) == 1.80

    # Completely flat portfolio parks 1.0 in SHV
    flat_parked = park_idle_cash({}, proxy=CASH_PROXY, max_gross=1.8)
    assert pytest.approx(flat_parked[CASH_PROXY], abs=1e-6) == 1.0


def test_compute_target_weights_mock_signals(make_bot):
    bot = make_bot(rules=ImpliedLongShortRules(top_n=5, gross_limit=1.8))

    # Construct synthetic signals DataFrame
    symbols = list(DEFAULT_UNIVERSE[:16])
    now = pd.Timestamp("2026-11-06")
    rows = []
    for i, s in enumerate(symbols):
        rows.append(
            {
                "snapshot_at": now,
                "underlying": s,
                "spot": 100.0,
                "cp_spread": float(i) * 0.01,
                "put_skew": 0.05 - float(i) * 0.002,
                "rr25": float(i) * 0.015,
                "call_iv_atm": 0.25,
                "put_iv_atm": 0.25,
                "expiry": now + pd.Timedelta(days=30),
                "dte": 30.0,
            }
        )
    signals_df = pd.DataFrame(rows)

    weights = bot.compute_target_weights(signals_df=signals_df, as_of=now)

    assert len(weights) > 0
    # Check top names long, bottom names short
    # 5 long names at +0.18, 5 short names at -0.18
    longs = {k: v for k, v in weights.items() if v > 0 and k != CASH_PROXY}
    shorts = {k: v for k, v in weights.items() if v < 0}

    assert len(longs) == 5
    assert len(shorts) == 5
    assert pytest.approx(sum(longs.values()), abs=1e-6) == 0.9
    assert pytest.approx(sum(shorts.values()), abs=1e-6) == -0.9
    assert pytest.approx(sum(longs.values()) + sum(shorts.values()), abs=1e-6) == 0.0

    # Total gross
    total_gross = sum(abs(v) for v in weights.values())
    assert total_gross <= 1.8 + 1e-6
