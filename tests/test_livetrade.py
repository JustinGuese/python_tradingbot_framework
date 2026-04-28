import unittest
import json
import os
from unittest.mock import MagicMock, patch
from pathlib import Path
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.livetrade.broker import LiveBroker
from tradingbot.utils.db import Bot

class TestLiveTrade(unittest.TestCase):
    def setUp(self):
        self.broker = MagicMock(spec=LiveBroker)
        self.broker.name = "mock_broker"
        self.bot_repo = MagicMock()
        self.data_service = MagicMock()
        self.broker.get_latest_price.return_value = 150.0
        
        # Default marker file path for tests
        self.marker_file = Path("data/state/test_livetrade_seen.json")
        if self.marker_file.exists():
            self.marker_file.unlink()

        self.bot_weights = {"bot1": 1.0}
        self.copier = LiveTradeCopier(
            broker=self.broker,
            bot_weights=self.bot_weights,
            copy_open_trades=True,
            dry_run=True
        )
        self.copier.bot_repo = self.bot_repo
        self.copier.data_service = self.data_service
        self.copier.marker_file = self.marker_file

    def tearDown(self):
        if self.marker_file.exists():
            self.marker_file.unlink()

    def test_calculate_target_weights(self):
        # Mock bot portfolio
        mock_bot = MagicMock(spec=Bot)
        mock_bot.portfolio = {"USD": 1000, "AAPL": 10}
        self.bot_repo.create_or_get_bot.return_value = mock_bot
        
        # Mock prices
        self.data_service.get_latest_prices_batch.return_value = {"AAPL": 150.0}
        
        # Bot total value: 1000 (USD) + 10 * 150 (AAPL) = 2500
        # AAPL weight: 1500 / 2500 = 0.6
        
        weights = self.copier._calculate_target_weights()
        self.assertEqual(weights, {"AAPL": 0.6})

    def test_calculate_orders(self):
        target_weights = {"AAPL": {"weight": 0.6, "type": "stock"}}
        current_positions = {"AAPL": 5} # 5 shares already
        total_equity = 2500
        
        self.broker.get_latest_price.return_value = 150.0
        
        # Target value: 2500 * 0.6 = 1500
        # Current value: 5 * 150 = 750
        # Diff: 1500 - 750 = 750
        # Qty to buy: 750 / 150 = 5
        
        orders = self.copier._calculate_orders(target_weights, current_positions, total_equity)
        
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["symbol"], "AAPL")
        self.assertEqual(orders[0]["side"], "BUY")
        self.assertAlmostEqual(orders[0]["quantity"], 5.0)

    def test_strict_mapping(self):
        self.copier.strict_mapping = True
        
        # Mock target weights
        mock_bot = MagicMock(spec=Bot)
        mock_bot.portfolio = {"USD": 1000, "UNKNOWN": 10}
        self.bot_repo.create_or_get_bot.return_value = mock_bot
        self.data_service.get_latest_prices_batch.return_value = {"UNKNOWN": 10.0}
        
        # Mock broker mapping failure
        self.broker.map_symbol.return_value = None
        
        with self.assertLogs("tradingbot.livetrade.copier", level="ERROR") as cm:
            self.copier.sync()
            self.assertTrue(any("STRICT MODE: Aborting sync" in line for line in cm.output))

    def test_copy_open_trades_marker_file(self):
        self.copier.copy_open_trades = False
        
        # Mock target weights to avoid early exit in sync if it gets past markers
        self.copier._calculate_target_weights = MagicMock(return_value={"AAPL": 0.6})
        self.broker.map_symbol.return_value = {"symbol": "AAPL", "type": "stock"}
        self.broker.get_total_equity.return_value = 1000.0
        self.broker.get_positions.return_value = {}

        # Run 1: Should mark and skip
        with self.assertLogs("tradingbot.livetrade.copier", level="INFO") as cm:
            self.copier.sync()
            self.assertTrue(any("Marked first-run for bot1" in line for line in cm.output))
            self.assertTrue(any("Skipping initial rebalance" in line for line in cm.output))
        
        self.assertTrue(self.marker_file.exists())
        with open(self.marker_file, "r") as f:
            markers = json.load(f)
            self.assertTrue(markers.get("mock_broker:bot1"))

        # Run 2: Should proceed
        with self.assertLogs("tradingbot.livetrade.copier", level="INFO") as cm:
            self.copier.sync()
            self.assertTrue(any("Starting sync" in line for line in cm.output))
            # Should NOT contain skip message
            self.assertFalse(any("Skipping initial rebalance" in line for line in cm.output))

if __name__ == "__main__":
    unittest.main()
