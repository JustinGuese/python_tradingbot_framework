"""
KronosClient — HTTP client for the Kronos HF Space inference API.

The Kronos model runs in a Hugging Face Docker Space (CPU, free tier, 16GB RAM).
This client handles:
  - Fetching OHLCV data via DataService
  - POSTing it to the Space's /predict endpoint
  - Returning a forecast DataFrame

Environment variables:
  KRONOS_SPACE_URL  Base URL of the HF Space (e.g. https://guestros-kronos-trading-api.hf.space)

LangChain integration:
  Import `kronos_forecast` and pass it to run_ai_with_tools(extra_tools=[kronos_forecast])
  so AI bots can call Kronos as a tool when making trading decisions.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd
import requests
from langchain_core.tools import tool

from .data_service import DataService

logger = logging.getLogger(__name__)

_SPACE_URL = os.environ.get("KRONOS_SPACE_URL", "").rstrip("/")
_PREDICT_TIMEOUT = 120  # seconds — Kronos inference on CPU can take ~30-60s


class KronosClient:
    """Thin HTTP client for the Kronos HF Space.

    Usage::

        client = KronosClient()
        pred_df = client.predict("SPY", horizon=5)
        # pred_df columns: target_date, open, high, low, close, volume

    Returns None and logs a warning on any error so callers can degrade gracefully.
    """

    def __init__(self, space_url: Optional[str] = None) -> None:
        self.space_url = (space_url or _SPACE_URL).rstrip("/")
        if not self.space_url:
            logger.warning("KronosClient: KRONOS_SPACE_URL not set — predictions will be skipped")

    def is_healthy(self) -> bool:
        """Return True if the Space's /health endpoint responds with status=ok."""
        if not self.space_url:
            return False
        try:
            resp = requests.get(f"{self.space_url}/health", timeout=10)
            return resp.ok and resp.json().get("status") == "ok"
        except Exception:
            return False

    def predict(
        self,
        symbol: str,
        horizon: int = 5,
        interval: str = "1d",
        period: str = "2y",
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV for *symbol* and return a Kronos forecast DataFrame.

        Args:
            symbol:  yfinance ticker (e.g. "SPY", "AAPL", "EURUSD=X")
            horizon: Number of future bars to predict (default 5 trading days)
            interval: OHLCV bar interval passed to DataService (default "1d")
            period:  History lookback for DataService (default "2y" ≈ 500 daily bars)

        Returns:
            DataFrame indexed 0..horizon-1 with columns:
              target_date, open, high, low, close, volume
            None on any error (network, model, insufficient data).
        """
        if not self.space_url:
            return None

        # Fetch clean OHLCV (no TA indicators — Kronos expects raw prices)
        try:
            ds = DataService()
            df = ds.get_yf_data(symbol=symbol, interval=interval, period=period)
        except Exception as exc:
            logger.warning(f"KronosClient: DataService fetch failed for {symbol}: {exc}")
            return None

        if df is None or len(df) < 50:
            logger.warning(
                f"KronosClient: insufficient data for {symbol} "
                f"({len(df) if df is not None else 0} rows, need ≥50)"
            )
            return None

        # Serialise to JSON-friendly list of records
        ohlcv_rows = []
        for _, row in df.iterrows():
            ohlcv_rows.append({
                "timestamp": str(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
            })

        payload = {
            "symbol": symbol,
            "horizon": horizon,
            "interval": interval,
            "ohlcv": ohlcv_rows,
        }

        try:
            resp = requests.post(
                f"{self.space_url}/predict",
                json=payload,
                timeout=_PREDICT_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            logger.error(f"KronosClient: predict timeout for {symbol} (>{_PREDICT_TIMEOUT}s)")
            return None
        except Exception as exc:
            logger.error(f"KronosClient: predict request failed for {symbol}: {exc}")
            return None

        try:
            preds = resp.json()["predictions"]
            pred_df = pd.DataFrame(preds)
            pred_df["target_date"] = pd.to_datetime(pred_df["target_date"])
            return pred_df
        except Exception as exc:
            logger.error(f"KronosClient: failed to parse response for {symbol}: {exc}")
            return None


# ---------------------------------------------------------------------------
# LangChain tool — drop-in for run_ai_with_tools(extra_tools=[kronos_forecast])
# ---------------------------------------------------------------------------

@tool
def kronos_forecast(symbol: str, horizon: int = 5) -> str:
    """Forecast the next N trading days (open/high/low/close/volume) for a symbol
    using the Kronos financial foundation model.

    Use this to get an AI-based OHLCV price forecast as an additional signal
    when deciding whether to buy, sell, or hold a position.

    Args:
        symbol:  Ticker symbol (e.g. "SPY", "AAPL", "QQQ")
        horizon: Number of future trading days to predict (default 5)

    Returns a table of predicted OHLCV values for each future date.
    """
    client = KronosClient()
    df = client.predict(symbol, horizon=horizon)
    if df is None:
        return f"Kronos forecast unavailable for {symbol} (Space unreachable or insufficient data)"
    return f"Kronos {horizon}-day forecast for {symbol}:\n{df.to_string(index=False)}"
