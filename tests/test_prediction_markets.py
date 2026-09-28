"""Prediction markets: Kalshi/Polymarket parsing, capture idempotency, point-in-time features."""

from datetime import UTC, date, datetime

import httpx
import numpy as np
import pandas as pd
import pytest

from tradingbot.predictionmarketoverlaybot import PredictionMarketOverlayBot
from tradingbot.utils import kalshi, polymarket
from tradingbot.utils import prediction_market_capture as cap
from tradingbot.utils import prediction_market_features as pmf
from tradingbot.utils.db import PredictionMarketSnapshot
from tradingbot.utils.prediction_market_series import Series

# Verbatim shapes from the live API (2026-09-28), trimmed.
LIVE_CANDLE = {
    "end_period_ts": 1790568000,
    "open_interest_fp": "773413.67",
    "volume_fp": "14259.45",
    "price": {"open_dollars": "0.3500", "close_dollars": "0.3200", "previous_dollars": "0.3600"},
    "yes_bid": {"close_dollars": "0.3200"},
    "yes_ask": {"close_dollars": "0.3300"},
}
HISTORICAL_CANDLE = {
    "end_period_ts": 1730782800,
    "open_interest": "11143.00",
    "volume": "100.00",
    "price": {"open": "0.3400", "close": "0.3400", "previous": None},
    "yes_ask": {"close": "0.7700"},
    "yes_bid": {"close": "0.3300"},
}


def _ts(y, m, d, h=4) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp())


def test_parse_candle_reads_both_schemas():
    live = kalshi.parse_candle(LIVE_CANDLE)
    assert live["prob"] == pytest.approx(0.32)
    assert (live["bid"], live["ask"]) == (pytest.approx(0.32), pytest.approx(0.33))
    assert live["volume"] == pytest.approx(14259.45) and live["open_interest"] == pytest.approx(773413.67)
    hist = kalshi.parse_candle(HISTORICAL_CANDLE)
    assert hist["prob"] == pytest.approx(0.34)
    assert hist["open_interest"] == pytest.approx(11143.0)


def test_parse_candle_without_trade_falls_back_to_mid_then_previous():
    mid = kalshi.parse_candle({**LIVE_CANDLE, "price": {"previous_dollars": "0.3600"}})
    assert mid["prob"] == pytest.approx(0.325)
    prev = kalshi.parse_candle({"end_period_ts": 1790568000, "price": {"previous_dollars": "0.3600"}})
    assert prev["prob"] == pytest.approx(0.36)


def test_legacy_cent_fields_parse_to_none():
    """The silent-null trap: old integer-cent names are simply not read."""
    old = kalshi.parse_candle({"end_period_ts": 1, "price": {"close": None}, "yes_bid": {"close_cents": 32}})
    assert old["prob"] is None


@pytest.mark.parametrize(
    "market, expected",
    [
        ({"ticker": "KXFED-27APR-T6.00", "strike_type": "greater", "floor_strike": 6}, ("close_above", 6.0, None)),
        ({"ticker": "FED-22DEC-T4.75", "strike_type": None, "floor_strike": None}, ("close_above", 4.75, None)),
        ({"ticker": "KXCPI-26OCT-T-0.3"}, ("close_above", -0.3, None)),
        (
            {"ticker": "KXINX-X-B7737", "strike_type": "between", "floor_strike": 7725, "cap_strike": 7749.99},
            ("range", 7725.0, 7749.99),
        ),
        ({"ticker": "KXINX-X-T7050", "strike_type": "less", "cap_strike": 7050}, ("close_below", 7050.0, None)),
        ({"ticker": "KXFEDDECISION-26OCT-C25", "strike_type": "custom"}, ("event", None, None)),
        # legacy S&P tails carry the side only in the wording
        ({"ticker": "INX-22MAY03-T4000", "yes_sub_title": "3999.99 or lower"}, ("close_below", 4000.0, None)),
        ({"ticker": "FED-22DEC-T4.75", "yes_sub_title": "Above 4.75%"}, ("close_above", 4.75, None)),
        # legacy negative strikes are written "N"
        ({"ticker": "CPI-22AUG-TN0.1", "yes_sub_title": "Above -0.1%"}, ("close_above", -0.1, None)),
    ],
)
def test_market_strike(market, expected):
    assert kalshi.market_strike(market)[:3] == expected


def test_polymarket_double_encoded_fields():
    market = {
        "slug": "us-recession-in-2025",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0", "1"]',
        "clobTokenIds": '["111", "222"]',
        "closed": True,
        "umaResolutionStatus": "resolved",
        "endDate": "2026-02-28T12:00:00Z",
    }
    assert polymarket.yes_token(market) == "111"
    meta = polymarket.market_meta(market, "us-recession-in-2025")
    assert meta["result"] == "no" and meta["expiry"] == datetime(2026, 2, 28, 12)


def test_covered_date_is_the_eastern_day_before_the_candle_end():
    assert cap.covered_date(datetime(2024, 12, 19, 5, 0)) == date(2024, 12, 18)  # midnight EST
    assert cap.covered_date(datetime(2026, 9, 27, 4, 0)) == date(2026, 9, 26)  # midnight EDT
    assert cap.covered_date(datetime(2025, 1, 9, 0, 0, 3)) == date(2025, 1, 8)  # Polymarket 00:00 UTC


def _kalshi_client(markets: list[dict], candles: dict[str, list[dict]]) -> kalshi.KalshiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/historical/markets"):
            return httpx.Response(200, json={"markets": markets, "cursor": ""})
        if path.endswith("/markets"):
            return httpx.Response(200, json={"markets": [], "cursor": ""})
        ticker = path.split("/")[-2]
        return httpx.Response(200, json={"candlesticks": candles.get(ticker, [])})

    client = httpx.Client(base_url=kalshi.BASE_URL, transport=httpx.MockTransport(handler))
    return kalshi.KalshiClient(client=client, pause=0)


FED = Series("fed_rate", "kalshi", ("KXFED",), "test")
MARKET = {
    "ticker": "KXFED-26DEC-T4.00",
    "event_ticker": "KXFED-26DEC",
    "strike_type": "greater",
    "floor_strike": 4,
    "open_time": "2026-09-01T00:00:00Z",
    "close_time": "2026-12-16T19:00:00Z",
    "result": "",
}


def _candle(ts: int, close: str | None) -> dict:
    return {"end_period_ts": ts, "price": {"close_dollars": close}, "volume_fp": "1", "open_interest_fp": "1"}


def test_capture_is_idempotent_and_skips_the_partial_day(sqlite_db, db_session):
    candles = {"KXFED-26DEC-T4.00": [_candle(_ts(2026, 9, 26), "0.40"), _candle(_ts(2026, 9, 27), "0.42")]}
    now = datetime(2026, 9, 26, 12)  # the 9/27 04:00 candle is today's, still open
    kc = _kalshi_client([MARKET], candles)
    assert cap.capture_kalshi(FED, kc, now, backfill=True) == 1
    assert cap.capture_kalshi(FED, kc, now, backfill=True) == 0
    row = db_session.query(PredictionMarketSnapshot).one()
    assert (row.date, row.prob, row.contract_type, row.strike) == (date(2026, 9, 25), 0.40, "close_above", 4.0)


def test_capture_raises_when_every_price_is_null(sqlite_db):
    kc = _kalshi_client([MARKET], {"KXFED-26DEC-T4.00": [_candle(_ts(2026, 9, 26), None)]})
    with pytest.raises(cap.AllNullPrices):
        cap.capture_kalshi(FED, kc, datetime(2026, 9, 28), backfill=True)


def test_capture_records_settlement(sqlite_db, db_session):
    candles = {"KXFED-26DEC-T4.00": [_candle(_ts(2026, 9, 26), "0.40")]}
    cap.capture_kalshi(FED, _kalshi_client([MARKET], candles), datetime(2026, 9, 28), backfill=True)
    settled = {**MARKET, "result": "no"}
    cap.capture_kalshi(FED, _kalshi_client([settled], candles), datetime(2026, 9, 28), backfill=True)
    assert db_session.query(PredictionMarketSnapshot).one().result == "no"


def test_ladder_moments_discrete_fed_levels():
    # P(upper > 3.75) = 1, P(> 4.00) = 0.6, P(> 4.25) = 0.0 -> 40% at 4.00, 60% at 4.25
    mean, std = pmf.ladder_moments([3.75, 4.00, 4.25], [1.0, 0.6, 0.0], 0.25, discrete=True)
    assert mean == pytest.approx(0.4 * 4.00 + 0.6 * 4.25)
    assert std == pytest.approx(np.sqrt(0.4 * 0.6) * 0.25)


def test_ladder_moments_missing_strike_lands_on_the_next_level():
    # Jan 2024: 5.25 and 5.75 listed, 5.50 not. "Above 5.25, not above 5.75" is 5.50, not 5.75.
    mean, _ = pmf.ladder_moments([5.00, 5.25, 5.75], [0.99, 0.97, 0.01], 0.25, discrete=True)
    assert mean == pytest.approx(0.01 * 5.00 + 0.02 * 5.25 + 0.96 * 5.50 + 0.01 * 6.00)


def test_ladder_moments_enforces_monotone_survival():
    crossed = pmf.ladder_moments([0.1, 0.2, 0.3], [0.5, 0.6, 0.1], 0.1)
    clean = pmf.ladder_moments([0.1, 0.2, 0.3], [0.5, 0.5, 0.1], 0.1)
    assert crossed == pytest.approx(clean)


def test_as_of_bars_never_shows_a_bar_its_own_day():
    daily = pd.DataFrame({"x": [1.0, 2.0]}, index=pd.to_datetime(["2026-09-24", "2026-09-25"]))
    got = pmf.as_of_bars(daily, pd.to_datetime(["2026-09-24", "2026-09-25", "2026-09-28"]))
    assert np.isnan(got.loc["2026-09-24", "x"])  # its own day is not known at its close
    assert got.loc["2026-09-25", "x"] == 1.0
    assert got.loc["2026-09-28", "x"] == 2.0  # carried over the weekend


def test_as_of_bars_goes_stale():
    daily = pd.DataFrame({"x": [1.0]}, index=pd.to_datetime(["2026-09-01"]))
    got = pmf.as_of_bars(daily, pd.to_datetime(["2026-09-05", "2026-09-30"]))
    assert got.loc["2026-09-05", "x"] == 1.0 and np.isnan(got.loc["2026-09-30", "x"])


def _snap(day, series, event, ticker, prob, strike=None, kind="close_above", expiry="2026-12-16", result=None):
    return {
        "date": pd.Timestamp(day),
        "venue": "kalshi",
        "series": series,
        "event_ticker": event,
        "market_ticker": ticker,
        "label": None,
        "contract_type": kind,
        "strike": strike,
        "strike_cap": None,
        "expiry": pd.Timestamp(expiry),
        "prob": prob,
        "bid": None,
        "ask": None,
        "volume": None,
        "open_interest": None,
        "result": result,
    }


def test_daily_features_fed_path_and_cut():
    rows = [
        # the September meeting settled at 4.25 (above 4.00 yes, above 4.25 no)
        _snap(
            "2026-09-16",
            "fed_rate",
            "KXFED-26SEP",
            "KXFED-26SEP-T4.00",
            0.99,
            4.00,
            expiry="2026-09-16 18:00",
            result="yes",
        ),
        _snap(
            "2026-09-16",
            "fed_rate",
            "KXFED-26SEP",
            "KXFED-26SEP-T4.25",
            0.01,
            4.25,
            expiry="2026-09-16 18:00",
            result="no",
        ),
        # October: 30% chance of a cut to 4.00
        _snap("2026-09-25", "fed_rate", "KXFED-26OCT", "KXFED-26OCT-T3.75", 1.0, 3.75, expiry="2026-10-28"),
        _snap("2026-09-25", "fed_rate", "KXFED-26OCT", "KXFED-26OCT-T4.00", 0.7, 4.00, expiry="2026-10-28"),
        _snap("2026-09-25", "fed_rate", "KXFED-26OCT", "KXFED-26OCT-T4.25", 0.0, 4.25, expiry="2026-10-28"),
        # March 2027 (~6 months): expected 3.75
        _snap("2026-09-25", "fed_rate", "KXFED-27MAR", "KXFED-27MAR-T3.50", 1.0, 3.50, expiry="2027-03-17"),
        _snap("2026-09-25", "fed_rate", "KXFED-27MAR", "KXFED-27MAR-T3.75", 0.0, 3.75, expiry="2027-03-17"),
        _snap(
            "2026-09-25",
            "fed_decision",
            "KXFEDDECISION-26OCT",
            "KXFEDDECISION-26OCT-H0",
            0.75,
            kind="event",
            expiry="2026-10-28",
        ),
        _snap(
            "2026-09-25",
            "fed_decision",
            "KXFEDDECISION-26OCT",
            "KXFEDDECISION-26OCT-C25",
            0.25,
            kind="event",
            expiry="2026-10-28",
        ),
    ]
    feats = pmf.daily_features(pd.DataFrame(rows)).loc["2026-09-25"]
    assert feats["fed_next_bps"] == pytest.approx((0.3 * 4.00 + 0.7 * 4.25 - 4.25) * 100)
    assert feats["fed_path_bps"] == pytest.approx(-50.0)
    assert feats["fed_cut_next_prob"] == pytest.approx(0.25)  # the decision market wins over the ladder


def test_daily_features_recession_blend_and_shutdown_zero():
    rows = [
        _snap("2026-07-01", "recession", "KXRECSSNBER-26", "KXRECSSNBER-26", 0.10, kind="event", expiry="2027-01-31"),
        _snap("2026-07-01", "recession", "KXRECSSNBER-27", "KXRECSSNBER-27", 0.30, kind="event", expiry="2028-01-31"),
        _snap(
            "2026-06-01",
            "shutdown",
            "KXGOVTSHUTDOWN-26JUN",
            "KXGOVTSHUTDOWN-26JUN",
            0.2,
            kind="event",
            expiry="2026-06-10",
        ),
    ]
    feats = pmf.daily_features(pd.DataFrame(rows))
    f = pd.Timestamp("2026-07-01").dayofyear / 366
    assert feats.loc["2026-07-01", "recession_prob"] == pytest.approx((1 - f) * 0.10 + f * 0.30)
    assert feats.loc["2026-06-01", "shutdown_prob"] == pytest.approx(0.2)
    assert feats.loc["2026-07-01", "shutdown_prob"] == 0.0  # series live, no contract open


def test_fill_gaps_carries_a_quiet_strike_so_the_ladder_stays_whole():
    rows = [
        # December settled the upper bound at 5.50 (above 5.25 yes, above 5.50 no)
        _snap(
            "2023-12-13",
            "fed_rate",
            "FED-23DEC",
            "FED-23DEC-T5.25",
            0.99,
            5.25,
            expiry="2023-12-13 19:00",
            result="yes",
        ),
        _snap(
            "2023-12-13", "fed_rate", "FED-23DEC", "FED-23DEC-T5.50", 0.01, 5.50, expiry="2023-12-13 19:00", result="no"
        ),
        # January: a certain hold at 5.50
        _snap("2023-12-29", "fed_rate", "FED-24JAN", "FED-24JAN-T5.25", 1.00, 5.25, expiry="2024-01-31"),
        _snap("2023-12-29", "fed_rate", "FED-24JAN", "FED-24JAN-T5.50", 0.03, 5.50, expiry="2024-01-31"),
        _snap("2023-12-29", "fed_rate", "FED-24JAN", "FED-24JAN-T5.75", 0.01, 5.75, expiry="2024-01-31"),
        # 12-31: only the 5.75 strike traded. Alone it reads as a certain hike to 5.75.
        _snap("2023-12-31", "fed_rate", "FED-24JAN", "FED-24JAN-T5.75", 0.01, 5.75, expiry="2024-01-31"),
    ]
    feats = pmf.daily_features(pd.DataFrame(rows))
    assert feats.loc["2023-12-31", "fed_next_bps"] == pytest.approx(1.0)  # 0.02 x 25bp + 0.01 x 50bp
    # carried, but never past the market's own close
    filled = pmf.fill_gaps(pd.DataFrame(rows))
    assert filled.loc[filled["market_ticker"] == "FED-23DEC-T5.25", "date"].max() == pd.Timestamp("2023-12-13")


# ---------------------------------------------------------------- overlay bot

FRIDAY, THURSDAY = "2026-09-18", "2026-09-17"


def _overlay(path=None, **params):
    bot = PredictionMarketOverlayBot.__new__(PredictionMarketOverlayBot)
    bot.bot_name = "PredictionMarketOverlayBot"
    defaults = {
        "signal": "pm",
        "rec_lo": 0.25,
        "rec_hi": 0.6,
        "hawk_bps": 50.0,
        "hawk_lookback": 2,
        "equity_max": 0.8,
        "gold": 0.1,
        "rebalance_weekday": 4,
    }
    for key, value in {**defaults, **params}.items():
        setattr(bot, key, value)
    history = pd.DataFrame({"pm_fed_path_bps": path if path is not None else [np.nan] * 3})
    bot.datas = {"QQQ": history}
    return bot


def _orow(ts=FRIDAY, recession=np.nan, close=100.0, sma200=90.0):
    return pd.Series({"close": close, "sma200": sma200, "pm_recession_prob": recession}, name=pd.Timestamp(ts))


def _rows(**kw):
    return {t: _orow(**kw) for t in ["SPY", "QQQ", "IEF", "GLD"]}


def test_overlay_holds_off_schedule_and_without_features():
    assert _overlay().targetWeights(_rows(ts=THURSDAY, recession=0.9)) is None
    assert _overlay().targetWeights(_rows()) is None  # every feature NaN: no rebalance, not an exit


def test_overlay_calm_market_holds_the_base_book():
    weights = _overlay(path=[0.0, 0.0, 0.0]).targetWeights(_rows(recession=0.1))
    assert weights == pytest.approx({"SPY": 0.4, "QQQ": 0.4, "IEF": 0.1, "GLD": 0.1})


def test_overlay_recession_moves_equity_into_bonds():
    weights = _overlay().targetWeights(_rows(recession=0.6))
    assert weights == pytest.approx({"IEF": 0.9, "GLD": 0.1})
    assert "USD" not in weights and sum(weights.values()) <= 1 + 1e-9


def test_overlay_hawkish_repricing_moves_equity_to_cash():
    # the 6-month path rose 25bp over 2 bars: half of hawk_bps -> r = 0.5
    weights = _overlay(path=[-50.0, -40.0, -25.0]).targetWeights(_rows(recession=0.1))
    assert weights == pytest.approx({"SPY": 0.2, "QQQ": 0.2, "IEF": 0.1, "GLD": 0.1})


def test_overlay_sma200_ablation():
    below = _overlay(signal="sma200").targetWeights(_rows(close=80.0, sma200=90.0))
    assert below == pytest.approx({"IEF": 0.9, "GLD": 0.1})
    assert _overlay(signal="static").targetWeights(_rows()) == pytest.approx(
        {"SPY": 0.4, "QQQ": 0.4, "IEF": 0.1, "GLD": 0.1}
    )
