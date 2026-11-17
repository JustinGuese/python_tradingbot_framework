"""
Tests for backtest trial registry (Step S1d).

Verifies:
- Rows written once per backtest/record_trial call with correct fields and deterministic params hash.
- DB failures are swallowed and never break a backtest or count helper.
- Helper trial_count works for 'all' and individual bots.
- Edge cases: None/empty params, NaN/inf floats, SQL injection safety.
"""

from contextlib import contextmanager
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingbot.utils.backtest import backtest_bot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.db import BacktestTrial, Base
from tradingbot.utils.trial_registry import (
    compute_params_hash,
    record_trial,
    trial_count,
)


@pytest.fixture
def memory_db(monkeypatch):
    """Isolated in-memory SQLite database for trial registry tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def _session():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr("tradingbot.utils.trial_registry.get_db_session", _session)
    monkeypatch.setattr("tradingbot.utils.trial_registry.init_db", lambda *a, **k: None)
    return engine, _session


class _DummyBot(Bot):
    """Lightweight test bot that avoids Bot.__init__ DB connections."""

    def __init__(self, bot_name="TestDummyBot", **kwargs):
        self.bot_name = bot_name
        self.symbol = "AAA"
        self.tickers = ["AAA"]
        self.interval = "1d"
        self.period = "1y"
        self.params = kwargs.copy()
        self.data = None
        self.datas = {}
        self.datasettings = (None, None)

    def decisionFunction(self, row):
        return 0


def _sample_data(n_bars=30):
    rng = np.random.default_rng(42)
    prices = 100.0 + np.cumsum(rng.normal(0, 1.0, n_bars))
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n_bars, freq="D", tz="UTC"),
            "close": prices,
        }
    )


# --------------------------------------------------------------------------- #
#  Functionality & deterministic hashing                                      #
# --------------------------------------------------------------------------- #


def test_params_hash_deterministic():
    """Hash must be identical across key ordering and different for different values."""
    h1 = compute_params_hash({"fast": 10, "slow": 50})
    h2 = compute_params_hash({"slow": 50, "fast": 10})
    h3 = compute_params_hash({"fast": 10, "slow": 51})
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64  # sha256 hex string


def test_record_trial_writes_row_and_count_helper(memory_db):
    """record_trial appends a row with correct fields and trial_count counts correctly."""
    _, session_fn = memory_db
    trial_dt = datetime(2026, 11, 14, 12, 0, 0)

    # Initially empty
    assert trial_count("all") == 0
    assert trial_count("AlphaBot") == 0

    # Record first trial
    t1 = record_trial(
        bot="AlphaBot",
        params={"window": 20},
        date=trial_dt,
        alpha=0.08,
        alpha_t=2.5,
        beta=0.75,
        n=252,
    )
    assert t1 is not None

    with session_fn() as s:
        rows = s.query(BacktestTrial).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.bot == "AlphaBot"
        assert r.params_hash == compute_params_hash({"window": 20})
        assert r.date == trial_dt
        assert pytest.approx(r.alpha) == 0.08
        assert pytest.approx(r.alpha_t) == 2.5
        assert pytest.approx(r.beta) == 0.75
        assert r.n == 252

    # Record trial for a second bot
    t2 = record_trial(
        bot="BetaBot",
        params={"stop": 0.05},
        alpha=0.01,
        alpha_t=0.5,
        beta=1.1,
        n=100,
    )
    assert t2 is not None

    # Check trial_count for both and overall
    assert trial_count("all") == 2
    assert trial_count() == 2
    assert trial_count("AlphaBot") == 1
    assert trial_count("BetaBot") == 1
    assert trial_count("NonExistentBot") == 0


def test_backtest_bot_records_trial_once_per_call(memory_db):
    """backtest_bot appends row by default and respects record_trial=False."""
    bot = _DummyBot("MyStrategyBot", fast=5, slow=15)
    data = _sample_data()

    # Call with default record_trial=True
    res1 = backtest_bot(
        bot,
        data=data,
        save_to_db=False,
        save_results_to_db=False,
    )
    assert isinstance(res1, dict)
    assert trial_count(bot.bot_name) == 1
    assert trial_count("all") == 1

    # Call with record_trial=False: no new row appended
    res2 = backtest_bot(
        bot,
        data=data,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=False,
    )
    assert isinstance(res2, dict)
    assert trial_count(bot.bot_name) == 1
    assert trial_count("all") == 1

    # Call again with record_trial=True: new row appended
    backtest_bot(
        bot,
        data=data,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=True,
    )
    assert trial_count(bot.bot_name) == 2


# --------------------------------------------------------------------------- #
#  Resilience & DB failure swallowing                                         #
# --------------------------------------------------------------------------- #


def test_db_failure_swallowed_in_record_trial_and_trial_count(monkeypatch):
    """When DB operations fail, record_trial returns None and trial_count returns 0 without raising."""

    @contextmanager
    def _failing_session():
        raise RuntimeError("DB connection lost")
        yield  # unreachable

    monkeypatch.setattr("tradingbot.utils.trial_registry.get_db_session", _failing_session)
    monkeypatch.setattr("tradingbot.utils.trial_registry.init_db", lambda *a, **k: None)

    # record_trial swallows exception and returns None
    result = record_trial(bot="FailingBot", params={"a": 1})
    assert result is None

    # trial_count swallows exception and returns 0
    cnt = trial_count("FailingBot")
    assert cnt == 0
    cnt_all = trial_count("all")
    assert cnt_all == 0


def test_backtest_bot_survives_db_failure(monkeypatch):
    """backtest_bot completes normally even if recording trial throws an exception."""

    @contextmanager
    def _failing_session():
        raise RuntimeError("DB connection failed during backtest")
        yield

    monkeypatch.setattr("tradingbot.utils.trial_registry.get_db_session", _failing_session)
    monkeypatch.setattr("tradingbot.utils.trial_registry.init_db", lambda *a, **k: None)

    bot = _DummyBot("ResilientBot")
    data = _sample_data()

    # Must not raise RuntimeError
    res = backtest_bot(
        bot,
        data=data,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=True,
    )
    assert isinstance(res, dict)
    assert "yearly_return" in res


# --------------------------------------------------------------------------- #
#  Edge cases & Security                                                      #
# --------------------------------------------------------------------------- #


def test_edge_cases_and_security(memory_db):
    """Handles None/empty params, non-finite floats, and SQL injection strings safely."""
    _, session_fn = memory_db

    # 1. None params and None metrics
    t_none = record_trial(
        bot="NullBot",
        params=None,
        alpha=None,
        alpha_t=None,
        beta=None,
        n=None,
    )
    assert t_none is not None
    assert t_none.params_hash == compute_params_hash({})

    # 2. Non-finite floats (NaN, Inf) sanitized to None
    t_nan = record_trial(
        bot="NaNBot",
        params={"test": 1},
        alpha=float("nan"),
        alpha_t=float("inf"),
        beta=-float("inf"),
        n=50,
    )
    assert t_nan is not None
    assert t_nan.alpha is None
    assert t_nan.alpha_t is None
    assert t_nan.beta is None
    assert t_nan.n == 50

    # 3. SQL injection pattern in bot name handled via ORM parameterization
    sql_inj = "Bot'; DROP TABLE backtest_trials; --"
    t_inj = record_trial(bot=sql_inj, params={"key": "val"})
    assert t_inj is not None
    assert trial_count(sql_inj) == 1

    # Table still exists and rows are queryable
    with session_fn() as s:
        rows = s.query(BacktestTrial).all()
        assert len(rows) == 3
