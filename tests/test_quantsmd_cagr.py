import datetime

import pandas as pd
import pytest

from scripts.onetime_quantsmd_backtest import true_cagr


def test_true_cagr_monthly_rotation():
    """Acceptance: true_cagr(4.58, '2006-03-16', '2026-11-06') within 0.1 of 8.7%."""
    cagr = true_cagr(4.58, "2006-03-16", "2026-11-06")
    assert cagr is not None
    assert abs(cagr * 100 - 8.7) < 0.1

    # Also test with pd.Timestamp and datetime.date objects
    cagr_ts = true_cagr(4.58, pd.Timestamp("2006-03-16"), pd.Timestamp("2026-11-06"))
    assert cagr_ts == pytest.approx(cagr)

    cagr_date = true_cagr(4.58, datetime.date(2006, 3, 16), datetime.date(2026, 11, 6))
    assert cagr_date == pytest.approx(cagr)


def test_true_cagr_negative_return():
    """Negative total return works as expected."""
    cagr = true_cagr(-0.30, "2006-03-16", "2026-11-06")
    assert cagr is not None
    assert cagr < 0
    # Expected: (1 - 0.3) ** (1 / 20.64339) - 1 ~ -0.0171
    assert abs(cagr * 100 - (-1.71)) < 0.1

    # Total loss: -100%
    assert true_cagr(-1.0, "2006-03-16", "2026-11-06") == -1.0


def test_true_cagr_edge_cases():
    """Zero-length or invalid windows return None without crashing."""
    # Zero-length window
    assert true_cagr(4.58, "2026-11-06", "2026-11-06") is None

    # Inverted window
    assert true_cagr(4.58, "2026-11-06", "2006-03-16") is None

    # Invalid / None inputs
    assert true_cagr(None, "2006-03-16", "2026-11-06") is None
    assert true_cagr(4.58, None, "2026-11-06") is None
    assert true_cagr(4.58, "invalid-date", "2026-11-06") is None

    # Loss exceeding 100%
    assert true_cagr(-1.5, "2006-03-16", "2026-11-06") is None
