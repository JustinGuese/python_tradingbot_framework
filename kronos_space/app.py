"""
Kronos Trading API — HF Docker Space

Loads Kronos-mini at startup and exposes two endpoints:
  GET  /health   → {"status": "ok", "model": "NeoQuasar/Kronos-mini"}
  POST /predict  → accepts OHLCV JSON + horizon, returns forecast rows

Intended to be called by kronosbot.py running in K8s.
The Space is paused between runs to save HF quota.
"""
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Kronos has no PyPI package — source is cloned into /app/Kronos by the Dockerfile
sys.path.insert(0, "/app/Kronos")
from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger(__name__)

_MODEL_CONFIGS = {
    "kronos-mini": {
        "model_id": "NeoQuasar/Kronos-mini",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-2k",
        "context_length": 2048,
    },
    "kronos-small": {
        "model_id": "NeoQuasar/Kronos-small",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "context_length": 512,
    },
}

MODEL_KEY = os.environ.get("KRONOS_MODEL", "kronos-mini")

# Loaded once at startup via lifespan
_predictor: Optional[KronosPredictor] = None
_model_id: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _predictor, _model_id
    cfg = _MODEL_CONFIGS[MODEL_KEY]
    _model_id = cfg["model_id"]
    logger.info(f"Loading {_model_id} + tokenizer {cfg['tokenizer_id']}...")
    t0 = time.time()
    tokenizer = KronosTokenizer.from_pretrained(cfg["tokenizer_id"])
    model = Kronos.from_pretrained(cfg["model_id"])
    _predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=cfg["context_length"])
    logger.info(f"Model ready in {time.time() - t0:.1f}s")
    yield
    _predictor = None


app = FastAPI(title="Kronos Trading API", lifespan=lifespan)


# --- Pydantic schemas ---

class OHLCVRow(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class PredictRequest(BaseModel):
    symbol: str
    horizon: int = 5
    interval: str = "1d"
    ohlcv: List[OHLCVRow]


class PredictionRow(BaseModel):
    target_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class PredictResponse(BaseModel):
    symbol: str
    model: str
    predictions: List[PredictionRow]


# --- Endpoints ---

@app.get("/health")
def health():
    if _predictor is None:
        return {"status": "loading", "model": MODEL_KEY}
    return {"status": "ok", "model": _model_id}


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    if _predictor is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet, retry shortly")
    if len(req.ohlcv) < 50:
        raise HTTPException(status_code=400, detail=f"Need at least 50 OHLCV rows, got {len(req.ohlcv)}")

    # Build DataFrame from request
    df = pd.DataFrame([r.model_dump() for r in req.ohlcv])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    x_timestamp = df["timestamp"].copy()
    x_df = df[["open", "high", "low", "close", "volume"]].copy()

    # Infer step frequency from the actual data so we work for any interval
    deltas = x_timestamp.diff().dropna()
    freq = deltas.median()
    last_ts = x_timestamp.iloc[-1]
    future_ts = pd.date_range(start=last_ts + freq, periods=req.horizon, freq=freq)
    y_timestamp = pd.Series(future_ts, name="timestamp")

    try:
        pred_df = _predictor.predict(
            df=x_df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=req.horizon,
            T=1.0,
            top_p=0.9,
            sample_count=1,
            verbose=False,
        )
    except Exception as exc:
        logger.error(f"Prediction error for {req.symbol}: {exc}")
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}")

    predictions = []
    for ts, (_, row) in zip(future_ts, pred_df.iterrows()):
        predictions.append(PredictionRow(
            target_date=ts.isoformat(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]) if "volume" in row else 0.0,
        ))

    return PredictResponse(symbol=req.symbol, model=_model_id, predictions=predictions)
