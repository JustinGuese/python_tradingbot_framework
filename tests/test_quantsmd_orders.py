import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.quantsmd_orders import (
    bracket_exit,
    fill_limit_buy,
    fill_limit_sell,
    fill_stop_buy,
    fill_stop_limit_buy,
    fill_stop_limit_sell,
    fill_stop_sell,
    ohlc_frames,
)


# fill_limit_buy(o, l, limit): fills if l <= limit, at min(o, limit)
def test_limit_buy_fills_at_limit_when_low_touches():
    assert fill_limit_buy(101.0, 99.0, 100.0) == pytest.approx(100.0)


def test_limit_buy_no_fill_when_low_above_limit():
    assert fill_limit_buy(101.0, 100.5, 100.0) is None


def test_limit_buy_gap_down_fills_at_open():
    assert fill_limit_buy(95.0, 94.0, 100.0) == pytest.approx(95.0)


# fill_limit_sell(o, h, limit): fills if h >= limit, at max(o, limit)
def test_limit_sell_fills_at_limit_when_high_touches():
    assert fill_limit_sell(99.0, 101.0, 100.0) == pytest.approx(100.0)


def test_limit_sell_no_fill_when_high_below_limit():
    assert fill_limit_sell(99.0, 99.5, 100.0) is None


def test_limit_sell_gap_up_fills_at_open():
    assert fill_limit_sell(105.0, 106.0, 100.0) == pytest.approx(105.0)


# fill_stop_buy(o, h, stop): fills if h >= stop, at max(o, stop)
def test_stop_buy_fills_at_stop_when_high_touches():
    assert fill_stop_buy(99.0, 101.0, 100.0) == pytest.approx(100.0)


def test_stop_buy_no_fill_when_high_below_stop():
    assert fill_stop_buy(99.0, 99.5, 100.0) is None


def test_stop_buy_gap_up_fills_at_open():
    assert fill_stop_buy(104.0, 105.0, 100.0) == pytest.approx(104.0)


# fill_stop_sell(o, l, stop): fills if l <= stop, at min(o, stop)
def test_stop_sell_fills_at_stop_when_low_touches():
    assert fill_stop_sell(101.0, 99.0, 100.0) == pytest.approx(100.0)


def test_stop_sell_no_fill_when_low_above_stop():
    assert fill_stop_sell(101.0, 100.5, 100.0) is None


def test_stop_sell_gap_down_fills_at_open():
    assert fill_stop_sell(95.0, 94.0, 100.0) == pytest.approx(95.0)


# fill_stop_limit_buy(o, h, l, stop, limit) -> (fill, triggered)
def test_stop_limit_buy_triggered_and_filled():
    # stop 100, limit 101, open 99.5 -> p = max(99.5, 100) = 100 <= 101 -> fill at 100
    fill, triggered = fill_stop_limit_buy(99.5, 101.0, 99.0, 100.0, 101.0)
    assert triggered is True
    assert fill == pytest.approx(100.0)


def test_stop_limit_buy_triggered_not_filled():
    # stop 100, limit 101, open 102 -> p = 102 > 101; low 101.5 > 101 -> live, no fill
    fill, triggered = fill_stop_limit_buy(102.0, 103.0, 101.5, 100.0, 101.0)
    assert triggered is True
    assert fill is None


def test_stop_limit_buy_triggered_then_limit_fills_inside_bar():
    # open above the limit, but the bar trades down to the limit after triggering
    fill, triggered = fill_stop_limit_buy(102.0, 103.0, 100.5, 100.0, 101.0)
    assert triggered is True
    assert fill == pytest.approx(101.0)


def test_stop_limit_buy_not_triggered():
    fill, triggered = fill_stop_limit_buy(98.0, 99.0, 97.0, 100.0, 101.0)
    assert fill is None
    assert triggered is False


# fill_stop_limit_sell(o, h, l, stop, limit) -> (fill, triggered)
def test_stop_limit_sell_triggered_and_filled():
    # stop 100, limit 99, open 100.5 -> p = min(100.5, 100) = 100 >= 99 -> fill at 100
    fill, triggered = fill_stop_limit_sell(100.5, 101.0, 99.5, 100.0, 99.0)
    assert triggered is True
    assert fill == pytest.approx(100.0)


def test_stop_limit_sell_triggered_not_filled():
    # stop 100, limit 99, open 98 -> p = 98 < 99; high 98.5 < 99 -> live, no fill
    fill, triggered = fill_stop_limit_sell(98.0, 98.5, 97.0, 100.0, 99.0)
    assert triggered is True
    assert fill is None


def test_stop_limit_sell_triggered_then_limit_fills_inside_bar():
    # open 98 below limit, bar rallies to limit after triggering
    fill, triggered = fill_stop_limit_sell(98.0, 99.5, 97.0, 100.0, 99.0)
    assert triggered is True
    assert fill == pytest.approx(99.0)


def test_stop_limit_sell_not_triggered():
    fill, triggered = fill_stop_limit_sell(102.0, 103.0, 101.0, 100.0, 99.0)
    assert fill is None
    assert triggered is False


# bracket_exit(o, h, l, take_profit, stop_loss)
def test_bracket_stop_wins_tie():
    # both the stop (95) and the target (105) are touched; stop wins
    assert bracket_exit(100.0, 106.0, 94.0, 105.0, 95.0) == (
        pytest.approx(95.0),
        "stop",
    )


def test_bracket_target_only():
    assert bracket_exit(100.0, 106.0, 99.0, 105.0, 95.0) == (
        pytest.approx(105.0),
        "target",
    )


def test_bracket_neither():
    assert bracket_exit(100.0, 104.0, 96.0, 105.0, 95.0) is None


def test_bracket_gap_through_stop_fills_at_open():
    assert bracket_exit(93.0, 94.0, 92.0, 105.0, 95.0) == (
        pytest.approx(93.0),
        "stop",
    )


def test_bracket_gap_through_target_fills_at_open():
    assert bracket_exit(108.0, 109.0, 107.0, 105.0, 95.0) == (
        pytest.approx(108.0),
        "target",
    )


# ohlc_frames(datas, tickers)
def _bars(opens, highs, lows, closes, index):
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=index,
    )


def test_ohlc_frames_uses_timestamp_column():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.2, 2.2],
            "volume": [10, 20],
        }
    )
    out = ohlc_frames({"AAA": frame}, ["AAA"])
    assert list(out) == ["AAA"]
    assert list(out["AAA"].columns) == ["open", "high", "low", "close"]
    assert list(out["AAA"].index) == list(pd.to_datetime(["2024-01-02", "2024-01-03"]))
    assert out["AAA"]["close"].tolist() == [1.2, 2.2]


def test_ohlc_frames_tz_aware_index_becomes_naive():
    index = pd.date_range("2024-01-02 14:30", periods=3, freq="1h", tz="UTC")
    frame = _bars([1.0, 1.0, 1.0], [2.0, 2.0, 2.0], [0.5, 0.5, 0.5], [1.5, 1.5, 1.5], index)
    out = ohlc_frames({"AAA": frame}, ["AAA"])
    assert out["AAA"].index.tz is None
    assert out["AAA"].index[0] == pd.Timestamp("2024-01-02 14:30")
    assert len(out["AAA"]) == 3


def test_ohlc_frames_omits_missing_ticker():
    index = pd.date_range("2024-01-02", periods=2, freq="D")
    frame = _bars([1.0, 1.0], [2.0, 2.0], [0.5, 0.5], [1.5, 1.5], index)
    out = ohlc_frames({"AAA": frame}, ["AAA", "BBB"])
    assert list(out) == ["AAA"]


def test_ohlc_frames_drops_nan_rows_and_omits_empty():
    index = pd.date_range("2024-01-02", periods=3, freq="D")
    frame = _bars(
        [1.0, np.nan, 1.0],
        [2.0, 2.0, 2.0],
        [0.5, 0.5, 0.5],
        [1.5, 1.5, 1.5],
        index,
    )
    out = ohlc_frames({"AAA": frame, "BBB": frame.iloc[:0]}, ["AAA", "BBB"])
    assert list(out) == ["AAA"]
    assert len(out["AAA"]) == 2


def test_ohlc_frames_duplicate_timestamps_keep_last():
    index = pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"])
    frame = _bars([1.0, 9.0, 1.0], [2.0, 2.0, 2.0], [0.5, 0.5, 0.5], [1.5, 7.7, 1.5], index)
    out = ohlc_frames({"AAA": frame}, ["AAA"])
    assert len(out["AAA"]) == 2
    assert out["AAA"].loc[pd.Timestamp("2024-01-02"), "close"] == pytest.approx(7.7)
    assert out["AAA"].loc[pd.Timestamp("2024-01-02"), "open"] == pytest.approx(9.0)
