import pytest
from pathlib import Path

from scripts.onetime_quantsmd_fix_doc_cagr import (
    END_DATE,
    METHOD_NOTE,
    fix_doc,
    fix_file,
    recompute_cagr_cell,
)


def test_conversion_of_known_rows():
    """Acceptance: MonthlyRotation shows ~8.8% (8.7%), SmaDaily shows ~7.7%, intraday shows n/a."""
    # MonthlyRotation (2006, 4.7%) -> 8.7%
    cagr_mr = recompute_cagr_cell("2006", "4.7%", END_DATE)
    assert cagr_mr == "8.7%"

    # SmaDaily (2000, 6.3%) -> 7.7%
    cagr_sma = recompute_cagr_cell("2000", "6.3%", END_DATE)
    assert cagr_sma == "7.7%"

    # Intraday row with MM-DD start -> n/a
    assert recompute_cagr_cell("08-11", "-0.0%", END_DATE) == "n/a"
    assert recompute_cagr_cell("10-23", "-0.1%", END_DATE) == "n/a"

    # Negative return row
    cagr_neg = recompute_cagr_cell("2006", "-0.1%", END_DATE)
    assert cagr_neg == "-0.5%"

    # Sample table conversion
    sample = (
        "Method: standard test.\n\n"
        "| Bot (source Sharpe) | Start | Alpha/yr | t | Beta | Corr | Sharpe | CAGR | Max DD |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| MonthlyRotation (0.53) | 2006 | +5.1% | 1.86 | 0.26 | 0.42 | 0.69 | 4.7% | -30.4% |\n"
        "| SmaDaily (0.45) | 2000 | +4.8% | 2.27 | 0.32 | 0.61 | 0.61 | 6.3% | -35.4% |\n"
        "| IntradayRsi15m (—) | 08-11 | -2.9% | -0.35 | 0.22 | 0.69 | -1.44 | -0.0% | -3.1% |\n"
    )
    fixed = fix_doc(sample)
    assert "CAGR (true)" in fixed
    assert "| MonthlyRotation (0.53) | 2006 | +5.1% | 1.86 | 0.26 | 0.42 | 0.69 | 8.7% | -30.4% |" in fixed
    assert "| SmaDaily (0.45) | 2000 | +4.8% | 2.27 | 0.32 | 0.61 | 0.61 | 7.7% | -35.4% |" in fixed
    assert "| IntradayRsi15m (—) | 08-11 | -2.9% | -0.35 | 0.22 | 0.69 | -1.44 | n/a | -3.1% |" in fixed
    assert METHOD_NOTE in fixed


def test_idempotency(tmp_path: Path):
    """Running fix_doc or fix_file a second time changes nothing."""
    doc_path = Path("docs/backtests/quantsmd-2026-11.md")
    content = doc_path.read_text(encoding="utf-8")

    fixed_once = fix_doc(content)
    fixed_twice = fix_doc(fixed_once)
    assert fixed_once == fixed_twice

    # Test file-level idempotency
    temp_file = tmp_path / "test_doc.md"
    temp_file.write_text(content, encoding="utf-8")

    first_change = fix_file(temp_file)
    # The file in repo may already have been updated by the script, but fix_file should be stable:
    second_change = fix_file(temp_file)
    assert second_change is False
    assert fix_doc(temp_file.read_text(encoding="utf-8")) == temp_file.read_text(encoding="utf-8")


def test_edge_cases_and_security():
    """Malformed dates, extreme losses, or corrupted rows do not crash."""
    assert recompute_cagr_cell("invalid", "4.7%") == "n/a"
    assert recompute_cagr_cell("2006", "not_a_number%") == "n/a"
    assert recompute_cagr_cell("", "") == "n/a"
    assert recompute_cagr_cell("2006", "-150%") == "n/a"
    assert recompute_cagr_cell("2006", "-100%") == "-100.0%"

    # Doc without tables or without target columns
    empty_doc = "# Title\n\nSome text without tables."
    assert fix_doc(empty_doc) == empty_doc
