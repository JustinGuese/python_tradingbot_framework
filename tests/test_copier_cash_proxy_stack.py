"""Tests proving a bot-supplied SHV weight is not parked a second time by copier."""

from unittest.mock import MagicMock

import pytest

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.utils.cash_park import park_idle_cash


def _make_copier(cash_proxy: str = "SHV", cash_buffer: float = 0.02):
    broker = MagicMock(spec=LiveBroker)
    broker.name = "test_broker"
    broker.supports_short = False
    broker.supports_limit_orders = False
    broker.is_tradeable.return_value = True
    broker.map_symbol.side_effect = lambda s: {"symbol": s, "type": "stock"}

    copier = LiveTradeCopier(broker=broker, bot_weights={"bot1": 1.0}, dry_run=True)
    copier.cash_proxy = cash_proxy
    copier.cash_buffer = cash_buffer
    return copier, broker


def test_copier_does_not_double_park_when_bot_supplies_shv():
    """A bot parking idle cash into SHV should not be parked a second time by copier."""
    copier, _broker = _make_copier(cash_proxy="SHV", cash_buffer=0.02)

    # Bot weights produced with park_idle_cash
    bot_targets = park_idle_cash({"AAPL": 0.6}, proxy="SHV")
    assert pytest.approx(bot_targets["AAPL"]) == 0.6
    assert pytest.approx(bot_targets["SHV"]) == 0.4

    broker_targets = {s: {"weight": w, "type": "stock"} for s, w in bot_targets.items()}

    copier._park_idle_cash(broker_targets)

    # SHV weight must NOT increase (no double parking)
    assert pytest.approx(broker_targets["SHV"]["weight"]) == 0.4
    assert pytest.approx(broker_targets["AAPL"]["weight"]) == 0.6

    total_gross = sum(abs(m["weight"]) for m in broker_targets.values())
    assert pytest.approx(total_gross) == 1.0


def test_copier_counts_existing_shv_on_partial_parking():
    """If bot supplies partial SHV, copier only parks the remaining uninvested cash."""
    copier, _broker = _make_copier(cash_proxy="SHV", cash_buffer=0.02)

    # Bot holds 50% AAPL and 20% SHV (gross = 0.70)
    broker_targets = {
        "AAPL": {"weight": 0.50, "type": "stock"},
        "SHV": {"weight": 0.20, "type": "stock"},
    }

    copier._park_idle_cash(broker_targets)

    # Remaining idle capacity = 1.0 - 0.70 - 0.02 = 0.28
    # Existing 0.20 is counted, so SHV becomes 0.20 + 0.28 = 0.48, NOT 0.20 + 0.48 = 0.68
    assert pytest.approx(broker_targets["SHV"]["weight"]) == 0.48
    assert pytest.approx(broker_targets["AAPL"]["weight"]) == 0.50

    total_gross = sum(abs(m["weight"]) for m in broker_targets.values())
    assert pytest.approx(total_gross) == 0.98


def test_copier_all_cash_bot_parked_in_shv():
    """Bot holding 100% SHV (all cash parked) is not modified by copier."""
    copier, _broker = _make_copier(cash_proxy="SHV", cash_buffer=0.02)

    bot_targets = park_idle_cash({}, proxy="SHV")
    assert bot_targets == {"SHV": 1.0}

    broker_targets = {"SHV": {"weight": 1.0, "type": "stock"}}
    copier._park_idle_cash(broker_targets)

    assert pytest.approx(broker_targets["SHV"]["weight"]) == 1.0


def test_copier_multi_bot_aggregated_shv_not_overparked():
    """Multiple bots supplying SHV weights aggregate without copier overparking."""
    copier, _broker = _make_copier(cash_proxy="SHV", cash_buffer=0.02)

    # Bot 1: 50% AAPL, 50% SHV (allocated 60% of copier) -> 0.30 AAPL, 0.30 SHV
    # Bot 2: 40% MSFT, 60% SHV (allocated 40% of copier) -> 0.16 MSFT, 0.24 SHV
    # Aggregated: AAPL 0.30, MSFT 0.16, SHV 0.54. Total gross = 1.00.
    broker_targets = {
        "AAPL": {"weight": 0.30, "type": "stock"},
        "MSFT": {"weight": 0.16, "type": "stock"},
        "SHV": {"weight": 0.54, "type": "stock"},
    }

    copier._park_idle_cash(broker_targets)

    assert pytest.approx(broker_targets["SHV"]["weight"]) == 0.54
    assert pytest.approx(broker_targets["AAPL"]["weight"]) == 0.30
    assert pytest.approx(broker_targets["MSFT"]["weight"]) == 0.16
    total_gross = sum(abs(m["weight"]) for m in broker_targets.values())
    assert pytest.approx(total_gross) == 1.00
