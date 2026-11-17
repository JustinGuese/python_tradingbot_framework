"""
Unit tests for step A2: retail venue coverage and unmapped-name behaviour.

Pins:
1. Current strict mode behaviour (copier.py:101): one unmapped ticker aborts sync.
2. Proposed drop-to-cash path: an untradeable / unlisted ticker dropped via is_tradeable=False
   leaves the other names traded and the weight in cash (or SHV cash proxy).
3. Offline coverage check logic for S&P 100 on retail venues.
"""

from unittest.mock import MagicMock, patch

import pytest

from scripts.onetime_retail_venue_coverage import check_retail_coverage_offline, get_sp100_full_universe
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.utils.universes import SP100, SP100_EXCLUDED


@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.name = "retail_broker"
    broker.always_open = True
    broker.get_total_equity.return_value = 10_000.0
    broker.get_positions.return_value = {}
    broker.cancel_open_orders.return_value = 0
    return broker


def test_strict_copier_aborts_on_unmapped_ticker(mock_broker, caplog):
    """Pin current behaviour: one unmapped ticker on a strict copier aborts the whole sync."""
    copier = LiveTradeCopier(broker=mock_broker, bot_weights={"bot1": 1.0}, dry_run=True)
    copier.strict_mapping = True

    # Target contains one valid symbol and one unmapped symbol
    target_weights = {"AAPL": 0.5, "UNKNOWN_TICKER": 0.5}

    mock_broker.is_tradeable.side_effect = lambda s: True

    def mock_map(s):
        if s == "AAPL":
            return {"symbol": "AAPL", "type": "stock"}
        return None  # Unmapped

    mock_broker.map_symbol.side_effect = mock_map

    with (
        patch.object(copier, "_calculate_target_weights", return_value=target_weights),
        patch.object(copier, "_calculate_orders") as mock_calc,
    ):
        copier.sync()

    # The sync must abort before order calculation or execution
    mock_calc.assert_not_called()
    mock_broker.place_order.assert_not_called()
    assert any("STRICT MODE: Aborting sync due to unmapped tickers" in record.message for record in caplog.records)


def test_drop_to_cash_path_trades_remaining_and_leaves_weight_in_cash(mock_broker):
    """Proposed drop-to-cash path: unlisted ticker drops, remaining names trade, weight stays cash."""
    copier = LiveTradeCopier(broker=mock_broker, bot_weights={"bot1": 1.0}, dry_run=True)
    copier.strict_mapping = True
    copier.cash_proxy = None  # Isolate from cash proxy to verify pure cash holding

    target_weights = {"AAPL": 0.5, "UNLISTED_STOCK": 0.5}

    # Adapter marks UNLISTED_STOCK as untradeable
    mock_broker.is_tradeable.side_effect = lambda s: s != "UNLISTED_STOCK"
    mock_broker.map_symbol.side_effect = lambda s: {"symbol": s, "type": "stock"}

    with (
        patch.object(copier, "_calculate_target_weights", return_value=target_weights),
        patch.object(copier, "_calculate_orders", return_value=[]) as mock_calc,
        patch.object(copier, "_execute_orders"),
    ):
        copier.sync()

    # Sync must NOT abort; orders must be calculated
    mock_calc.assert_called_once()
    broker_targets = mock_calc.call_args.args[0]

    # Only AAPL is in the tradeable target; UNLISTED_STOCK was dropped to cash
    assert set(broker_targets.keys()) == {"AAPL"}
    assert broker_targets["AAPL"]["weight"] == 0.5  # Weight is NOT redistributed


def test_drop_to_cash_path_parks_weight_in_shv(mock_broker):
    """Proposed drop-to-cash path with cash proxy: dropped weight is parked into SHV."""
    copier = LiveTradeCopier(broker=mock_broker, bot_weights={"bot1": 1.0}, dry_run=True)
    copier.strict_mapping = True
    copier.cash_proxy = "SHV"
    copier.cash_buffer = 0.02

    target_weights = {"AAPL": 0.5, "UNLISTED_STOCK": 0.5}

    mock_broker.is_tradeable.side_effect = lambda s: s != "UNLISTED_STOCK"
    mock_broker.map_symbol.side_effect = lambda s: {"symbol": s, "type": "stock" if s == "AAPL" else "etf"}

    with (
        patch.object(copier, "_calculate_target_weights", return_value=target_weights),
        patch.object(copier, "_calculate_orders", return_value=[]) as mock_calc,
    ):
        copier.sync()

    mock_calc.assert_called_once()
    broker_targets = mock_calc.call_args.args[0]

    # AAPL is held at 50%, and uninvested 50% (minus 2% cash buffer = 48%) is parked in SHV
    assert set(broker_targets.keys()) == {"AAPL", "SHV"}
    assert broker_targets["AAPL"]["weight"] == 0.5
    assert pytest.approx(broker_targets["SHV"]["weight"], 1e-6) == 0.48


def test_offline_coverage_sp100_and_recent_adds():
    """Verify offline coverage audit includes SP100 constituents, recent adds, and marks live lookups."""
    universe = get_sp100_full_universe()
    assert set(SP100).issubset(set(universe))
    assert set(SP100_EXCLUDED).issubset(set(universe))

    # Explicitly check required names from PLAN.md
    for required in ("PLTR", "SNDK", "GEV", "DELL", "ANET"):
        assert required in universe

    results = check_retail_coverage_offline(tickers=universe)
    assert len(results) == len(universe)

    # eToro and MT5 should mark unmapped stocks as "to be filled"
    for ticker in ("PLTR", "SNDK", "GEV", "DELL", "ANET"):
        assert results[ticker]["etoro"]["mapped_symbol"] == "to be filled"
        assert results[ticker]["etoro"]["is_tradeable"] == "to be filled"
        assert results[ticker]["mt5"]["mapped_symbol"] == "to be filled"
        assert results[ticker]["mt5"]["is_tradeable"] == "to be filled"
        # Collective2 adapter logic tradeable offline for US stocks
        assert results[ticker]["collective2"]["is_tradeable"] == "yes"

    # BRK.B should be marked as excluded / untradeable on C2 due to dot notation
    assert results["BRK.B"]["collective2"]["is_tradeable"] == "no"
