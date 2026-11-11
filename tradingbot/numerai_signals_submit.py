"""Numerai Signals prediction submitter job.

Generates 12-1 momentum signals for the Numerai Signals tournament universe,
formats predictions into the required CSV structure, and uploads them via numerapi.

NOTE: The 12-1 momentum strategy is UNPROVEN and submitted unstaked for Numerai scoring.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Bloomberg country / exchange code to yfinance ticker suffix mapping
BLOOMBERG_TO_YAHOO_EXCHANGE: dict[str, str] = {
    "US": "",  # AAPL US -> AAPL
    "LN": ".L",  # BP/ LN -> BP.L
    "GY": ".DE",  # BMW GY -> BMW.DE
    "GR": ".DE",
    "FP": ".PA",
    "PA": ".PA",
    "TO": ".TO",
    "CN": ".TO",
    "JP": ".T",
    "JT": ".T",
    "HK": ".HK",
    "AU": ".AX",
    "AT": ".AX",
    "SW": ".SW",
    "VX": ".SW",
    "IM": ".MI",
    "SM": ".MC",
    "NA": ".AS",
    "BB": ".BR",
}


def numerai_ticker_to_yfinance(ticker: str) -> str | None:
    """Map a Numerai/Bloomberg ticker symbol to a yfinance-compatible symbol.

    Examples:
        'AAPL US' -> 'AAPL'
        'BRK/B US' -> 'BRK-B'
        'BMW GY' -> 'BMW.DE'
        'AAPL' -> 'AAPL'

    Returns None if the ticker cannot be mapped.
    """
    if not ticker or not isinstance(ticker, str):
        return None
    ticker = ticker.strip()
    parts = ticker.split()
    if len(parts) == 2:
        sym, exchange = parts[0], parts[1].upper()
        sym = sym.replace("/", "-")
        if exchange == "US":
            return sym
        if exchange in BLOOMBERG_TO_YAHOO_EXCHANGE:
            suffix = BLOOMBERG_TO_YAHOO_EXCHANGE[exchange]
            return f"{sym}{suffix}"
    return None


def build_ticker_mapping(universe: Sequence[str]) -> dict[str, str]:
    """Build a mapping of {yf_ticker: numerai_ticker} for all mappable universe tickers.

    Unmapped tickers are excluded from the mapping.
    """
    mapping: dict[str, str] = {}
    for num_ticker in universe:
        yf_ticker = numerai_ticker_to_yfinance(num_ticker)
        if yf_ticker:
            mapping[yf_ticker] = num_ticker
    return mapping


def map_signals_to_numerai(
    signals: pd.Series,
    mapping: dict[str, str],
) -> pd.DataFrame:
    """Map yfinance tickers in signals to Numerai tickers and drop unmapped ones.

    Args:
        signals: pd.Series indexed by yfinance ticker symbol.
        mapping: dict mapping yfinance ticker -> Numerai ticker.

    Returns:
        pd.DataFrame with columns ['numerai_ticker', 'signal'].
    """
    records = []
    for yf_ticker, val in signals.items():
        if yf_ticker in mapping:
            records.append(
                {
                    "numerai_ticker": mapping[yf_ticker],
                    "signal": float(val),
                }
            )
    return pd.DataFrame(records)


def check_credentials(
    public_id: str | None = None,
    secret_key: str | None = None,
    model_name: str | None = None,
) -> tuple[str, str, str]:
    """Validate that required credentials are present.

    Exits with code 2 if any credential is missing, logging variable names
    without ever exposing secret values.
    """
    pub = public_id or os.environ.get("NUMERAI_PUBLIC_ID")
    sec = secret_key or os.environ.get("NUMERAI_SECRET_KEY")
    model = model_name or os.environ.get("NUMERAI_MODEL_NAME")

    missing = []
    if not pub:
        missing.append("NUMERAI_PUBLIC_ID")
    if not sec:
        missing.append("NUMERAI_SECRET_KEY")
    if not model:
        missing.append("NUMERAI_MODEL_NAME")

    if missing:
        logger.error("Missing required Numerai credentials: %s", ", ".join(missing))
        sys.exit(2)

    return pub, sec, model


def check_already_submitted(napi: Any, model_id: str, current_round: int | None) -> bool:
    """True if this model already uploaded today (UTC); Signals rounds are daily.

    numerapi's submission_ids carries only insertedAt, not the round number.
    """
    if current_round is None:
        return False
    try:
        today = datetime.now(UTC).date()
        for sub in napi.submission_ids(model_id) or []:
            ts = sub.get("insertedAt") if isinstance(sub, dict) else None
            if ts is not None and ts.date() == today:
                return True
    except Exception as exc:
        logger.warning("Could not check submissions for round %s: %s", current_round, exc)
    return False


def load_prices_from_db(
    symbols: Sequence[str], min_rows: int = 270, max_age_days: int = 6, lookback_days: int = 420
) -> pd.DataFrame:
    """Long-format daily closes (symbol, timestamp, close) from historic_data.

    Keeps only symbols with >= min_rows bars whose last bar is at most
    max_age_days old (enough for the 12-1 momentum window), in one query.
    """
    from datetime import timedelta

    from tradingbot.utils.db import HistoricData, get_db_session

    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    with get_db_session() as session:
        rows = (
            session.query(HistoricData.symbol, HistoricData.timestamp, HistoricData.close)
            .filter(
                HistoricData.interval == "1d",
                HistoricData.symbol.in_(list(symbols)),
                HistoricData.timestamp >= cutoff.replace(tzinfo=None),
            )
            .all()
        )
    df = pd.DataFrame(rows, columns=["symbol", "timestamp", "close"]).dropna(subset=["close"])
    if df.empty:
        return df
    stats = df.groupby("symbol")["timestamp"].agg(["count", "max"])
    fresh_after = pd.Timestamp(datetime.now(UTC).replace(tzinfo=None) - timedelta(days=max_age_days))
    keep = stats[(stats["count"] >= min_rows) & (pd.to_datetime(stats["max"]) >= fresh_after)].index
    return df[df["symbol"].isin(keep)].reset_index(drop=True)


def fetch_universe(napi: Any) -> list[str]:
    """The live Signals universe. numerapi's ticker_universe() reads the retired
    signals/v1.0 file, so read the current live.parquet directly."""
    last: Exception | None = None
    for version in ("v3.1", "v3.0"):
        try:
            path = napi.download_dataset(f"signals/{version}/live.parquet")
            return pd.read_parquet(path).numerai_ticker.tolist()
        except Exception as exc:
            last = exc
    raise RuntimeError(f"Numerai live universe unavailable: {last}")


def submit_signals(
    public_id: str | None = None,
    secret_key: str | None = None,
    model_name: str | None = None,
    *,
    data_service: Any = None,
    prices: pd.DataFrame | None = None,
    napi: Any = None,
    min_tickers: int = 100,
    asof: Any = None,
) -> int:
    """Main submission routine for Numerai Signals.

    1. Checks credentials.
    2. Instantiates SignalsAPI and resolves model UUID.
    3. Checks idempotency per round (skips if already submitted).
    4. Fetches universe, maps tickers, drops unmapped.
    5. Fetches historical prices via DataService and computes signals via build_signal.
    6. Formats predictions to CSV and uploads predictions.

    Returns:
        0 on success or skipped upload, non-zero on failure.
    """
    pub, sec, model = check_credentials(public_id, secret_key, model_name)

    if napi is None:
        # Lazy import of numerapi inside function
        from numerapi import SignalsAPI

        napi = SignalsAPI(public_id=pub, secret_key=sec)

    # Resolve model_id
    model_id = model
    try:
        models = napi.get_models()
        if isinstance(models, dict) and model in models:
            model_id = models[model]
    except Exception as exc:
        logger.warning("Could not fetch models mapping: %s", exc)

    # Check round and idempotency
    try:
        current_round = napi.get_current_round()
    except Exception as exc:
        logger.warning("Could not fetch current round: %s", exc)
        current_round = None

    if check_already_submitted(napi, model_id, current_round):
        logger.info(
            "Predictions already submitted for round %s (model %s). Skipping upload.",
            current_round,
            model,
        )
        return 0

    # Fetch live universe
    universe = fetch_universe(napi)
    if not universe:
        logger.error("Numerai ticker universe is empty")
        return 1

    # Map tickers and drop unmapped
    mapping = build_ticker_mapping(universe)
    yf_tickers = list(mapping.keys())
    if len(yf_tickers) < min_tickers:
        logger.error(
            "Fewer than %d universe tickers mapped to yfinance (found %d)",
            min_tickers,
            len(yf_tickers),
        )
        raise ValueError(f"Refusing to upload under {min_tickers} tickers (found {len(yf_tickers)})")

    # Prices come from our own historic_data (daily bars) -- no yfinance. Only
    # universe tickers we already track with enough fresh history are scored;
    # Numerai accepts a partial universe as long as it has >= min_tickers names.
    if prices is None:
        if data_service is not None:
            prices = data_service.get_yf_data_multiple(yf_tickers, interval="1d", period="2y")
        else:
            prices = load_prices_from_db(yf_tickers)
            yf_tickers = sorted(prices["symbol"].unique())
            logger.info("Scoring %d universe tickers that have fresh DB history", len(yf_tickers))
            if len(yf_tickers) < min_tickers:
                raise ValueError(f"Refusing to upload under {min_tickers} tickers (DB covers {len(yf_tickers)})")

    # Build signal using N0 pure function
    from tradingbot.utils.numerai_signal import build_signal

    signals = build_signal(prices, universe=yf_tickers, asof=asof, min_valid=min_tickers)

    # Map yfinance -> Numerai tickers and drop unmapped
    predictions_df = map_signals_to_numerai(signals, mapping)
    if len(predictions_df) < min_tickers:
        logger.error(
            "Refusing to upload under %d tickers (found %d after mapping)",
            min_tickers,
            len(predictions_df),
        )
        raise ValueError(f"Refusing to upload under {min_tickers} tickers (found {len(predictions_df)})")

    # Write CSV and upload
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        csv_path = f.name
        predictions_df.to_csv(f, index=False)

    try:
        sub_id = napi.upload_predictions(csv_path, model_id=model_id)
        logger.info(
            "Successfully uploaded predictions for model %s (submission ID: %s)",
            model,
            sub_id,
        )
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)

    return 0


def main() -> int:
    """CLI entry point for numerai_signals_submit."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    return submit_signals()


if __name__ == "__main__":
    sys.exit(main())
