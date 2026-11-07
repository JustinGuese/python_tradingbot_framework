"""
Pure unit tests for Hard Events with LLM Labels study (Step B2 of PLAN.md).

No DB access, no network access.
"""

from __future__ import annotations

from datetime import UTC, datetime
import pandas as pd
import pytest

from scripts.onetime_event_hard_study import (
    apply_costs_to_strategy,
    filter_too_few,
    normalize_label,
    parse_batch_response,
)


def test_normalize_label_canonical_and_fuzzy():
    """Test label normalization for exact classes, keyword variants, and unknown headlines."""
    # Exact matches
    assert normalize_label("refinery/pipeline outage") == "refinery/pipeline outage"
    assert normalize_label("hurricane/storm") == "hurricane/storm"
    assert normalize_label("sanctions") == "sanctions"
    assert normalize_label("strike/shutdown") == "strike/shutdown"
    assert normalize_label("chip/export control") == "chip/export control"
    assert normalize_label("other") == "other"

    # Fuzzy / keyword phrases
    assert normalize_label("Colonial Pipeline main line outage") == "refinery/pipeline outage"
    assert normalize_label("Category 4 hurricane approaches Florida") == "hurricane/storm"
    assert normalize_label("US expands sanctions on foreign oil fleet") == "sanctions"
    assert normalize_label("Port workers vote to strike tomorrow") == "strike/shutdown"
    assert normalize_label("New export control restrictions on advanced AI chips") == "chip/export control"

    # Non-event / irrelevant headlines
    assert normalize_label("Microsoft reports quarterly earnings growth") == "other"
    assert normalize_label(None) == "other"
    assert normalize_label(123) == "other"


def test_parse_batch_response_valid_and_code_fences():
    """Test JSON array parsing with clean text and markdown code fences."""
    # Clean JSON array
    raw_1 = '["refinery/pipeline outage", "hurricane/storm", "sanctions"]'
    parsed_1 = parse_batch_response(raw_1, expected_len=3)
    assert parsed_1 == ["refinery/pipeline outage", "hurricane/storm", "sanctions"]

    # Fenced JSON with surrounding text
    raw_2 = """Here is the classification:
```json
[
  "strike/shutdown",
  "chip/export control"
]
```
Done."""
    parsed_2 = parse_batch_response(raw_2, expected_len=2)
    assert parsed_2 == ["strike/shutdown", "chip/export control"]


def test_parse_batch_response_mismatch_fallback():
    """Test that length mismatch or invalid JSON returns None to trigger fallback."""
    # Length mismatch (expected 3, got 2)
    raw_mismatch = '["sanctions", "strike/shutdown"]'
    assert parse_batch_response(raw_mismatch, expected_len=3) is None

    # Invalid JSON syntax
    raw_invalid = '["sanctions", "broken'
    assert parse_batch_response(raw_invalid, expected_len=2) is None

    # Empty string or wrong type
    assert parse_batch_response("", expected_len=1) is None
    assert parse_batch_response(None, expected_len=1) is None  # type: ignore


def test_filter_too_few_threshold():
    """Test that event counts under threshold (default 20) are flagged as too few."""
    counts = {
        "refinery/pipeline outage": 12,
        "hurricane/storm": 7,
        "sanctions": 25,
        "strike/shutdown": 40,
        "chip/export control": 105,
        "other": 500,
    }
    qualifying, too_few = filter_too_few(counts, min_events=20)

    assert "sanctions" in qualifying
    assert "strike/shutdown" in qualifying
    assert "chip/export control" in qualifying
    assert qualifying["sanctions"] == 25

    assert "refinery/pipeline outage" in too_few
    assert "hurricane/storm" in too_few
    assert "other" not in qualifying
    assert "other" not in too_few


def test_window_math_and_cost_application():
    """
    Test look-ahead guard and round-trip fee deduction on synthetic daily returns.
    """
    # 5 sessions: 2026-03-02 (Mon) to 2026-03-06 (Fri)
    dates = pd.date_range("2026-03-02", periods=5, freq="B")
    strat_rets = pd.Series(0.01, index=dates)

    # Event timestamp: 2026-03-02 10:00 UTC (during session, before 16:00 close)
    # First close strictly after is 2026-03-02 close -> entry is 2026-03-02 close
    sig_df = pd.DataFrame([{
        "ts": pd.Timestamp("2026-03-02 10:00:00", tz=UTC),
        "symbol": "XLE",
    }])

    cost_per_side = 0.0005  # 5 bp

    # Test hold_days=1 ([0,+1]): held session is 2026-03-03
    # Both entry and exit occur on this held bar -> total cost 2 * cost_per_side (10 bp)
    net_rets_1d = apply_costs_to_strategy(strat_rets, sig_df, dates, hold_days=1, cost_per_side=cost_per_side)
    expected_ret_1d = 0.01 - 2 * cost_per_side
    assert pytest.approx(net_rets_1d.loc[dates[1]]) == expected_ret_1d
    assert pytest.approx(net_rets_1d.loc[dates[0]]) == 0.01  # Unaffected entry day

    # Test hold_days=3 ([0,+3]): held sessions are 2026-03-03, 2026-03-04, 2026-03-05
    # Entry fee on 2026-03-03, exit fee on 2026-03-05, 2026-03-04 incurs no fee
    net_rets_3d = apply_costs_to_strategy(strat_rets, sig_df, dates, hold_days=3, cost_per_side=cost_per_side)
    assert pytest.approx(net_rets_3d.loc[dates[1]]) == 0.01 - cost_per_side
    assert pytest.approx(net_rets_3d.loc[dates[2]]) == 0.01
    assert pytest.approx(net_rets_3d.loc[dates[3]]) == 0.01 - cost_per_side
