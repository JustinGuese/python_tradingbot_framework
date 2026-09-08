"""
The single CronJob that executes user-built strategies.

It reads a table owned by another service (tradingbot-ui-backend), so the two
failure modes worth pinning are the ones that are silent: a schema that has
drifted, and one bad strategy taking the whole batch down with it.
"""

import json

import pytest
from sqlalchemy import text

from tradingbot import run_user_strategies as runner

SPEC = {
    "version": 1,
    "tickers": ["SPY"],
    "interval": "1d",
    "period": "2y",
    "entry": {
        "match": "all",
        "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": "<", "right": {"const": 30}}],
    },
    "exit": {
        "match": "all",
        "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": ">", "right": {"const": 70}}],
    },
}


@pytest.fixture
def strategy_table(db_session):
    """
    A stand-in for the API's table.

    Deliberately built from the same column list the runner declares in
    REQUIRED_COLUMNS, so this fixture cannot accidentally supply a column the
    runner is not entitled to read.
    """
    db_session.execute(
        text(
            """
            CREATE TABLE userstrategy (
                id TEXT PRIMARY KEY,
                bot_name TEXT NOT NULL,
                spec TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT,
                last_run_at TEXT,
                last_error TEXT
            )
            """
        )
    )
    db_session.commit()
    return db_session


def _insert(session, strategy_id, bot_name, status="live", spec=None):
    session.execute(
        text(
            "INSERT INTO userstrategy (id, bot_name, spec, status, created_at) "
            "VALUES (:id, :bot_name, :spec, :status, :created_at)"
        ),
        {
            "id": strategy_id,
            "bot_name": bot_name,
            "spec": json.dumps(spec or SPEC),
            "status": status,
            "created_at": "2026-01-01",
        },
    )
    session.commit()


# ---------------------------------------------------------------------------
#  Loading
# ---------------------------------------------------------------------------


def test_only_live_strategies_are_loaded(strategy_table):
    _insert(strategy_table, "a", "us_a", status="live")
    _insert(strategy_table, "b", "us_b", status="draft")
    _insert(strategy_table, "c", "us_c", status="paused")

    loaded = runner.load_live_strategies(strategy_table)

    assert [bot_name for _, bot_name, _ in loaded] == ["us_a"]


def test_a_spec_stored_as_json_text_is_parsed(strategy_table):
    """Postgres hands back a dict; SQLite hands back a string."""
    _insert(strategy_table, "a", "us_a")

    _, _, spec = runner.load_live_strategies(strategy_table)[0]

    assert isinstance(spec, dict)
    assert spec["tickers"] == ["SPY"]


def test_an_unparseable_spec_is_skipped_not_fatal(strategy_table):
    strategy_table.execute(
        text("INSERT INTO userstrategy (id, bot_name, spec, status) VALUES ('bad', 'us_bad', 'not json', 'live')")
    )
    _insert(strategy_table, "good", "us_good")
    strategy_table.commit()

    loaded = runner.load_live_strategies(strategy_table)

    assert [bot_name for _, bot_name, _ in loaded] == ["us_good"]


def test_a_dict_spec_passes_through():
    assert runner._as_dict({"a": 1}) == {"a": 1}


def test_a_non_object_spec_is_rejected():
    with pytest.raises(TypeError):
        runner._as_dict(42)


# ---------------------------------------------------------------------------
#  Schema contract
# ---------------------------------------------------------------------------


def test_a_missing_table_fails_loudly(db_session):
    """
    Exiting 0 here would be indistinguishable from "no live strategies today",
    so a missing table has to raise.
    """
    with pytest.raises(RuntimeError, match="does not exist"):
        runner._assert_table_ready(db_session)


def test_a_missing_column_fails_loudly(db_session):
    db_session.execute(text("CREATE TABLE userstrategy (id TEXT PRIMARY KEY, bot_name TEXT, spec TEXT)"))
    db_session.commit()

    with pytest.raises(RuntimeError, match="missing columns"):
        runner._assert_table_ready(db_session)


def test_a_complete_table_passes(strategy_table):
    runner._assert_table_ready(strategy_table)


def test_required_columns_are_all_actually_selected():
    """
    The declared contract must cover what the SQL really touches, or the
    startup check passes while a query fails mid-run.
    """
    statements = str(runner._SELECT_LIVE) + str(runner._MARK_RUN)
    for column in ("id", "bot_name", "spec", "status", "last_run_at", "last_error"):
        assert column in statements
        assert column in runner.REQUIRED_COLUMNS


# ---------------------------------------------------------------------------
#  Execution
# ---------------------------------------------------------------------------


def test_run_one_validates_before_executing(mocker):
    """
    A spec valid at save time can become invalid later — a `ta` upgrade renaming
    a column, or the interval whitelist narrowing. Executing a half-understood
    spec with real position sizing is worse than skipping it.
    """
    from tradingbot.utils.strategy_spec import SpecError

    with pytest.raises(SpecError):
        runner.run_one("us_bad", {**SPEC, "interval": "1m"})


def test_run_one_builds_an_attached_bot_and_runs_it(mocker):
    """Live execution needs the portfolio row, so attach_db must be True."""
    built = {}

    class _FakeBot:
        def __init__(self, spec, name, attach_db=True):
            built["name"] = name
            built["attach_db"] = attach_db

        def run(self):
            built["ran"] = True

    mocker.patch.object(runner, "RuleBot", _FakeBot)

    assert runner.run_one("us_x", SPEC) == 1
    assert built == {"name": "us_x", "attach_db": True, "ran": True}
