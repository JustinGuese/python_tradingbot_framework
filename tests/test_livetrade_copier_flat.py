"""A flat source bot is a real target (hold cash / park in the proxy), not a failed read."""

from unittest.mock import MagicMock

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.copier import LiveTradeCopier


def _copier(portfolio, *, positions=None, prices=None, weights=None, proxy="SHV"):
    broker = MagicMock(spec=LiveBroker)
    broker.name = "test_broker"
    broker.supports_short = False
    broker.supports_limit_orders = False
    broker.is_tradeable.return_value = True
    broker.map_symbol.side_effect = lambda s: {"symbol": s, "type": "stock"}
    broker.get_total_equity.return_value = 100_000.0
    broker.cancel_open_orders.return_value = 0
    broker.get_positions.return_value = dict(positions or {})
    broker.get_latest_price.return_value = 100.0
    broker.get_cash.return_value = 10_000.0
    copier = LiveTradeCopier(broker=broker, bot_weights=weights or {"bot1": 1.0}, dry_run=True)
    copier.cash_proxy = proxy
    copier.settle_delay_seconds = 0
    copier.bot_repo = MagicMock()
    copier.bot_repo.read_portfolio.return_value = portfolio
    copier.data_service = MagicMock()
    copier.data_service.get_latest_prices_batch.return_value = prices or {}
    copier.execution_policy = MagicMock()
    copier.execution_policy.explain_window.return_value = (True, "")
    copier.execution_policy.marketable_limit.side_effect = lambda side, p: p
    return copier, broker


def test_flat_bot_liquidates_stale_positions_and_parks_in_proxy():
    copier, _ = _copier({"USD": 9490.95}, positions={"DBC": 108.0, "USO": 23.0, "SHV": 325.0})
    captured = {}
    orig = copier._calculate_orders

    def spy(targets, positions, equity):
        captured["targets"] = targets
        captured["orders"] = orig(targets, positions, equity)
        return captured["orders"]

    copier._calculate_orders = spy
    copier.sync()
    assert copier._target_is_flat is True
    # Idle book (100% minus the 2% cash buffer) goes to the proxy; the old legs get a 0 target.
    assert captured["targets"] == {"SHV": {"weight": 0.98, "type": "stock"}}
    sells = {o["symbol"] for o in captured["orders"] if o["side"] == "SELL"}
    assert {"DBC", "USO"} <= sells
    assert any(o["symbol"] == "SHV" and o["side"] == "BUY" for o in captured["orders"])


def test_flat_bot_with_no_proxy_targets_pure_cash_and_still_reconciles():
    copier, _ = _copier({"USD": 9490.95}, positions={"DBC": 108.0}, proxy=None)
    captured = {}
    orig = copier._calculate_orders

    def spy(targets, positions, equity):
        captured["targets"], captured["positions"] = targets, positions
        return orig(targets, positions, equity)

    copier._calculate_orders = spy
    copier.sync()
    assert captured["targets"] == {}
    assert captured["positions"] == {"DBC": 108.0}


def test_price_outage_is_not_treated_as_flat():
    # Bot holds AAPL but the price lookup fails: no price means no weight, which must NOT
    # read as "flat" and liquidate the live book.
    copier, broker = _copier({"USD": 1000.0, "AAPL": 10.0}, positions={"DBC": 108.0}, prices={})
    copier.sync()
    assert copier._target_is_flat is False
    broker.get_positions.assert_not_called()


def test_zero_total_weight_is_not_flat():
    copier, broker = _copier({"USD": 1000.0}, weights={"bot1": 0.0})
    copier.sync()
    assert copier._target_is_flat is False
    broker.get_positions.assert_not_called()


def test_unreadable_bot_value_is_not_flat():
    copier, broker = _copier({"USD": 0.0})
    copier.sync()
    assert copier._target_is_flat is False
    broker.get_positions.assert_not_called()


def test_missing_bot_row_still_raises():
    copier, _ = _copier(None)
    try:
        copier.sync()
    except ValueError as e:
        assert "no row" in str(e)
    else:
        raise AssertionError("expected ValueError for a missing source bot")


def test_invested_bot_is_not_flat():
    copier, _ = _copier({"USD": 500.0, "AAPL": 10.0}, prices={"AAPL": 150.0})
    weights = copier._calculate_target_weights()
    assert copier._target_is_flat is False
    assert weights == {"AAPL": 0.75}
