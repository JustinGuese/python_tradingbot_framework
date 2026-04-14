"""
kronosbot — Daily Kronos OHLCV forecast cronjob.

Runs once per day after market close (22:05 UTC Mon-Fri).

Lifecycle:
  1. Wake the HF Space via HfApi.restart_space()
  2. Poll /health with retries until Kronos-mini finishes loading (~60s)
  3. Predict the next KRONOS_HORIZON trading days for each active ticker
  4. Upsert KronosPrediction rows into Postgres (deduplicated on symbol+target_date+model)
  5. Pause the Space immediately to save HF quota (~2-3 min total runtime)

Active tickers are collected from:
  - Non-USD keys in every BotModel.portfolio JSON (live holdings)
  - The KRONOS_EXTRA_SYMBOLS env var (comma-separated, default "SPY,QQQ,GLD")

Environment variables:
  KRONOS_SPACE_URL      Space base URL (e.g. https://guestros-kronos-trading-api.hf.space)
  HF_TOKEN              HuggingFace token with write access to the Space repo
  HF_SPACE_REPO         HF repo id of the Space (default guestros/kronos-trading-api)
  KRONOS_HORIZON        Days ahead to forecast (default 5)
  KRONOS_EXTRA_SYMBOLS  Comma-separated extra tickers to always forecast
"""
import logging
import os
import time

from tradingbot.utils.core import BotModel, get_db_session, init_db, setup_logging
from tradingbot.utils.db import KronosPrediction
from tradingbot.utils.kronos_client import KronosClient

setup_logging()
logger = logging.getLogger(__name__)

# --- Config from env ---
SPACE_URL = os.environ.get("KRONOS_SPACE_URL", "https://guestros-kronos-trading-api.hf.space")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_SPACE_REPO = os.environ.get("HF_SPACE_REPO", "guestros/kronos-trading-api")
HORIZON = int(os.environ.get("KRONOS_HORIZON", "5"))
EXTRA_SYMBOLS = [
    s.strip() for s in os.environ.get("KRONOS_EXTRA_SYMBOLS", "SPY,QQQ,GLD").split(",") if s.strip()
]

HEALTH_RETRIES = 6
HEALTH_RETRY_DELAY = 30  # seconds between health checks (total wait ≤ 3 min)


def _get_active_symbols() -> list[str]:
    """Collect unique non-USD tickers from all bot portfolio JSON fields."""
    symbols: set[str] = set()
    try:
        with get_db_session() as session:
            for bot in session.query(BotModel).all():
                if bot.portfolio:
                    for key in bot.portfolio:
                        # Portfolio keys are asset symbols; skip cash and unexpected values
                        if isinstance(key, str) and key != "USD" and 1 <= len(key) <= 12:
                            symbols.add(key)
    except Exception as exc:
        logger.warning(f"Could not query bot portfolios: {exc}")
    symbols.update(EXTRA_SYMBOLS)
    return sorted(symbols)


def _wait_for_space(client: KronosClient) -> bool:
    """Poll /health until the Space is warm. Returns True if healthy within retries."""
    for attempt in range(1, HEALTH_RETRIES + 1):
        if client.is_healthy():
            logger.info("Space is healthy and ready")
            return True
        logger.info(f"Waiting for Space warmup... ({attempt}/{HEALTH_RETRIES})")
        time.sleep(HEALTH_RETRY_DELAY)
    return False


def _upsert_predictions(predictions: list) -> None:
    """Write predictions to Postgres, updating existing rows by (symbol, target_date, model_name)."""
    with get_db_session() as session:
        for pred in predictions:
            existing = (
                session.query(KronosPrediction)
                .filter_by(symbol=pred.symbol, target_date=pred.target_date, model_name=pred.model_name)
                .first()
            )
            if existing:
                existing.predicted_open = pred.predicted_open
                existing.predicted_high = pred.predicted_high
                existing.predicted_low = pred.predicted_low
                existing.predicted_close = pred.predicted_close
                existing.predicted_volume = pred.predicted_volume
                existing.prediction_made_at = pred.prediction_made_at
                existing.horizon_days = pred.horizon_days
            else:
                session.add(pred)
    logger.info(f"Upserted {len(predictions)} prediction rows")


def main() -> None:
    init_db()
    client = KronosClient(space_url=SPACE_URL)

    # Lazy import so the K8s image doesn't need huggingface_hub at module level
    # (it's added to pyproject.toml as a dep, but import-time errors are friendlier this way)
    api = None
    if HF_TOKEN:
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=HF_TOKEN)
        except ImportError:
            logger.warning("huggingface_hub not installed — Space lifecycle management disabled")

    # 1. Wake the Space
    if api:
        logger.info(f"Restarting HF Space {HF_SPACE_REPO}...")
        try:
            api.restart_space(HF_SPACE_REPO)
        except Exception as exc:
            logger.warning(f"restart_space call failed (Space may already be running): {exc}")

    # 2. Wait for Kronos model to load
    if not _wait_for_space(client):
        logger.error("Space did not become healthy within the retry window — aborting")
        if api:
            try:
                api.pause_space(HF_SPACE_REPO)
            except Exception:
                pass
        return

    # 3. Run predictions
    symbols = _get_active_symbols()
    logger.info(f"Predicting {HORIZON} days ahead for {len(symbols)} symbols: {symbols}")

    all_predictions = []
    import pandas as pd
    made_at = pd.Timestamp.utcnow().to_pydatetime()

    for symbol in symbols:
        logger.info(f"  Predicting {symbol}...")
        pred_df = client.predict(symbol, horizon=HORIZON)
        if pred_df is None:
            logger.warning(f"  Prediction failed for {symbol}, skipping")
            continue

        for _, row in pred_df.iterrows():
            all_predictions.append(KronosPrediction(
                symbol=symbol,
                model_name="NeoQuasar/Kronos-mini",
                interval="1d",
                prediction_made_at=made_at,
                target_date=row["target_date"].to_pydatetime(),
                predicted_open=float(row["open"]),
                predicted_high=float(row["high"]),
                predicted_low=float(row["low"]),
                predicted_close=float(row["close"]),
                predicted_volume=float(row.get("volume", 0) or 0),
                horizon_days=HORIZON,
            ))

    if all_predictions:
        _upsert_predictions(all_predictions)
    else:
        logger.warning("No predictions were generated")

    # 4. Pause the Space to save HF quota
    if api:
        logger.info("Pausing HF Space...")
        try:
            api.pause_space(HF_SPACE_REPO)
            logger.info("Space paused")
        except Exception as exc:
            logger.warning(f"pause_space failed: {exc}")

    logger.info("kronosbot complete")


if __name__ == "__main__":
    main()
