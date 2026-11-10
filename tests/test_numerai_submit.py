"""Tests for Numerai Signals prediction submitter job."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tradingbot.numerai_signals_submit import (
    build_ticker_mapping,
    check_credentials,
    map_signals_to_numerai,
    submit_signals,
)


@pytest.fixture
def synthetic_prices():
    """Create 270 days of daily prices for 120 tickers."""
    dates = pd.date_range("2023-01-01", periods=270, freq="B", tz="UTC")
    tickers = [f"STOCK{i:03d}" for i in range(120)]
    rng = np.random.default_rng(42)
    daily_returns = rng.normal(0.0005, 0.015, size=(270, 120))
    price_matrix = 100.0 * np.cumprod(1.0 + daily_returns, axis=0)
    return pd.DataFrame(price_matrix, index=dates, columns=tickers)


@pytest.fixture
def mock_napi():
    """Create a mock SignalsAPI instance."""
    api = MagicMock()
    api.get_models.return_value = {"datafortress": "uuid-datafortress-1234"}
    api.get_current_round.return_value = 500
    api.get_submission_filenames.return_value = []
    api.upload_predictions.return_value = "sub_98765"
    return api


def test_maps_yfinance_to_numerai_and_drops_unmapped(synthetic_prices, mock_napi):
    """Test 1: maps yfinance -> Numerai tickers, drops unmapped ones, formats CSV."""
    # 110 valid US tickers (STOCK000 to STOCK109) + 5 unmappable ticker strings
    mappable = [f"STOCK{i:03d} US" for i in range(110)]
    unmappable = ["BADTICKER 999", "INVALID/XYZ FOO BAR", "NOEXCHANGE"]
    universe = mappable + unmappable
    mock_napi.ticker_universe.return_value = universe

    captured_df = None

    def capture_upload(csv_path, model_id=None):
        nonlocal captured_df
        captured_df = pd.read_csv(csv_path)
        return "sub_12345"

    mock_napi.upload_predictions.side_effect = capture_upload

    # Direct unit test on helper functions
    mapping = build_ticker_mapping(universe)
    assert len(mapping) == 110
    for i in range(110):
        assert mapping[f"STOCK{i:03d}"] == f"STOCK{i:03d} US"
    for unmap in unmappable:
        assert unmap not in mapping.values()

    # Signals with some extra unmapped yfinance ticker
    sample_signals = pd.Series(
        [0.55, 0.45, 0.85],
        index=["STOCK000", "STOCK001", "EXTRA_UNMAPPED"],
    )
    mapped_df = map_signals_to_numerai(sample_signals, mapping)
    assert len(mapped_df) == 2
    assert "EXTRA_UNMAPPED" not in mapped_df["numerai_ticker"].values
    assert set(mapped_df["numerai_ticker"].values) == {"STOCK000 US", "STOCK001 US"}

    # End-to-end submit_signals call with mocked API and prices
    ret = submit_signals(
        public_id="mock_pub",
        secret_key="mock_sec",
        model_name="datafortress",
        prices=synthetic_prices,
        napi=mock_napi,
    )

    assert ret == 0
    mock_napi.upload_predictions.assert_called_once()
    assert mock_napi.upload_predictions.call_args.kwargs["model_id"] == "uuid-datafortress-1234"
    assert captured_df is not None
    assert captured_df.columns.tolist() == ["numerai_ticker", "signal"]
    assert len(captured_df) == 110
    # Confirm none of the unmappable tickers made it into predictions
    for unmap in unmappable:
        assert unmap not in captured_df["numerai_ticker"].values
    # Check signal values strictly in (0, 1)
    assert (captured_df["signal"] > 0.0).all()
    assert (captured_df["signal"] < 1.0).all()


def test_refuses_to_upload_under_100_tickers(synthetic_prices, mock_napi):
    """Test 2: Refuses to upload when fewer than 100 tickers are valid/mapped."""
    # Only 50 mappable tickers
    small_universe = [f"STOCK{i:03d} US" for i in range(50)]
    mock_napi.ticker_universe.return_value = small_universe

    with pytest.raises(ValueError, match="Refusing to upload under 100 tickers"):
        submit_signals(
            public_id="mock_pub",
            secret_key="mock_sec",
            model_name="datafortress",
            prices=synthetic_prices,
            napi=mock_napi,
        )

    mock_napi.upload_predictions.assert_not_called()


def test_second_run_in_same_round_skips(synthetic_prices, mock_napi):
    """Test 3: Idempotent per round; a second run in the same round skips upload."""
    mock_napi.ticker_universe.return_value = [f"STOCK{i:03d} US" for i in range(110)]

    # Mock an existing submission for the current round (500)
    mock_napi.get_submission_filenames.return_value = [{"filename": "predictions.csv", "round_num": 500}]

    ret = submit_signals(
        public_id="mock_pub",
        secret_key="mock_sec",
        model_name="datafortress",
        prices=synthetic_prices,
        napi=mock_napi,
    )

    # Returns 0 without uploading
    assert ret == 0
    mock_napi.upload_predictions.assert_not_called()


def test_missing_credentials_exit_2_never_logs_secrets(monkeypatch, caplog):
    """Test 4: Missing credentials exits 2 and logs names without exposing secrets."""
    secret_canary = "SUPER_SECRET_CANARY_VALUE"

    monkeypatch.setenv("NUMERAI_PUBLIC_ID", secret_canary)
    monkeypatch.delenv("NUMERAI_SECRET_KEY", raising=False)
    monkeypatch.delenv("NUMERAI_MODEL_NAME", raising=False)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as exc_info:
            check_credentials()
        assert exc_info.value.code == 2

    # Secret canary value must NEVER appear in the log output
    assert secret_canary not in caplog.text
    # Missing variable names must be reported
    assert "NUMERAI_SECRET_KEY" in caplog.text
    assert "NUMERAI_MODEL_NAME" in caplog.text
