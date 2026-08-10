import pytest
import json
import os
from pathlib import Path
from tradingbot.livetrade.symbol_map import SymbolMapper

@pytest.fixture
def temp_map_file(tmp_path):
    map_data = {
        "BRK-B": {
            "interactive_brokers": {"symbol": "BRK B", "type": "stock"},
            "collective2": {"symbol": "BRK.B", "type": "stock"}
        }
    }
    f = tmp_path / "test_symbol_map.json"
    f.write_text(json.dumps(map_data))
    return str(f)

def test_symbol_mapper_load_overrides(temp_map_file):
    mapper = SymbolMapper(map_file=temp_map_file)
    assert "BRK-B" in mapper.overrides
    assert mapper.overrides["BRK-B"]["interactive_brokers"]["symbol"] == "BRK B"

@pytest.mark.parametrize("yf_symbol, expected_broker, expected_type", [
    ("AAPL", "AAPL", "stock"),
    ("EURUSD=X", "EURUSD", "forex"),
    ("BTC-USD", "BTCUSD", "crypto"),
    ("^GSPC", "SPX", "index"),
])
def test_map_symbol_default_rules(yf_symbol, expected_broker, expected_type):
    mapper = SymbolMapper(map_file="nonexistent.json")
    res = mapper.map_symbol(yf_symbol)
    assert res["symbol"] == expected_broker
    assert res["type"] == expected_type

@pytest.mark.parametrize("broker_symbol, expected_yf", [
    ("AAPL", "AAPL"),
    ("EURUSD", "EURUSD=X"),
    ("BTCUSD", "BTC-USD"),
    ("SPX", "^GSPC"),
])
def test_unmap_symbol_heuristics(broker_symbol, expected_yf):
    mapper = SymbolMapper(map_file="nonexistent.json")
    assert mapper.unmap_symbol(broker_symbol) == expected_yf

def test_map_symbol_with_broker_overrides(temp_map_file):
    mapper = SymbolMapper(map_file=temp_map_file)
    
    # IB Override
    res_ib = mapper.map_symbol("BRK-B", broker_name="interactive_brokers")
    assert res_ib["symbol"] == "BRK B"
    
    # C2 Override
    res_c2 = mapper.map_symbol("BRK-B", broker_name="collective2")
    assert res_c2["symbol"] == "BRK.B"
    
    # Default if broker not in override
    res_other = mapper.map_symbol("BRK-B", broker_name="other_broker")
    assert res_other["symbol"] == "BRK-B"

def test_xau_maps_to_gdx_on_collective2():
    """XAUZenbotTreeBot trades ^XAU (PHLX Gold/Silver index), which is not tradeable.
    The shipped map must translate it to the GDX miners ETF for Collective2."""
    mapper = SymbolMapper()  # the real shipped symbol_map.json
    res = mapper.map_symbol("^XAU", broker_name="collective2")
    assert res["symbol"] == "GDX"
    assert res["type"] == "stock"


def test_xau_has_no_default_rule():
    """Guards the reason the copier needs its caret check: without an override,
    ^XAU falls through the default rules unchanged rather than returning None."""
    mapper = SymbolMapper(map_file="nonexistent.json")
    assert mapper.map_symbol("^XAU")["symbol"] == "^XAU"


def test_hyperliquid_roundtrip():
    """The default crypto rule yields BTCUSD, which is not a Hyperliquid coin.
    The override must map to BTC *and* invert cleanly, because the LiveBroker
    price fallback unmaps the broker symbol before asking yfinance."""
    mapper = SymbolMapper()  # the real shipped symbol_map.json
    assert mapper.map_symbol("BTC-USD", broker_name="hyperliquid")["symbol"] == "BTC"
    assert mapper.unmap_symbol("BTC", broker_name="hyperliquid") == "BTC-USD"
    assert mapper.map_symbol("ETH-USD", broker_name="hyperliquid")["symbol"] == "ETH"
    assert mapper.unmap_symbol("ETH", broker_name="hyperliquid") == "ETH-USD"


def test_unmap_symbol_with_broker_overrides(temp_map_file):
    mapper = SymbolMapper(map_file=temp_map_file)
    
    # Should find BRK-B from "BRK B" if broker_name is IB
    assert mapper.unmap_symbol("BRK B", broker_name="interactive_brokers") == "BRK-B"
    assert mapper.unmap_symbol("BRK.B", broker_name="collective2") == "BRK-B"
    
    # Should fail to find override without correct broker name
    assert mapper.unmap_symbol("BRK B") == "BRK B"
