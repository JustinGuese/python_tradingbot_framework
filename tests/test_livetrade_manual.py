"""
Pytests for manual notify broker and runner integration (S4).

Requirements from PLAN-manualnotify.md:
1. First run builds the full portfolio: exactly one Telegram call, which contains BUY lines.
   State is saved, and cash is never negative.
2. Second run with the same targets and prices sends nothing and writes nothing
   (the "only on change" core).
3. A Telegram failure raises, so state is NOT persisted (a retry re-notifies), and the
   exception text does not contain the token.
4. Whole-share rounding: a BUY below 1 share of notional is skipped, and a SELL liquidates
   the exact held qty.
5. Missing TELEGRAM_BOT_TOKEN gives ConfigError, which gives runner exit code 2.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from tradingbot.livetrade import runner
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.livetrade.manual import ManualBroker
from tradingbot.livetrade.registry import REGISTRY, ConfigError
from tradingbot.utils.db import ManualPortfolio


@pytest.fixture(autouse=True)
def _patch_manual_session(sqlite_db, monkeypatch):
    """Route ManualBroker DB calls to the SQLite test engine."""
    monkeypatch.setattr("tradingbot.livetrade.manual.get_db_session", sqlite_db)


def test_first_run_builds_full_portfolio_and_saves_state(db_session, monkeypatch):
    """
    1. First run builds the full portfolio: exactly one Telegram call, which
    contains BUY lines. State is saved, and cash is never negative.
    """
    mock_send = MagicMock()
    monkeypatch.setattr("tradingbot.livetrade.manual.send_telegram_message", mock_send)
    monkeypatch.setenv("LIVETRADE_MANUAL_EQUITY_USD", "50000")

    broker = ManualBroker(telegram_token="fake_token", telegram_chat_id="fake_chat")
    prices = {"SPY": 500.0, "QQQ": 400.0}
    broker.get_latest_price = lambda s: prices.get(s, 100.0)

    copier = LiveTradeCopier(broker=broker, bot_weights={"bot1": 1.0}, dry_run=False)
    copier.cash_proxy = None
    monkeypatch.setattr(copier, "_calculate_target_weights", lambda: {"SPY": 0.5, "QQQ": 0.4})
    copier.data_service = MagicMock()
    copier.data_service.get_latest_prices_batch.return_value = prices
    copier.execution_policy = MagicMock()
    copier.execution_policy.explain_window.return_value = (True, "")

    broker.connect()
    assert broker.cash == 50000.0
    assert broker.positions == {}

    copier.sync()
    broker.finish_sync(dry_run=False)

    # Exactly one Telegram call
    assert mock_send.call_count == 1
    sent_text = mock_send.call_args[0][2]
    assert "BUY 50 SPY" in sent_text
    assert "BUY 50 QQQ" in sent_text

    # Cash is never negative
    assert broker.cash >= 0.0

    # State is saved
    db_session.expire_all()
    row = db_session.get(ManualPortfolio, "manual")
    assert row is not None
    assert row.cash >= 0.0
    assert row.cash == broker.cash
    assert row.positions == {"SPY": 50, "QQQ": 50}


def test_second_run_with_same_targets_is_silent(db_session, monkeypatch):
    """
    2. Second run with the same targets and prices sends nothing and writes nothing
    (the "only on change" core).
    """
    mock_send = MagicMock()
    monkeypatch.setattr("tradingbot.livetrade.manual.send_telegram_message", mock_send)
    monkeypatch.setenv("LIVETRADE_MANUAL_EQUITY_USD", "50000")

    prices = {"SPY": 500.0, "QQQ": 400.0}

    # Run 1: initial setup
    broker1 = ManualBroker(telegram_token="fake_token", telegram_chat_id="fake_chat")
    broker1.get_latest_price = lambda s: prices.get(s, 100.0)
    copier1 = LiveTradeCopier(broker=broker1, bot_weights={"bot1": 1.0}, dry_run=False)
    copier1.cash_proxy = None
    monkeypatch.setattr(copier1, "_calculate_target_weights", lambda: {"SPY": 0.5, "QQQ": 0.4})
    copier1.data_service = MagicMock()
    copier1.data_service.get_latest_prices_batch.return_value = prices
    copier1.execution_policy = MagicMock()
    copier1.execution_policy.explain_window.return_value = (True, "")

    broker1.connect()
    copier1.sync()
    broker1.finish_sync(dry_run=False)
    assert mock_send.call_count == 1

    # Record saved state in DB after first run
    db_session.expire_all()
    row_after_run1 = db_session.get(ManualPortfolio, "manual")
    initial_updated_at = row_after_run1.updated_at
    initial_cash = row_after_run1.cash
    initial_positions = dict(row_after_run1.positions)

    # Run 2: same targets and prices
    mock_send.reset_mock()
    broker2 = ManualBroker(telegram_token="fake_token", telegram_chat_id="fake_chat")
    broker2.get_latest_price = lambda s: prices.get(s, 100.0)
    copier2 = LiveTradeCopier(broker=broker2, bot_weights={"bot1": 1.0}, dry_run=False)
    copier2.cash_proxy = None
    monkeypatch.setattr(copier2, "_calculate_target_weights", lambda: {"SPY": 0.5, "QQQ": 0.4})
    copier2.data_service = MagicMock()
    copier2.data_service.get_latest_prices_batch.return_value = prices
    copier2.execution_policy = MagicMock()
    copier2.execution_policy.explain_window.return_value = (True, "")

    spy_save = MagicMock(wraps=broker2._save_state)
    broker2._save_state = spy_save

    broker2.connect()
    copier2.sync()
    broker2.finish_sync(dry_run=False)

    # Sends nothing
    mock_send.assert_not_called()
    assert len(broker2.pending) == 0

    # Writes nothing
    spy_save.assert_not_called()
    db_session.expire_all()
    row_after_run2 = db_session.get(ManualPortfolio, "manual")
    assert row_after_run2.updated_at == initial_updated_at
    assert row_after_run2.cash == initial_cash
    assert row_after_run2.positions == initial_positions


def test_telegram_failure_does_not_persist_and_sanitizes_token(db_session):
    """
    3. A Telegram failure raises, so state is NOT persisted (a retry re-notifies),
    and the exception text does not contain the token.
    """
    token = "SECRET_BOT_TOKEN_xyz987"
    chat_id = "chat_999"

    broker = ManualBroker(telegram_token=token, telegram_chat_id=chat_id)
    broker.get_latest_price = lambda s: 100.0
    broker.connect()
    broker.place_order("SPY", quantity=10, side="BUY")

    # Simulate httpx error containing token in URL
    with (
        patch.object(
            httpx.Client,
            "post",
            side_effect=httpx.ConnectError(f"Failed to connect to https://api.telegram.org/bot{token}/sendMessage"),
        ),
        pytest.raises(RuntimeError) as exc_info,
    ):
        broker.finish_sync(dry_run=False)

    # Exception text does NOT contain the token
    exc_message = str(exc_info.value)
    assert token not in exc_message
    assert "[REDACTED_TOKEN]" in exc_message

    # State is NOT persisted
    db_session.expire_all()
    assert db_session.get(ManualPortfolio, "manual") is None
    # Orders remain pending for retry
    assert len(broker.pending) == 1

    # Retry re-notifies and succeeds
    with patch.object(
        httpx.Client,
        "post",
        return_value=httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", "http://test")),
    ):
        broker.finish_sync(dry_run=False)

    # State IS persisted on successful retry
    db_session.expire_all()
    row = db_session.get(ManualPortfolio, "manual")
    assert row is not None
    assert row.positions == {"SPY": 10}
    assert len(broker.pending) == 0


def test_whole_share_rounding_and_liquidation():
    """
    4. Whole-share rounding: a BUY below 1 share of notional is skipped,
    and a SELL liquidates the exact held qty.
    """
    broker = ManualBroker(telegram_token="token", telegram_chat_id="chat")
    broker.cash = 10_000.0
    broker.positions = {"AAPL": 5, "MSFT": 10}
    broker.get_latest_price = lambda s: 100.0

    # BUY below 1 share of notional is skipped
    broker.place_order("GOOG", quantity=0.8, side="BUY")
    assert "GOOG" not in broker.positions
    assert len(broker.pending) == 0
    assert broker.cash == 10_000.0

    # SELL liquidates exact held qty (even if order quantity has floating point overshoot)
    broker.place_order("AAPL", quantity=5.5, side="SELL")
    assert "AAPL" not in broker.positions
    assert broker.pending[-1]["symbol"] == "AAPL"
    assert broker.pending[-1]["quantity"] == 5
    assert broker.pending[-1]["side"] == "SELL"
    assert broker.cash == 10_500.0  # 5 shares * $100 proceeds added


def test_missing_telegram_token_gives_config_error_and_exit_code_2(monkeypatch):
    """
    5. Missing TELEGRAM_BOT_TOKEN gives ConfigError, which gives runner exit code 2.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    spec = REGISTRY["manual"]

    # Calling check_required_env directly raises ConfigError
    with pytest.raises(ConfigError) as exc_info:
        spec.check_required_env()
    assert "TELEGRAM_BOT_TOKEN" in str(exc_info.value)

    # Calling spec.build directly raises ConfigError
    with pytest.raises(ConfigError) as exc_info_build:
        spec.build()
    assert "TELEGRAM_BOT_TOKEN" in str(exc_info_build.value)

    # runner.run gives exit code 2
    exit_code = runner.run(spec)
    assert exit_code == 2
