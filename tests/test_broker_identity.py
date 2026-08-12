"""
Tests for LiveBroker.account_ref / .is_sandbox.

equity_recorder keys live_equity rows on (broker, account_ref, date) and stamps
is_testnet from is_sandbox. Before these were part of the ABC, the recorder used
a getattr() chain over adapter-specific attribute names — `query_address or
account_id or system_id`, and `getattr(broker, "testnet", False)`. That chain only
ever matched Hyperliquid, which was harmless while Hyperliquid was the only broker
recording equity. Now that every broker records, a demo eToro or Darwinex account
would have been stamped as real money in the published track record.
"""

from unittest.mock import MagicMock

import pytest

from tradingbot.livetrade.collective2 import Collective2Broker
from tradingbot.livetrade.darwinex import DarwinexBroker
from tradingbot.livetrade.etoro import EtoroBroker
from tradingbot.livetrade.interactive_brokers import InteractiveBrokersBroker


@pytest.fixture
def stubs():
    """Adapters build a DataService (and thus an engine) unless one is injected."""
    return {"symbol_mapper": MagicMock(), "data_service": MagicMock()}


def test_etoro_demo_is_a_sandbox(stubs):
    broker = EtoroBroker(api_key="k", user_key="u", demo=True, **stubs)
    assert broker.is_sandbox is True


def test_etoro_live_is_not_a_sandbox(stubs):
    broker = EtoroBroker(api_key="k", user_key="u", demo=False, **stubs)
    assert broker.is_sandbox is False


def test_darwinex_demo_is_a_sandbox(stubs):
    broker = DarwinexBroker(username="u", password="p", account_id="A1", demo=True, **stubs)
    assert broker.is_sandbox is True
    assert broker.account_ref == "A1"


def test_darwinex_live_is_not_a_sandbox(stubs):
    broker = DarwinexBroker(username="u", password="p", account_id="A1", demo=False, **stubs)
    assert broker.is_sandbox is False


def test_darwinex_account_ref_is_read_late(stubs):
    # account_id is None until _login() discovers it; account_ref must not have
    # been frozen to "" at construction time.
    broker = DarwinexBroker(username="u", password="p", account_id=None, demo=True, **stubs)
    assert broker.account_ref == ""
    broker.account_id = "DISCOVERED"
    assert broker.account_ref == "DISCOVERED"


@pytest.mark.parametrize(
    ("account_id", "expected"),
    [("DU1234567", True), ("du1234567", True), ("U1234567", False), ("", False)],
)
def test_ibkr_paper_accounts_are_du_prefixed(account_id, expected, stubs):
    broker = InteractiveBrokersBroker(account_id=account_id, **stubs)
    assert broker.is_sandbox is expected


def test_ibkr_account_ref_is_the_account_id(stubs):
    assert InteractiveBrokersBroker(account_id="U999", **stubs).account_ref == "U999"


def test_collective2_account_ref_is_the_system_id(stubs):
    broker = Collective2Broker(api_key="k", system_id="123456", **stubs)
    assert broker.account_ref == "123456"
    assert broker.is_sandbox is False


def test_etoro_has_no_account_ref_but_stays_distinguishable(stubs):
    # eToro exposes no account identifier; rows stay unique because live_equity
    # keys on the broker name too.
    broker = EtoroBroker(api_key="k", user_key="u", demo=True, **stubs)
    assert broker.account_ref == ""
    assert broker.name == "etoro"
