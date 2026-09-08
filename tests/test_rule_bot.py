"""
`RuleBot` must be indistinguishable from a hand-written bot.

That is the whole premise of the strategy builder: a spec assembled in the app
runs through the same backtester and the same live path as the 24 Python bots, so
a backtest genuinely describes what will later execute. These tests pin the two
places that could break it — the decision output, and the crossover enrichment
that `decisionFunction`'s single-row signature cannot do on its own.
"""

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.backtest import backtest_bot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.indicators import safe_get
from tradingbot.utils.rule_bot import RuleBot
from tradingbot.utils.strategy_spec import PREV_PREFIX, SpecError

BARS = 120


def _spec(entry, exit_, tickers=("AAA",), interval="1d", period="2y"):
    return {
        "version": 1,
        "tickers": list(tickers),
        "interval": interval,
        "period": period,
        "entry": entry,
        "exit": exit_,
    }


RSI_SPEC = _spec(
    entry={"match": "all", "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": "<", "right": {"const": 30}}]},
    exit_={"match": "all", "conditions": [{"left": {"indicator": "momentum_rsi"}, "op": ">", "right": {"const": 70}}]},
)


def _frame(bars=BARS, seed=7):
    """OHLCV plus an oscillating RSI, shaped like getYFDataWithTA output."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.0, bars))
    # A sine sweep guarantees the 30/70 thresholds are crossed many times, so the
    # backtest actually trades rather than trivially matching at zero trades.
    rsi = 50 + 35 * np.sin(np.linspace(0, 8 * np.pi, bars))
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=bars, freq="D", tz="UTC"),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": rng.uniform(1e5, 2e5, bars),
            "trend_adx": np.full(bars, 25.0),
            "momentum_rsi": rsi,
        }
    )


class _HandWrittenRSI(Bot):
    """The bot a developer would write by hand for RSI_SPEC."""

    def __init__(self):
        self.bot_name = "HandWritten"
        self.tickers = ["AAA"]
        self.symbol = "AAA"
        self.benchmark_tickers = []
        self.interval = "1d"
        self.period = "2y"
        self.params = {}
        self.data = None
        self.datas = {}
        self.datasettings = (None, None)

    def decisionFunction(self, row):
        rsi = safe_get(row, "momentum_rsi", default=float("nan"), check_finite=True)
        if rsi != rsi:  # NaN
            return 0
        if rsi < 30:
            return 1
        if rsi > 70:
            return -1
        return 0


def _run(bot, data, **kwargs):
    return backtest_bot(
        bot,
        initial_capital=10000.0,
        data=data,
        save_to_db=False,
        save_results_to_db=False,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
#  Construction                                                                #
# --------------------------------------------------------------------------- #


def test_detached_construction_touches_no_database():
    """
    POSTGRES_URI points nowhere in tests, so any DB work would raise. Bot.__init__
    runs DDL and inserts a portfolio row; the backtest endpoint must do neither.
    """
    bot = RuleBot(RSI_SPEC, "us_test", attach_db=False)

    assert bot.bot_name == "us_test"
    assert bot.tickers == ["AAA"]
    assert bot.symbol == "AAA"
    assert bot.interval == "1d"
    assert bot.period == "2y"
    assert bot.attached is False
    # No portfolio row exists, so reaching for one must fail loudly and by name.
    assert not hasattr(bot, "dbBot")


def test_a_detached_bot_refuses_to_run():
    """Backtesting is safe without a portfolio row; trading is not."""
    bot = RuleBot(RSI_SPEC, "us_test", attach_db=False)
    with pytest.raises(RuntimeError, match="attach_db=False"):
        bot.run()


def test_universe_comes_from_the_spec():
    bot = RuleBot(_spec(RSI_SPEC["entry"], RSI_SPEC["exit"], tickers=("AAA", "BBB")), "us_x", attach_db=False)
    assert bot.tickers == ["AAA", "BBB"]
    # Multi-ticker bots carry no single symbol, matching Bot.__init__.
    assert bot.symbol is None


def test_a_raw_dict_is_validated_at_construction():
    bad = _spec(entry={"match": "all", "conditions": []}, exit_=RSI_SPEC["exit"])
    with pytest.raises(SpecError):
        RuleBot(bad, "us_bad", attach_db=False)


def test_backtest_accepts_a_rule_bot():
    """backtest_bot refuses bots that don't override decisionFunction."""
    bot = RuleBot(RSI_SPEC, "us_test", attach_db=False)
    assert type(bot).decisionFunction is not Bot.decisionFunction


# --------------------------------------------------------------------------- #
#  Parity with a hand-written bot                                              #
# --------------------------------------------------------------------------- #


def test_rule_bot_matches_a_hand_written_bot_exactly():
    data = _frame()

    hand = _run(_HandWrittenRSI(), data.copy(), return_series=True)
    rule = _run(RuleBot(RSI_SPEC, "us_rsi", attach_db=False), data.copy(), return_series=True)

    assert rule["nrtrades"] == hand["nrtrades"]
    assert rule["nrtrades"] > 4, "the fixture must actually trade or this proves nothing"

    for key in ("yearly_return", "sharpe_ratio", "maxdrawdown", "buy_hold_return", "win_rate"):
        assert rule[key] == pytest.approx(hand[key]), key

    assert rule["trades"] == hand["trades"]
    assert [p["v"] for p in rule["equity_curve"]] == pytest.approx([p["v"] for p in hand["equity_curve"]])


# --------------------------------------------------------------------------- #
#  Crossover enrichment                                                        #
# --------------------------------------------------------------------------- #


CROSS_SPEC = _spec(
    entry={
        "match": "all",
        "conditions": [
            {"left": {"indicator": "momentum_rsi"}, "op": "crosses_above", "right": {"const": 30}},
        ],
    },
    exit_={
        "match": "all",
        "conditions": [
            {"left": {"indicator": "trend_ema_fast"}, "op": "crosses_below", "right": {"indicator": "trend_ema_slow"}},
        ],
    },
)


def _patch_base_fetch(mocker, frame):
    """Make Bot.getYFDataWithTA return a fixed frame, so no network or DB is touched."""
    return mocker.patch.object(Bot, "getYFDataWithTA", return_value=frame)


def test_previous_bar_columns_are_added_for_crossovers(mocker):
    frame = _frame()
    frame["trend_ema_fast"] = frame["close"]
    frame["trend_ema_slow"] = frame["close"].rolling(5, min_periods=1).mean()
    _patch_base_fetch(mocker, frame)

    bot = RuleBot(CROSS_SPEC, "us_cross", attach_db=False)
    enriched = bot.getYFDataWithTA(symbol="AAA", interval="1d", period="2y")

    for column in ("momentum_rsi", "trend_ema_fast", "trend_ema_slow"):
        assert PREV_PREFIX + column in enriched.columns
    # The shift must be a true one-bar lag, and undefined on the first bar.
    assert pd.isna(enriched[PREV_PREFIX + "momentum_rsi"].iloc[0])
    assert enriched[PREV_PREFIX + "momentum_rsi"].iloc[1] == pytest.approx(enriched["momentum_rsi"].iloc[0])


def test_only_crossover_columns_get_a_previous_copy(mocker):
    """Shifting all 86 TA columns on every fetch would be pure waste."""
    _patch_base_fetch(mocker, _frame())

    bot = RuleBot(RSI_SPEC, "us_rsi", attach_db=False)
    enriched = bot.getYFDataWithTA(symbol="AAA", interval="1d", period="2y")

    assert not [c for c in enriched.columns if c.startswith(PREV_PREFIX)]


def test_missing_crossover_column_warns_and_stays_inert(mocker, caplog):
    """A spec referencing a column the data lacks must not crash a live run."""
    frame = _frame().drop(columns=["momentum_rsi"])
    frame["trend_ema_fast"] = frame["close"]
    frame["trend_ema_slow"] = frame["close"]
    _patch_base_fetch(mocker, frame)

    bot = RuleBot(CROSS_SPEC, "us_cross", attach_db=False)
    with caplog.at_level("WARNING"):
        enriched = bot.getYFDataWithTA(symbol="AAA", interval="1d", period="2y")

    assert "momentum_rsi" in caplog.text
    assert PREV_PREFIX + "trend_ema_fast" in enriched.columns
    # Entry can never fire, so the strategy holds cash rather than erroring.
    assert bot.decisionFunction(enriched.iloc[5]) in (0, -1)


def test_crossover_strategy_trades_through_the_backtester(mocker):
    """End to end: enrichment happens in the fetch, so the backtest sees the lag."""
    frame = _frame()
    frame["trend_ema_fast"] = frame["close"]
    frame["trend_ema_slow"] = frame["close"].rolling(10, min_periods=1).mean()
    _patch_base_fetch(mocker, frame)

    bot = RuleBot(CROSS_SPEC, "us_cross", attach_db=False)
    enriched = bot.getYFDataWithTA(symbol="AAA", interval="1d", period="2y")
    result = _run(bot, enriched, return_series=True)

    assert result["nrtrades"] > 0
    assert len(result["trades"]) == result["nrtrades"]


def test_enriched_frame_replaces_the_base_class_cache(mocker):
    """
    self.data must carry the __prev_ columns too. A decisionFunction that reads
    self.data alongside its row would otherwise see two different schemas.
    """
    frame = _frame()
    frame["trend_ema_fast"] = frame["close"]
    frame["trend_ema_slow"] = frame["close"]

    def _fake(self, **kwargs):
        self.data = frame
        self.datas["AAA"] = frame
        return frame

    mocker.patch.object(Bot, "getYFDataWithTA", _fake)

    bot = RuleBot(CROSS_SPEC, "us_cross", attach_db=False)
    bot.getYFDataWithTA(symbol="AAA", interval="1d", period="2y")

    assert PREV_PREFIX + "momentum_rsi" in bot.data.columns
    assert PREV_PREFIX + "momentum_rsi" in bot.datas["AAA"].columns


# --------------------------------------------------------------------------- #
#  Multi-ticker                                                                #
# --------------------------------------------------------------------------- #


def test_multi_ticker_rule_bot_backtests():
    spec = _spec(RSI_SPEC["entry"], RSI_SPEC["exit"], tickers=("AAA", "BBB"))
    bot = RuleBot(spec, "us_multi", attach_db=False)
    data = {"AAA": _frame(seed=1), "BBB": _frame(seed=2)}

    result = _run(bot, data, return_series=True)

    assert result["nrtrades"] > 0
    assert {t["symbol"] for t in result["trades"]} <= {"AAA", "BBB"}
