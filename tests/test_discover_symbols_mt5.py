"""Tests for discover_symbols with MT5 (--broker mt5_mql5)."""

import json

import pytest

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.discover_symbols import SymbolDiscoverer, main
from tradingbot.livetrade.registry import REGISTRY, BrokerSpec


class FakeMT5Broker(LiveBroker):
    """Minimal fake broker implementing LiveBroker for discover_symbols testing."""

    name = "mt5_mql5"

    def __init__(self):
        self.connected = False
        self.disconnected = False

    def connect(self, readonly: bool = False) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True

    def get_cash(self) -> float:
        return 1000.0

    def get_positions(self) -> dict[str, float]:
        return {}

    def get_total_equity(self) -> float:
        return 1000.0

    def place_order(self, broker_symbol, quantity, side, symbol_type=None) -> None:
        pass

    def map_symbol(self, yf_symbol: str):
        return None

    def search_symbol(self, query: str) -> list[dict]:
        # Return suffixed symbol for QQQ
        if query in ("QQQ", "QQQ.US"):
            return [
                {
                    "symbol": "QQQ.US",
                    "type": "stock",
                    "description": "Invesco QQQ Trust",
                    "exchange": "",
                    "score": 90,
                }
            ]
        return []


@pytest.fixture
def fake_broker():
    return FakeMT5Broker()


@pytest.fixture
def stub_spec(fake_broker):
    return BrokerSpec(
        key="mt5_mql5",
        logger_name="livetrade_mt5_mql5",
        build=lambda: fake_broker,
        required_env=(),
    )


def test_discover_symbols_mt5_resolves_and_finds_suffixed_candidates(monkeypatch, tmp_path, stub_spec, fake_broker):
    """--broker mt5_mql5 resolves stub spec, connects, and discovers suffixed candidates into review file."""
    monkeypatch.setitem(REGISTRY, "mt5_mql5", stub_spec)

    # Patch collect_all_tickers to return a controlled set
    monkeypatch.setattr(SymbolDiscoverer, "collect_all_tickers", lambda self: {"QQQ"})

    map_file = tmp_path / "symbol_map.json"
    map_file.write_text(
        json.dumps(
            {
                "QQQ": {
                    "collective2": {
                        "symbol": "QQQ",
                        "type": "stock",
                        "verified": "2026-04-28",
                        "source": "human",
                    }
                }
            }
        )
    )

    review_file = tmp_path / "symbol_map.review.json"

    ret = main(
        [
            "--broker",
            "mt5_mql5",
            "--review-file",
            str(review_file),
            "--map-file",
            str(map_file),
        ]
    )

    assert ret == 0
    assert fake_broker.connected is True
    assert fake_broker.disconnected is True

    assert review_file.exists()
    review_data = json.loads(review_file.read_text())
    assert "QQQ" in review_data
    candidates = review_data["QQQ"]["candidates"]
    assert any(c["symbol"] == "QQQ.US" for c in candidates)


def test_discover_symbols_mt5_apply_writes_under_key_without_touching_others(
    monkeypatch, tmp_path, stub_spec, fake_broker
):
    """--broker mt5_mql5 --apply writes mapping under 'mt5_mql5' preserving other brokers' entries."""
    monkeypatch.setitem(REGISTRY, "mt5_mql5", stub_spec)

    map_file = tmp_path / "symbol_map.json"
    initial_map = {
        "QQQ": {
            "collective2": {
                "symbol": "QQQ",
                "type": "stock",
                "verified": "2026-04-28",
                "source": "human",
            },
            "interactive_brokers": {
                "symbol": "CNDX",
                "type": "stock",
                "conid": 75961314,
                "verified": "2026-11-03",
                "source": "human",
            },
        },
        "SPY": {
            "darwinex": {
                "symbol": "SPY.US",
                "type": "stock",
                "verified": "2026-05-04",
                "source": "human",
            }
        },
    }
    map_file.write_text(json.dumps(initial_map, indent=2))

    review_file = tmp_path / "symbol_map.review.json"
    review_data = {
        "_instructions": "Pick candidates...",
        "QQQ": {
            "default_mapping": {"symbol": "QQQ", "type": "stock"},
            "candidates": [{"symbol": "QQQ.US", "type": "stock"}],
            "selected_symbol": "QQQ.US",
            "selected_type": "stock",
        },
    }
    review_file.write_text(json.dumps(review_data, indent=2))

    ret = main(
        [
            "--broker",
            "mt5_mql5",
            "--apply",
            "--review-file",
            str(review_file),
            "--map-file",
            str(map_file),
        ]
    )

    assert ret == 0
    assert fake_broker.connected is True
    assert fake_broker.disconnected is True

    updated_map = json.loads(map_file.read_text())

    # 1. Collective2 and IB entries on QQQ are untouched
    assert updated_map["QQQ"]["collective2"] == initial_map["QQQ"]["collective2"]
    assert updated_map["QQQ"]["interactive_brokers"] == initial_map["QQQ"]["interactive_brokers"]

    # 2. MT5 entry was written under 'mt5_mql5'
    assert updated_map["QQQ"]["mt5_mql5"]["symbol"] == "QQQ.US"
    assert updated_map["QQQ"]["mt5_mql5"]["type"] == "stock"
    assert updated_map["QQQ"]["mt5_mql5"]["source"] == "human"

    # 3. SPY (other symbol) was completely untouched
    assert updated_map["SPY"] == initial_map["SPY"]
