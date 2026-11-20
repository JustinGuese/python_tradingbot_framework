"""
Tests for opt-in carry accrual (PLAN-carryblend.md S1).

Covers:
1. Bot class defaults:
   - CARRY_ACCRUAL is False by default on class Bot and all existing bots.
   - carry_rates(ts) returns {} by default.
2. Backtest parity:
   - When CARRY_ACCRUAL is False, carry_rates is ignored and results are identical.
   - Backtest results for existing bots (TSMOMTrendBot, BuybackHedgedBot, NeutralStockBot)
     remain unchanged with CARRY_ACCRUAL=False.
3. Accurate accrual in backtest:
   - When CARRY_ACCRUAL is True, a toy long/short pair earns exactly the carry differential.
   - Positive holding with positive rate credits cash.
   - Negative holding with positive rate debits cash.
   - Negative rates flip sign accordingly.
   - Collateral/idle cash ("USD" in rates) earns the cash rate.
4. Accurate accrual in paper trading (Bot.run / _accrue_carry):
   - Credits accrued carry since last run: holding * price * rate * days_elapsed / 365.0.
   - Updates portfolio["USD"] in the database and logs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradingbot.buybackhedgedbot import BuybackHedgedBot
from tradingbot.neutralstockbot import NeutralStockBot
from tradingbot.tsmomtrendbot import TSMOMTrendBot
from tradingbot.utils.backtest import backtest_bot
from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.botclass import Bot
from tradingbot.utils.config import ShortConfig
from tradingbot.utils.db import Bot as BotModel
from tradingbot.utils.db import RunLog


def _make_flat_frame(prices: list[float] | float, periods: int = 40, freq: str = "D") -> pd.DataFrame:
    """Create OHLCV DataFrame with timestamp index and constant/specified prices."""
    if isinstance(prices, (int, float)):
        price_list = [float(prices)] * periods
    else:
        price_list = [float(p) for p in prices]
        periods = len(price_list)

    idx = pd.date_range("2026-01-01", periods=periods, freq=freq)
    return pd.DataFrame(
        {
            "open": price_list,
            "high": price_list,
            "low": price_list,
            "close": price_list,
            "volume": [1000.0] * periods,
            "trend_adx": [25.0] * periods,
        },
        index=idx,
    )


class _ToyCarryBot(Bot):
    """Toy bot for testing carry accrual in backtest without touching DB."""

    ALLOW_SHORT = True
    MAX_GROSS = 2.0
    SHORTABLE = None
    CARRY_ACCRUAL = True

    def __init__(self, tickers: list[str], weights: dict[str, float], rates: dict[str, float]):
        self.bot_name = "ToyCarryBot"
        self.tickers = list(tickers)
        self.benchmark_tickers = []
        self.SHORTABLE = tuple(tickers)
        self.symbol = None
        self.interval = "1d"
        self.period = "1y"
        self.datas = {}
        self.data = None
        self.params = {}
        self.LIQUIDATE_UNTRACKED = False
        self._weights = dict(weights)
        self._rates = dict(rates)
        self.rebalance_count = 0

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        self.rebalance_count += 1
        # Rebalance once on first bar, then hold
        return dict(self._weights) if self.rebalance_count == 1 else None

    def carry_rates(self, ts: Any = None) -> dict[str, float]:
        return dict(self._rates)


class TestCarryAccrualDefaults:
    def test_bot_defaults(self):
        """Verify CARRY_ACCRUAL is False and carry_rates returns {} by default."""
        assert Bot.CARRY_ACCRUAL is False
        bot = Bot.__new__(Bot)
        assert bot.carry_rates(datetime.now(UTC)) == {}

    def test_existing_bots_have_carry_accrual_false(self):
        """TSMOMTrendBot, BuybackHedgedBot, NeutralStockBot have CARRY_ACCRUAL == False."""
        assert getattr(TSMOMTrendBot, "CARRY_ACCRUAL", False) is False
        assert getattr(BuybackHedgedBot, "CARRY_ACCRUAL", False) is False
        assert getattr(NeutralStockBot, "CARRY_ACCRUAL", False) is False


class TestBacktestFlagFalseParity:
    def test_flag_false_ignores_carry_rates(self, monkeypatch):
        """With CARRY_ACCRUAL=False, carry_rates is never called and output is identical."""
        tickers = ["SYM_A", "SYM_B"]
        data = {t: _make_flat_frame(100.0, periods=50) for t in tickers}

        bot_off = _ToyCarryBot(tickers, {"SYM_A": 0.5, "SYM_B": -0.5}, {"SYM_A": 0.10, "SYM_B": 0.01})
        bot_off.CARRY_ACCRUAL = False

        # Zero out borrow rate for clean comparison
        zero_borrow = ShortConfig(borrow_rate_pct=0.0, maint_pct=0.3, init_pct=0.5, borrow_overrides={})
        monkeypatch.setattr("tradingbot.utils.backtest.SHORT_CONFIG", zero_borrow)

        res_off = backtest_bot(
            bot_off,
            initial_capital=10000.0,
            data=data,
            save_to_db=False,
            save_results_to_db=False,
            record_trial=False,
            slippage_pct=0.0,
            commission_pct=0.0,
            return_series=True,
        )

        # Constant price and zero borrow => yearly return exactly 0.0
        assert res_off["yearly_return"] == pytest.approx(0.0, abs=1e-12)
        # Final portfolio value exactly initial capital
        final_val = res_off["equity_curve"][-1]["v"]
        assert final_val == pytest.approx(10000.0, abs=1e-8)

    def test_existing_bots_backtest_unchanged_with_flag_false(self, monkeypatch):
        """Existing bots with CARRY_ACCRUAL=False run normally and do not accrue carry."""
        tickers = ["SPY", "QQQ", "SHV"]
        data = {t: _make_flat_frame(100.0, periods=30) for t in tickers}
        zero_borrow = ShortConfig(borrow_rate_pct=0.0, maint_pct=0.3, init_pct=0.5, borrow_overrides={})
        monkeypatch.setattr("tradingbot.utils.backtest.SHORT_CONFIG", zero_borrow)

        bot = NeutralStockBot.__new__(NeutralStockBot)
        bot.bot_name = "NeutralStockBot"
        bot.tickers = tickers
        bot.benchmark_tickers = []
        bot.symbol = None
        bot.interval = "1d"
        bot.period = "1y"
        bot.datas = {}
        bot.data = None
        bot.params = {}
        bot.LIQUIDATE_UNTRACKED = False
        bot.target_long = 0.5
        bot.target_short = 0.5
        bot.k = 1
        bot.park_cash = False
        bot.CASH_PROXY = "SHV"
        bot.rebalance_called = False

        def mock_target_weights(rows):
            if not bot.rebalance_called:
                bot.rebalance_called = True
                return {"SPY": 0.5, "QQQ": -0.5}
            return None

        bot.targetWeights = mock_target_weights

        assert getattr(bot, "CARRY_ACCRUAL", False) is False

        res = backtest_bot(
            bot,
            initial_capital=10000.0,
            data=data,
            save_to_db=False,
            save_results_to_db=False,
            record_trial=False,
            slippage_pct=0.0,
            commission_pct=0.0,
        )
        # Flat price, 0 borrow fee, CARRY_ACCRUAL False => exactly 0 return
        assert res["yearly_return"] == pytest.approx(0.0, abs=1e-12)


class TestBacktestCarryAccrualMath:
    def test_toy_long_short_pair_earns_exact_differential(self, monkeypatch):
        """
        Long A (+50%), Short B (-50%).
        Prices flat at 100.0 over 253 bars (252 bar steps after initial entry).
        rate A = +0.05, rate B = +0.02.
        Differential = +0.03 (3% per year).
        Daily accrual on $5,000 notional:
            5000 * 0.05 / 252 - 5000 * 0.02 / 252 = 5000 * 0.03 / 252 = 150 / 252.
        Over 252 bars holding:
            252 * (150 / 252) = $150.00 exact carry.
        Total return on $10,000 portfolio = 150 / 10000 = 0.015 (1.5%).
        """
        periods = 253  # 1 entry bar + 252 accrual bars
        tickers = ["EURUSD", "USDJPY"]
        data = {t: _make_flat_frame(100.0, periods=periods) for t in tickers}

        zero_borrow = ShortConfig(borrow_rate_pct=0.0, maint_pct=0.3, init_pct=0.5, borrow_overrides={})
        monkeypatch.setattr("tradingbot.utils.backtest.SHORT_CONFIG", zero_borrow)

        rate_a = 0.05
        rate_b = 0.02
        bot = _ToyCarryBot(
            tickers,
            {"EURUSD": 0.5, "USDJPY": -0.5},
            {"EURUSD": rate_a, "USDJPY": rate_b},
        )
        bot.CARRY_ACCRUAL = True

        res = backtest_bot(
            bot,
            initial_capital=10000.0,
            data=data,
            save_to_db=False,
            save_results_to_db=False,
            record_trial=False,
            slippage_pct=0.0,
            commission_pct=0.0,
            return_series=True,
        )

        # 252 holding bars * (5000 * 0.03 / 252) = exactly 150.00
        expected_carry = 252 * (5000.0 * (rate_a - rate_b) / 252.0)
        expected_final_equity = 10000.0 + expected_carry
        actual_final_equity = res["equity_curve"][-1]["v"]

        assert actual_final_equity == pytest.approx(expected_final_equity, rel=1e-6)
        assert res["yearly_return"] == pytest.approx(expected_carry / 10000.0, rel=1e-6)

    def test_idle_cash_usd_carry_accrual(self):
        """Collateral / idle cash rate for 'USD' credits cash * rate_usd / periods_per_year."""
        periods = 253  # 1 entry bar + 252 holding bars
        tickers = ["EURUSD"]
        data = {"EURUSD": _make_flat_frame(100.0, periods=periods)}

        # 50% long EURUSD, 50% idle cash in USD
        # EURUSD rate = 0.04, USD rate = 0.05
        bot = _ToyCarryBot(
            tickers,
            {"EURUSD": 0.5},
            {"EURUSD": 0.04, "USD": 0.05},
        )
        bot.CARRY_ACCRUAL = True

        res = backtest_bot(
            bot,
            initial_capital=10000.0,
            data=data,
            save_to_db=False,
            save_results_to_db=False,
            record_trial=False,
            slippage_pct=0.0,
            commission_pct=0.0,
            return_series=True,
        )

        # Expected carry per bar:
        # Long EURUSD ($5000): 5000 * 0.04 / 252
        # USD cash ($5000): 5000 * 0.05 / 252
        assert res["yearly_return"] == pytest.approx(0.045, abs=0.002)
        assert res["yearly_return"] > 0.044

    def test_negative_rates_flip_sign(self, monkeypatch):
        """Holding with negative rate debits cash; shorting with negative rate credits cash."""
        periods = 253
        tickers = ["NEG_A", "NEG_B"]
        data = {t: _make_flat_frame(100.0, periods=periods) for t in tickers}

        zero_borrow = ShortConfig(borrow_rate_pct=0.0, maint_pct=0.3, init_pct=0.5, borrow_overrides={})
        monkeypatch.setattr("tradingbot.utils.backtest.SHORT_CONFIG", zero_borrow)

        # NEG_A has rate -0.02 (long pays carry)
        # NEG_B has rate -0.05 (short receives carry of +0.05)
        # Net differential = -0.02 - (-0.05) = +0.03
        bot = _ToyCarryBot(
            tickers,
            {"NEG_A": 0.5, "NEG_B": -0.5},
            {"NEG_A": -0.02, "NEG_B": -0.05},
        )
        bot.CARRY_ACCRUAL = True

        res = backtest_bot(
            bot,
            initial_capital=10000.0,
            data=data,
            save_to_db=False,
            save_results_to_db=False,
            record_trial=False,
            slippage_pct=0.0,
            commission_pct=0.0,
            return_series=True,
        )

        expected_carry = 252 * (5000.0 * (-0.02 - (-0.05)) / 252.0)
        actual_final_equity = res["equity_curve"][-1]["v"]
        assert actual_final_equity == pytest.approx(10000.0 + expected_carry, rel=1e-6)


class TestPaperTradingAccrueCarry:
    def test_accrue_carry_in_database(self, sqlite_db, monkeypatch):
        """
        _accrue_carry credits holding * price * rate * days_elapsed / 365.0 to portfolio['USD'].
        """
        monkeypatch.setattr("tradingbot.utils.botclass.get_db_session", sqlite_db)
        bot_name = "PaperCarryTestBot"

        # Create bot in SQLite DB
        with sqlite_db() as session:
            bot_model = BotModel(
                name=bot_name,
                portfolio={"USD": 10000.0, "EURUSD": 5000.0, "USDJPY": -5000.0},
            )
            session.add(bot_model)
            # Add previous successful run 10 days ago
            t_last = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)
            run = RunLog(bot_name=bot_name, start_time=t_last, success=True, result="test")
            session.add(run)

        # Create Bot instance without connecting to Postgres
        bot = Bot.__new__(Bot)
        bot.bot_name = bot_name
        bot.CARRY_ACCRUAL = True
        bot.tickers = ["EURUSD", "USDJPY"]
        bot._bot_repository = BotRepository
        bot.carry_rates = lambda ts=None: {"EURUSD": 0.05, "USDJPY": 0.02, "USD": 0.04}
        bot.getLatestPrice = lambda sym: 1.0

        # Run _accrue_carry at current time (10 days elapsed)
        now = datetime.now(UTC).replace(tzinfo=None)
        accrued = bot._accrue_carry(now=now)

        # Expected:
        # EURUSD: 5000 * 1.0 * 0.05 * (10 / 365) = 6.849315
        # USDJPY: -5000 * 1.0 * 0.02 * (10 / 365) = -2.739726
        # USD: 10000 * 0.04 * (10 / 365) = 10.958904
        # Total = 6.849315 - 2.739726 + 10.958904 = 15.068493
        expected = (5000 * 0.05 - 5000 * 0.02 + 10000 * 0.04) * (10.0 / 365.0)
        assert accrued == pytest.approx(expected, rel=1e-5)

        # Verify portfolio in DB was updated
        updated_portfolio = BotRepository.read_portfolio(bot_name)
        assert updated_portfolio["USD"] == pytest.approx(10000.0 + expected, rel=1e-5)
        assert updated_portfolio["EURUSD"] == 5000.0
        assert updated_portfolio["USDJPY"] == -5000.0

    def test_bot_run_triggers_carry_accrual(self, sqlite_db, monkeypatch):
        """Bot.run() executes _accrue_carry when CARRY_ACCRUAL is True."""
        monkeypatch.setattr("tradingbot.utils.botclass.get_db_session", sqlite_db)
        bot_name = "RunCarryBot"

        with sqlite_db() as session:
            bot_model = BotModel(
                name=bot_name,
                portfolio={"USD": 10000.0, "EURUSD": 1000.0},
            )
            session.add(bot_model)
            t_last = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7.3)
            run = RunLog(bot_name=bot_name, start_time=t_last, success=True, result="test")
            session.add(run)

        bot = Bot.__new__(Bot)
        bot.bot_name = bot_name
        bot.CARRY_ACCRUAL = True
        bot.tickers = ["EURUSD"]
        bot.symbol = "EURUSD"
        bot._bot_repository = BotRepository
        bot.carry_rates = lambda ts=None: {"EURUSD": 0.05}
        bot.getLatestPrice = lambda sym: 1.20
        bot.makeOneIteration = lambda: 0

        # Mock PortfolioManager
        mock_pm = MagicMock()
        mock_pm.roll_and_settle_options = MagicMock()
        bot._portfolio_manager = mock_pm

        bot.run()

        # Expected carry: 1000 * 1.20 * 0.05 * 7.3 / 365 = 1.20
        expected_carry = 1000.0 * 1.20 * 0.05 * (7.3 / 365.0)
        updated = BotRepository.read_portfolio(bot_name)
        assert updated["USD"] == pytest.approx(10000.0 + expected_carry, rel=1e-4)

    def test_bot_run_flag_false_does_not_accrue(self, sqlite_db, monkeypatch):
        """Bot.run() does not accrue carry when CARRY_ACCRUAL is False."""
        monkeypatch.setattr("tradingbot.utils.botclass.get_db_session", sqlite_db)
        bot_name = "RunNoCarryBot"

        with sqlite_db() as session:
            bot_model = BotModel(
                name=bot_name,
                portfolio={"USD": 10000.0, "EURUSD": 1000.0},
            )
            session.add(bot_model)
            t_last = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)
            run = RunLog(bot_name=bot_name, start_time=t_last, success=True, result="test")
            session.add(run)

        bot = Bot.__new__(Bot)
        bot.bot_name = bot_name
        bot.CARRY_ACCRUAL = False
        bot.tickers = ["EURUSD"]
        bot.symbol = "EURUSD"
        bot._bot_repository = BotRepository
        bot.carry_rates = lambda ts=None: {"EURUSD": 0.10}
        bot.getLatestPrice = lambda sym: 1.0
        bot.makeOneIteration = lambda: 0

        mock_pm = MagicMock()
        mock_pm.roll_and_settle_options = MagicMock()
        bot._portfolio_manager = mock_pm

        bot.run()

        updated = BotRepository.read_portfolio(bot_name)
        assert updated["USD"] == 10000.0
