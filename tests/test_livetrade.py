import os
import unittest
from unittest.mock import MagicMock, patch

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.utils.db import Bot


class TestLiveTrade(unittest.TestCase):
    def setUp(self):
        self.broker = MagicMock(spec=LiveBroker)
        self.broker.name = "mock_broker"
        self.bot_repo = MagicMock()
        self.data_service = MagicMock()
        self.broker.get_latest_price.return_value = 150.0
        # Ample cash, so _execute_orders' buy-clamp (copier.py:225-247) is a no-op
        # and tests asserting order sequencing aren't perturbed by scaling. Without
        # a value here spec-mock returns a MagicMock and the `> 0` compare raises.
        self.broker.get_cash.return_value = 100_000.0

        self.bot_weights = {"bot1": 1.0}
        self.copier = LiveTradeCopier(broker=self.broker, bot_weights=self.bot_weights, dry_run=True)
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
        current_positions = {"AAPL": 5.0}  # 5 shares already
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

    def test_sync_aborts_when_positions_cannot_be_read(self):
        """
        A failed positions read must abort, not be treated as a flat account.

        sync() is a full target-state reconciliation, so falling through with
        no positions makes every target weight look like a fresh buy — doubling
        exposure on top of holdings the broker still has.
        """
        mock_bot = MagicMock(spec=Bot)
        mock_bot.portfolio = {"USD": 1000, "AAPL": 10}
        self.bot_repo.create_or_get_bot.return_value = mock_bot
        self.data_service.get_latest_prices_batch.return_value = {"AAPL": 150.0}
        self.broker.map_symbol.return_value = {"symbol": "AAPL", "type": "stock"}
        self.broker.get_total_equity.return_value = 10_000.0
        self.broker.get_positions.side_effect = RuntimeError("broker 503")

        with self.assertLogs("tradingbot.livetrade.copier", level="ERROR") as cm:
            self.copier.sync()

        self.assertTrue(any("could not read positions" in line for line in cm.output))
        self.broker.place_order.assert_not_called()

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

    def test_strict_mapping_rejects_untranslated_index_ticker(self):
        """map_symbol() never returns None for an unknown ^-ticker — the default
        rules pass it through unchanged. Strict mode must still abort, or we'd
        submit an order for a symbol like ^XAU that the broker just rejects."""
        self.copier.strict_mapping = True

        mock_bot = MagicMock(spec=Bot)
        mock_bot.portfolio = {"USD": 1000, "^XAU": 10}
        self.bot_repo.create_or_get_bot.return_value = mock_bot
        self.data_service.get_latest_prices_batch.return_value = {"^XAU": 100.0}

        # What SymbolMapper's default rules actually do with an unknown index.
        self.broker.map_symbol.return_value = {"symbol": "^XAU", "type": "stock", "source": "default-rule"}

        with self.assertLogs("tradingbot.livetrade.copier", level="ERROR") as cm:
            self.copier.sync()
        self.assertTrue(any("STRICT MODE: Aborting sync" in line for line in cm.output))
        self.broker.place_order.assert_not_called()

    def test_untradeable_leg_is_dropped_to_cash_not_redistributed(self):
        """A leg the broker structurally cannot trade (foreign listing, crypto) is
        dropped, NOT counted as unmapped — so strict mapping does not abort the
        whole sync — and its weight is not pushed onto the remaining legs."""
        self.copier.strict_mapping = True
        mock_bot = MagicMock(spec=Bot)
        mock_bot.portfolio = {"USD": 0, "QQQ": 10, "RENW.DE": 10}
        self.bot_repo.create_or_get_bot.return_value = mock_bot
        self.data_service.get_latest_prices_batch.return_value = {"QQQ": 100.0, "RENW.DE": 100.0}
        self.broker.is_tradeable.side_effect = lambda s: not s.endswith(".DE")
        self.broker.map_symbol.side_effect = lambda s: {"symbol": s, "type": "stock"}
        self.broker.get_total_equity.return_value = 1000.0
        self.broker.get_positions.return_value = {}
        self.broker.cancel_open_orders.return_value = 0

        self.copier.cash_proxy = None  # isolate the drop rule from cash parking

        with patch.object(self.copier, "_calculate_orders", return_value=[]) as calc:
            self.copier.sync()

        targets = calc.call_args.args[0]
        self.assertEqual(set(targets), {"QQQ"})
        self.assertAlmostEqual(targets["QQQ"]["weight"], 0.5)  # 50% stays cash

    # ------------------------------------------------------------------ #
    #  Idle cash -> cash proxy (SHV)                                      #
    # ------------------------------------------------------------------ #

    def _park(self, targets, proxy="SHV", buffer=0.02, tradeable=True):
        self.copier.cash_proxy = proxy
        self.copier.cash_buffer = buffer
        self.broker.is_tradeable.side_effect = lambda s: tradeable
        self.broker.map_symbol.side_effect = lambda s: {"symbol": s, "type": "stock"}
        self.copier._park_idle_cash(targets)
        return targets

    def test_idle_cash_parked_in_proxy_minus_buffer(self):
        targets = self._park({"QQQ": {"weight": 0.6, "type": "stock"}})
        self.assertAlmostEqual(targets["SHV"]["weight"], 0.38)
        self.assertAlmostEqual(targets["QQQ"]["weight"], 0.6)

    def test_proxy_already_held_by_a_bot_is_topped_up_not_replaced(self):
        targets = self._park({"QQQ": {"weight": 0.5, "type": "stock"}, "SHV": {"weight": 0.1, "type": "stock"}})
        self.assertAlmostEqual(targets["SHV"]["weight"], 0.1 + 0.38)

    def test_fully_invested_book_parks_nothing(self):
        targets = self._park({"QQQ": {"weight": 0.99, "type": "stock"}})
        self.assertNotIn("SHV", targets)

    def test_untradeable_or_disabled_proxy_leaves_cash(self):
        self.assertNotIn("SHV", self._park({"QQQ": {"weight": 0.5, "type": "stock"}}, tradeable=False))
        self.assertEqual(
            self._park({"QQQ": {"weight": 0.5, "type": "stock"}}, proxy=None), {"QQQ": {"weight": 0.5, "type": "stock"}}
        )

    def test_cash_proxy_comes_from_broker_and_env_overrides(self):
        self.broker.cash_proxy = "SHV"
        with patch.dict("os.environ"):
            os.environ.pop("LIVETRADE_CASH_PROXY", None)
            self.assertEqual(LiveTradeCopier(broker=self.broker, bot_weights={}).cash_proxy, "SHV")
        self.broker.cash_proxy = None
        self.assertIsNone(LiveTradeCopier(broker=self.broker, bot_weights={}).cash_proxy)
        for value, expected in (("BIL", "BIL"), ("none", None), ("", None)):
            with patch.dict("os.environ", {"LIVETRADE_CASH_PROXY": value}):
                self.assertEqual(LiveTradeCopier(broker=self.broker, bot_weights={}).cash_proxy, expected)

    def test_venues_without_us_etfs_default_to_plain_cash(self):
        from tradingbot.livetrade.collective2 import Collective2Broker
        from tradingbot.livetrade.darwinex import DarwinexBroker
        from tradingbot.livetrade.hyperliquid import HyperliquidBroker

        self.assertEqual(Collective2Broker.cash_proxy, "SHV")
        self.assertIsNone(DarwinexBroker.cash_proxy)
        self.assertIsNone(HyperliquidBroker.cash_proxy)

    def test_translated_index_ticker_still_maps(self):
        """Regression: ^GSPC -> SPX loses the caret and must keep working."""
        self.copier.strict_mapping = True

        mock_bot = MagicMock(spec=Bot)
        mock_bot.portfolio = {"USD": 0, "^GSPC": 10}
        self.bot_repo.create_or_get_bot.return_value = mock_bot
        self.data_service.get_latest_prices_batch.return_value = {"^GSPC": 100.0}
        self.broker.map_symbol.return_value = {"symbol": "SPX", "type": "index"}
        self.broker.get_total_equity.return_value = 1000.0
        self.broker.get_positions.return_value = {}

        self.copier.sync()  # must not abort

        orders = self.copier._calculate_orders({"SPX": {"weight": 1.0, "type": "index"}}, {}, 1000.0)
        self.assertEqual(orders[0]["symbol"], "SPX")

    def test_calculate_orders_skips_zero_price(self):
        target_weights = {"AAPL": {"weight": 0.5, "type": "stock"}}
        current_positions = {"AAPL": 0.0}
        total_equity = 2000

        # Mock price as 0
        self.broker.get_latest_price.return_value = 0.0

        orders = self.copier._calculate_orders(target_weights, current_positions, total_equity)
        self.assertEqual(len(orders), 0)

    def test_execute_orders_ordering_and_delay(self):
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
