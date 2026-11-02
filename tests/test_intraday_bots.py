"""IntradayRulesBot: replay-and-reconcile behaviour, with Bot.__init__ bypassed."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradingbot.openrangebreakoutbot import OpenRangeBreakoutBot
from tradingbot.utils.orb_rules import ORBRules
from tradingbot.vwappullbackbot import VWAPPullbackBot

START = pd.Timestamp("2026-01-14 14:30", tz="UTC")  # 09:30 EST


def bars(n, spec=None):
    spec = spec or {}
    rows = []
    for i in range(n):
        h, lo, c = spec.get(i, (100.1, 99.9, 100.0) if i < 3 else (100.05, 99.95, 100.0))
        rows.append(
            {
                "timestamp": START + pd.Timedelta(minutes=5 * i),
                "open": c,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


BREAK = {4: (100.6, 100.0, 100.5)}  # ORB entry at the close of bar 4 (10:00-10:05 NY)


def make(cls, usd, qqq):
    bot = cls.__new__(cls)
    bot.bot_name = cls.__name__
    bot.symbol = "QQQ"
    bot.interval, bot.period = "5m", "5d"
    bot.rules = ORBRules(or_minutes=15, window_minutes=90, target_r=0.5, max_stop_pct=0.01)
    bot._bot_repository = SimpleNamespace(read_portfolio=lambda name: {"USD": usd, "QQQ": qqq})
    bot.buy, bot.sell = MagicMock(), MagicMock()
    return bot


def at(minutes_after_open):
    return (START + pd.Timedelta(minutes=minutes_after_open)).to_pydatetime()


def test_signal_buys_once_when_flat():
    bot = make(OpenRangeBreakoutBot, 10_000, 0)
    assert bot.decide(bars(6, BREAK), at(30), holding=False, has_cash=True) == 1


def test_already_holding_does_nothing():
    bot = make(OpenRangeBreakoutBot, 0, 50)
    assert bot.decide(bars(6, BREAK), at(30), holding=True, has_cash=False) == 0


def test_forming_bar_is_ignored():
    # at 25 min the bar starting at 25 is still forming; the break (bar 4, ends 25) is complete
    bot = make(OpenRangeBreakoutBot, 10_000, 0)
    assert bot.decide(bars(6, BREAK), at(25), holding=False, has_cash=True) == 1
    assert bot.decide(bars(6, {5: (100.6, 100.0, 100.5)}), at(25), holding=False, has_cash=True) == 0


def test_stopped_out_sells_and_does_not_reenter():
    spec = {**BREAK, 5: (100.5, 99.5, 99.8)}
    bot = make(OpenRangeBreakoutBot, 0, 50)
    assert bot.decide(bars(7, spec), at(35), holding=True, has_cash=False) == -1
    assert bot.decide(bars(7, spec), at(35), holding=False, has_cash=True) == 0


def test_stale_signal_not_chased():
    bot = make(OpenRangeBreakoutBot, 10_000, 0)
    assert bot.decide(bars(8, BREAK), at(40), holding=False, has_cash=True) == 0


def test_stale_feed_trades_nothing():
    bot = make(OpenRangeBreakoutBot, 0, 50)
    assert bot.decide(bars(6, BREAK), at(90), holding=True, has_cash=False) == 0


def test_window_end_sells():
    bot = make(OpenRangeBreakoutBot, 0, 50)
    assert bot.decide(bars(18, BREAK), at(90), holding=True, has_cash=False) == -1


def test_no_data_or_closed_market_does_nothing():
    bot = make(OpenRangeBreakoutBot, 10_000, 0)
    assert bot.decide(pd.DataFrame(), at(30), holding=False, has_cash=True) == 0
    assert bot.decide(bars(6, BREAK), datetime(2026, 1, 17, 16, 0, tzinfo=UTC), holding=False, has_cash=True) == 0


@pytest.mark.parametrize("cls", [OpenRangeBreakoutBot, VWAPPullbackBot])
def test_makeoneiteration_executes_decision(cls, monkeypatch):
    bot = make(cls, 10_000, 0)
    bot.getYFData = MagicMock(return_value=bars(6, BREAK))
    bot.decide = MagicMock(return_value=1)
    assert bot.makeOneIteration() == 1
    bot.buy.assert_called_once_with("QQQ")
    bot.decide = MagicMock(return_value=-1)
    assert bot.makeOneIteration() == -1
    bot.sell.assert_called_once_with("QQQ")


def test_bot_names_and_param_grids():
    assert VWAPPullbackBot.param_grid and OpenRangeBreakoutBot.param_grid
