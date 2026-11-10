"""
Unit tests for livetrade symbol coverage tool (coverage.py).
"""

from unittest.mock import patch

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.coverage import (
    blend_universe,
    check_coverage,
    main,
)
from tradingbot.livetrade.registry import BrokerSpec


class DummyBroker(LiveBroker):
    """Stub broker for coverage testing without external calls."""

    name = "dummy"

    def __init__(self, tradeable_map: dict[str, bool], symbol_map: dict[str, dict | None]):
        self._tradeable_map = tradeable_map
        self._symbol_map = symbol_map
        self.connected = False
        self.disconnected = False

    def connect(self, readonly: bool = False) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True

    def is_tradeable(self, yf_symbol: str) -> bool:
        return self._tradeable_map.get(yf_symbol, True)

    def map_symbol(self, yf_symbol: str) -> dict | None:
        return self._symbol_map.get(yf_symbol)

    def get_cash(self) -> float:
        return 1000.0

    def get_positions(self) -> dict[str, float]:
        return {}

    def get_total_equity(self) -> float:
        return 1000.0

    def place_order(self, broker_symbol: str, quantity: float, side: str, symbol_type: str | None = None) -> None:
        raise NotImplementedError("Coverage check must never place orders")

    def search_symbol(self, query: str) -> list[dict]:
        return []


def test_blend_universe_contains_known_members():
    """Universe must contain known members from all three blend bots."""
    universe = blend_universe()

    assert isinstance(universe, list)
    assert len(universe) == len(set(universe)), "Universe should have no duplicates"
    assert universe == sorted(universe), "Universe should be sorted"

    # TARegimeMultiAssetBot members
    for sym in ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "UUP"]:
        assert sym in universe

    # TSMOMTrendBot unique members
    for sym in ["IWM", "SLV", "USO", "DBA", "FXE", "FXY", "FXB", "FXA", "FXF"]:
        assert sym in universe

    # GoldenButterflyMomBot unique members
    for sym in ["VTI", "IJS", "SHY", "IAU"]:
        assert sym in universe


def test_check_coverage_reports_gaps():
    """check_coverage correctly classifies resolved, missing, and untradeable symbols."""
    tradeable = {
        "SPY": True,
        "TLT": True,
        "UNTRADEABLE_TICKER": False,
        "UNMAPPED_TICKER": True,
        "^INDEX_TICKER": True,
    }
    mapping = {
        "SPY": {"symbol": "SPY", "type": "stock"},
        "TLT": {"symbol": "TLT.US", "type": "stock"},
        "UNTRADEABLE_TICKER": {"symbol": "UNTRADEABLE"},
        "UNMAPPED_TICKER": None,
        "^INDEX_TICKER": {"symbol": "^INDEX_TICKER", "type": "index"},
    }
    broker = DummyBroker(tradeable_map=tradeable, symbol_map=mapping)

    tickers = ["SPY", "TLT", "UNTRADEABLE_TICKER", "UNMAPPED_TICKER", "^INDEX_TICKER"]
    results = check_coverage(broker, tickers)

    assert "resolved" in results
    assert "missing" in results
    assert "untradeable" in results

    # Resolved
    assert results["resolved"]["SPY"] == "SPY"
    assert results["resolved"]["TLT"] == "TLT.US"

    # Untradeable (checked before mapping)
    assert "UNTRADEABLE_TICKER" in results["untradeable"]

    # Missing (None or ^ prefix unmapped index)
    assert "UNMAPPED_TICKER" in results["missing"]
    assert "^INDEX_TICKER" in results["missing"]


def test_coverage_cli_exit_codes_and_security(capsys):
    """CLI exits 1 on gaps, 0 when all resolved, 2 on config error, and never prints credentials."""
    # 1. Unknown broker -> exit 2
    rc_unknown = main(["--broker", "nonexistent_broker"])
    assert rc_unknown == 2

    # 2. Config error (missing required env) -> exit 2
    mock_spec_failing = BrokerSpec(
        key="test_fail",
        logger_name="test_fail",
        build=lambda: DummyBroker({}, {}),
        required_env=("MOCK_SECRET_KEY_THAT_IS_UNSET_12345",),
    )
    with patch.dict("tradingbot.livetrade.coverage.REGISTRY", {"test_fail": mock_spec_failing}, clear=False):
        rc_config = main(["--broker", "test_fail"])
        assert rc_config == 2

    # 3. Gaps exist -> exit 1
    broker_with_gaps = DummyBroker(
        tradeable_map={"SPY": True},
        symbol_map={"SPY": {"symbol": "SPY"}},
    )
    mock_spec_gaps = BrokerSpec(
        key="test_gaps",
        logger_name="test_gaps",
        build=lambda: broker_with_gaps,
        required_env=(),
    )
    with patch.dict("tradingbot.livetrade.coverage.REGISTRY", {"test_gaps": mock_spec_gaps}, clear=False):
        rc_gaps = main(["--broker", "test_gaps"])
        assert rc_gaps == 1
        assert broker_with_gaps.connected is True
        assert broker_with_gaps.disconnected is True

    # 4. All resolved -> exit 0
    all_tickers = blend_universe()
    broker_full = DummyBroker(
        tradeable_map=dict.fromkeys(all_tickers, True),
        symbol_map={t: {"symbol": t} for t in all_tickers},
    )
    mock_spec_full = BrokerSpec(
        key="test_full",
        logger_name="test_full",
        build=lambda: broker_full,
        required_env=(),
    )
    with patch.dict("tradingbot.livetrade.coverage.REGISTRY", {"test_full": mock_spec_full}, clear=False):
        rc_full = main(["--broker", "test_full"])
        assert rc_full == 0

    # Ensure no credentials/passwords leaked into stdout
    captured = capsys.readouterr()
    assert "MOCK_SECRET_KEY_THAT_IS_UNSET_12345" in captured.err or captured.out or ""
    assert "password" not in captured.out.lower()
