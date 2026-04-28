import unittest
from unittest.mock import MagicMock
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

        self.bot_weights = {"bot1": 1.0}
        self.copier = LiveTradeCopier(
            broker=self.broker,
            bot_weights=self.bot_weights,
            dry_run=True
        )
        self.copier.bot_repo = self.bot_repo
        self.copier.data_service = self.data_service

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

    def test_calculate_orders_skips_zero_price(self):
        target_weights = {"AAPL": {"weight": 0.5, "type": "stock"}}
        current_positions = {"AAPL": 0}
        total_equity = 2000
        
        # Mock price as 0
        self.broker.get_latest_price.return_value = 0.0
        
        orders = self.copier._calculate_orders(target_weights, current_positions, total_equity)
        self.assertEqual(len(orders), 0)

    def test_execute_orders_ordering_and_delay(self):
        import time
        from unittest.mock import patch
        
        orders = [
            {"symbol": "MSFT", "quantity": 10, "side": "BUY", "value": 1000, "type": "stock"},
            {"symbol": "AAPL", "quantity": 5, "side": "SELL", "value": 750, "type": "stock"},
        ]
        
        self.copier.dry_run = False
        self.copier.settle_delay_seconds = 5
        
        with patch("time.sleep") as mock_sleep:
            self.copier._execute_orders(orders)
            
            # Verify SELL was called before BUY
            # self.broker.place_order.call_args_list shows the order
            calls = self.broker.place_order.call_args_list
            self.assertEqual(calls[0][0][2], "SELL")
            self.assertEqual(calls[1][0][2], "BUY")
            
            # Verify sleep was called
            mock_sleep.assert_called_once_with(5)

    def test_copier_continues_on_broker_exception(self):
        orders = [
            {"symbol": "AAPL", "quantity": 5, "side": "SELL", "value": 750, "type": "stock"},
            {"symbol": "MSFT", "quantity": 10, "side": "SELL", "value": 1000, "type": "stock"},
        ]
        self.copier.dry_run = False
        
        # First call raises error, second succeeds
        self.broker.place_order.side_effect = [Exception("API Error"), None]
        
        with self.assertLogs("tradingbot.livetrade.copier", level="ERROR") as cm:
            self.copier._execute_orders(orders)
            # Verify both were attempted
            self.assertEqual(self.broker.place_order.call_count, 2)
            # Verify error was logged
            self.assertTrue(any("Failed to execute" in line for line in cm.output))

    def test_portfolio_fraction_scales_target_value(self):
        # equity 2000, fraction 0.5 -> effective 1000; weight 0.5 -> target $500
        # 0 current shares @ $100 -> buy 500/100 = 5
        self.copier.portfolio_fraction = 0.5
        target_weights = {"AAPL": {"weight": 0.5, "type": "stock"}}
        self.broker.get_latest_price.return_value = 100.0
        # _calculate_orders receives the already-scaled equity from sync()
        orders = self.copier._calculate_orders(target_weights, {}, total_equity=1000.0)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "BUY")
        self.assertAlmostEqual(orders[0]["quantity"], 5.0)

    def test_sync_aborts_on_negative_equity(self):
        # Mock target weights
        mock_bot = MagicMock(spec=Bot)
        mock_bot.portfolio = {"USD": 1000, "AAPL": 10}
        self.bot_repo.create_or_get_bot.return_value = mock_bot
        self.data_service.get_latest_prices_batch.return_value = {"AAPL": 150.0}
        
        # Mock negative equity and empty positions
        self.broker.get_total_equity.return_value = -100.0
        self.broker.get_positions.return_value = {}
        
        with self.assertLogs("tradingbot.livetrade.copier", level="ERROR") as cm:
            self.copier.sync()
            self.assertTrue(any("Aborting sync due to non-positive equity" in line for line in cm.output))
        
        # Verify no orders were executed
        self.broker.place_order.assert_not_called()

if __name__ == "__main__":
    unittest.main()
