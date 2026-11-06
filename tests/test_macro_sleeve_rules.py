from datetime import date

from tradingbot.utils.macro_sleeve_rules import target_weights

FOMC = [("FOMC", date(2026, 10, 28))]  # Wednesday


def test_pre_fomc_day_holds_tlt_and_gld_equally():
    assert target_weights(date(2026, 10, 27), FOMC) == {"TLT": 0.5, "GLD": 0.5}


def test_fomc_day_and_next_hold_uup_then_exit():
    assert target_weights(date(2026, 10, 28), FOMC) == {"UUP": 1.0}
    assert target_weights(date(2026, 10, 29), FOMC) == {"UUP": 1.0}
    assert target_weights(date(2026, 10, 30), FOMC) == {}  # exit at T+2 close


def test_cpi_day_holds_tlt_and_overlap_splits_per_leg():
    assert target_weights(date(2026, 11, 10), [("CPI", date(2026, 11, 10))]) == {"TLT": 1.0}
    both = [("FOMC", date(2026, 12, 9)), ("CPI", date(2026, 12, 9))]
    w = target_weights(date(2026, 12, 9), both)
    assert w == {"UUP": 0.5, "TLT": 0.5}


def test_weekend_skip_and_quiet_day():
    # Friday before a Monday event is offset -1
    assert target_weights(date(2026, 11, 6), [("FOMC", date(2026, 11, 9))]) == {"TLT": 0.5, "GLD": 0.5}
    assert target_weights(date(2026, 11, 3), FOMC) == {}
