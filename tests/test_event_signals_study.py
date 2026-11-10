"""
Unit tests for Signals Bank follow-the-signal event study (Step E3).

Pure unit tests: no DB access, no network access.
"""

from scripts.onetime_event_signals_study import (
    PRE_REGISTERED_VARIANTS,
    map_symbol,
    parse_direction,
)


def test_parse_direction_four_fixed_summaries():
    """
    Direction parser on 4 fixed summaries:
    Two BUY/LONG examples and two SELL/SHORT examples.
    """
    # 1. Clear BUY recommendation with levels
    s1 = "The recommendation is to BUY the US30 (Dow Jones) at 46,615.80 with a stop-loss at 46,315.80."
    assert parse_direction(s1) == 1

    # 2. LONG trade suggestion
    s2 = "A long trade on Bitcoin is suggested with entry at $70,750, stop-loss at $70,000 and take-profit at $71,555."
    assert parse_direction(s2) == 1

    # 3. Direct SELL order
    s3 = "Sell US100 at 25,107.4 with a stop-loss at 25,257.4 and three take-profit targets at 25,037.4."
    assert parse_direction(s3) == -1

    # 4. SHORT limit order
    s4 = (
        "A short limit order on gold (XAU/USD) is placed at 5,180 with a stop-loss at 5,202 and a take-profit at 5,129."
    )
    assert parse_direction(s4) == -1


def test_parse_direction_ambiguous_and_temporal():
    """
    Summaries without actionable direction or with temporal 'short-term' words
    should return None, avoiding false short signals.
    """
    # Performance recap / TP hit without new direction
    assert parse_direction("TP 1 on the US500 index was just hit, the stop loss has been moved to break even.") is None

    # Temporal phrase 'short-term' without buy/sell entry
    assert parse_direction("The pair experienced short-term consolidation around the weekly pivot.") is None

    # Both buy and sell mentioned (ambiguous commentary / summary)
    assert parse_direction("Traders are split whether to buy or sell at this key inflection level.") is None

    # Empty / None
    assert parse_direction(None) is None
    assert parse_direction("") is None


def test_unmappable_symbols_dropped_not_defaulted():
    """
    Unmappable, unrecognized, or channel noise symbols must return None
    (dropped), never falling back to a default ticker like SPY or QQQ.
    """
    unmappable = [
        "BROKER",
        "THE_SIGNALS_BANK",
        "FOREX",
        "THE SIGNALS BANK",
        "TSB",
        "KONTOWACHTRACTION",
        "USD",  # bare currency, not a pair
        "UNKNOWN_TICKER_123",
        "",
        None,
    ]
    for sym in unmappable:
        assert map_symbol(sym) is None, f"Expected {sym} to be dropped (return None), got {map_symbol(sym)}"


def test_mapped_symbols_correct_tickers():
    """
    Known symbols map to valid Yahoo Finance tickers as specified in PLAN.md
    and telegramsignalsbankbot examples.
    """
    assert map_symbol("XAUUSD") == "GC=F"
    assert map_symbol("XAU/USD") == "GC=F"
    assert map_symbol("XAGUSD") == "SI=F"
    assert map_symbol("US500") == "^GSPC"
    assert map_symbol("US500.c") == "^GSPC"
    assert map_symbol("US30") == "^DJI"
    assert map_symbol("DJ30.c") == "^DJI"
    assert map_symbol("BTC") == "BTC-USD"
    assert map_symbol("BTCUSD") == "BTC-USD"
    assert map_symbol("ETH") == "ETH-USD"
    assert map_symbol("EURUSD") == "EURUSD=X"
    assert map_symbol("EUR/USD") == "EURUSD=X"
    assert map_symbol("CADJPY") == "CADJPY=X"
    assert map_symbol("AAPL") == "AAPL"


def test_pre_registered_variants_grid():
    """Ensure pre-registered variants grid includes required subsets and horizons."""
    assert len(PRE_REGISTERED_VARIANTS) == 9
    names = [v["name"] for v in PRE_REGISTERED_VARIANTS]
    assert len(names) == len(set(names)), "Variant names must be unique"
    for hold in [1, 3, 5]:
        assert f"long_only_h{hold}" in names
        assert f"all_signed_h{hold}" in names
        assert f"short_only_h{hold}" in names
