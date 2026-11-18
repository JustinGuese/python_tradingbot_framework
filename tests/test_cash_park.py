"""Unit tests for park_idle_cash utility."""

import pytest

from tradingbot.utils.cash_park import park_idle_cash


def test_empty_weights_parks_100_percent():
    w = {}
    result = park_idle_cash(w)
    assert result == {"SHV": 1.0}
    assert result is not w


def test_long_only_parks_remaining_capacity():
    w = {"AAPL": 0.6}
    result = park_idle_cash(w)
    assert pytest.approx(result["AAPL"]) == 0.6
    assert pytest.approx(result["SHV"]) == 0.4
    assert result is not w
    assert "SHV" not in w


def test_fully_invested_leaves_weights_unchanged():
    w = {"AAPL": 1.0}
    result = park_idle_cash(w)
    assert result == {"AAPL": 1.0}
    assert result is not w


def test_over_invested_leaves_weights_unchanged():
    w = {"AAPL": 1.2}
    result = park_idle_cash(w)
    assert result == {"AAPL": 1.2}
    assert result is not w


def test_idle_below_min_weight_leaves_weights_unchanged():
    # idle = 0.005 < min_weight (0.01)
    w = {"AAPL": 0.995}
    result = park_idle_cash(w, min_weight=0.01)
    assert result == {"AAPL": 0.995}
    assert result is not w


def test_idle_at_min_weight_boundary():
    w = {"AAPL": 0.99}
    result = park_idle_cash(w, min_weight=0.01)
    assert pytest.approx(result["AAPL"]) == 0.99
    assert pytest.approx(result["SHV"]) == 0.01


def test_existing_proxy_weight_is_incremented():
    w = {"AAPL": 0.4, "SHV": 0.2}
    # net = 0.6, gross = 0.6, idle = 0.4
    result = park_idle_cash(w)
    assert pytest.approx(result["AAPL"]) == 0.4
    assert pytest.approx(result["SHV"]) == 0.6
    assert pytest.approx(w["SHV"]) == 0.2  # input unchanged


def test_existing_proxy_fully_invested_unchanged():
    w = {"SHV": 1.0}
    result = park_idle_cash(w)
    assert result == {"SHV": 1.0}
    assert result is not w


def test_long_short_constrained_by_gross():
    # Long 0.5, Short -0.3, max_gross 1.0:
    # net = 0.2 -> 1 - net = 0.8
    # gross = 0.8 -> max_gross - gross = 0.2
    # idle = min(0.8, 0.2) = 0.2
    w = {"AAPL": 0.5, "MSFT": -0.3}
    result = park_idle_cash(w, max_gross=1.0)
    assert pytest.approx(result["AAPL"]) == 0.5
    assert pytest.approx(result["MSFT"]) == -0.3
    assert pytest.approx(result["SHV"]) == 0.2

    # Verify gross <= 1.0 and net <= 1.0
    gross = sum(abs(v) for v in result.values())
    net = sum(result.values())
    assert pytest.approx(gross) == 1.0
    assert pytest.approx(net) == 0.4


def test_long_short_constrained_by_net():
    # Long 0.8, Short -0.1, max_gross 1.5:
    # net = 0.7 -> 1 - net = 0.3
    # gross = 0.9 -> max_gross - gross = 0.6
    # idle = min(0.3, 0.6) = 0.3
    w = {"AAPL": 0.8, "MSFT": -0.1}
    result = park_idle_cash(w, max_gross=1.5)
    assert pytest.approx(result["AAPL"]) == 0.8
    assert pytest.approx(result["MSFT"]) == -0.1
    assert pytest.approx(result["SHV"]) == 0.3

    net = sum(result.values())
    assert pytest.approx(net) == 1.0


def test_dollar_neutral_with_max_gross_headroom():
    # Long 0.5, Short -0.5, max_gross 2.0:
    # net = 0.0 -> 1 - net = 1.0
    # gross = 1.0 -> max_gross - gross = 1.0
    # idle = min(1.0, 1.0) = 1.0
    w = {"AAPL": 0.5, "MSFT": -0.5}
    result = park_idle_cash(w, max_gross=2.0)
    assert pytest.approx(result["AAPL"]) == 0.5
    assert pytest.approx(result["MSFT"]) == -0.5
    assert pytest.approx(result["SHV"]) == 1.0
    assert pytest.approx(sum(result.values())) == 1.0
    assert pytest.approx(sum(abs(v) for v in result.values())) == 2.0


def test_dollar_neutral_at_max_gross_unchanged():
    # Long 0.5, Short -0.5, max_gross 1.0:
    # gross = 1.0, max_gross - gross = 0.0
    w = {"AAPL": 0.5, "MSFT": -0.5}
    result = park_idle_cash(w, max_gross=1.0)
    assert result == {"AAPL": 0.5, "MSFT": -0.5}
    assert result is not w


def test_short_only_parks_up_to_max_gross():
    # Short -0.4, max_gross 1.0:
    # net = -0.4 -> 1 - net = 1.4
    # gross = 0.4 -> max_gross - gross = 0.6
    # idle = min(1.4, 0.6) = 0.6
    w = {"AAPL": -0.4}
    result = park_idle_cash(w, max_gross=1.0)
    assert pytest.approx(result["AAPL"]) == -0.4
    assert pytest.approx(result["SHV"]) == 0.6
    assert pytest.approx(sum(result.values())) == 0.2
    assert pytest.approx(sum(abs(v) for v in result.values())) == 1.0


def test_custom_proxy():
    w = {"AAPL": 0.7}
    result = park_idle_cash(w, proxy="BIL")
    assert "SHV" not in result
    assert pytest.approx(result["BIL"]) == 0.3


def test_never_shorts_proxy():
    # Even if gross or net is beyond bounds, idle is negative, proxy is never shorted
    w = {"AAPL": 1.5}
    result = park_idle_cash(w, min_weight=-0.5)
    assert "SHV" not in result
    assert result == {"AAPL": 1.5}


def test_idempotence():
    w = {"AAPL": 0.4}
    step1 = park_idle_cash(w)
    step2 = park_idle_cash(step1)
    assert step1 == step2
    assert step2 == {"AAPL": 0.4, "SHV": 0.6}


def test_preserves_dictionary_copy():
    w = {"AAPL": 0.5}
    result = park_idle_cash(w)
    result["MSFT"] = 0.1
    assert "MSFT" not in w

    w_full = {"AAPL": 1.0}
    result_full = park_idle_cash(w_full)
    assert result_full is not w_full
    result_full["AAPL"] = 0.5
    assert w_full["AAPL"] == 1.0
