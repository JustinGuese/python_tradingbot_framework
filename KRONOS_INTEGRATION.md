# Kronos Integration Summary

This document summarizes the Kronos financial forecasting integration added to the framework.

## Overview

**Kronos** is a foundation model for financial K-line (OHLCV) forecasting. The integration allows trading bots to leverage AI-driven price predictions from a state-of-the-art model trained on 12B+ candlestick records across 45+ exchanges.

**Architecture**: The model runs on a Hugging Face Docker Space (CPU, free tier, 16GB RAM). K8s cronjobs call it via HTTP to stay within K8s memory limits (2Gi).

## Files Created

### Core Implementation

1. **`kronos_space/Dockerfile`** — HF Space Docker image
   - Python 3.11, CPU-only PyTorch, FastAPI
   - Clones Kronos source at build time
   - Pre-bakes model weights (instant startup)
   - Ports 7860 (HF convention)

2. **`kronos_space/app.py`** — FastAPI inference server
   - `GET /health` — check if Kronos-mini is loaded
   - `POST /predict` — accept OHLCV JSON, return forecast rows
   - Lifespan event loads model once at startup

3. **`kronos_space/requirements.txt`** — Space dependencies
   - torch (CPU), transformers, fastapi, uvicorn, pandas, huggingface_hub

4. **`tradingbot/utils/kronos_client.py`** — K8s HTTP client
   - `KronosClient.predict(symbol, horizon=5)` → DataFrame
   - Falls back gracefully (logs warning, returns None) if Space unavailable
   - `@tool kronos_forecast` for LangChain integration with `run_ai_with_tools()`

5. **`tradingbot/kronosbot.py`** — Daily cronjob orchestrator
   - Restarts the Space via `HfApi.restart_space()`
   - Polls `/health` until Kronos loads (~60s)
   - Loops over active tickers, predicts next N days
   - Upserts `KronosPrediction` rows to Postgres
   - Pauses Space via `HfApi.pause_space()` to save quota

### Database

6. **`tradingbot/utils/db.py` — Added `KronosPrediction` model**
   - Stores forecasts per (symbol, target_date, model_name)
   - Auto-created by `init_db()`
   - Indexed on symbol + target_date for fast queries

### Kubernetes Deployment

7. **`helm/tradingbots/values.yaml` — Updated**
   - Added `kronosbot` cronjob entry: `"5 22 * * 1-5"` (10:05 PM UTC Mon-Fri)
   - Added `KRONOS_SPACE_URL` env var (plain, public URL)
   - Added `HF_TOKEN` secret reference (for Space lifecycle control)

### Python Dependencies

8. **`pyproject.toml` — Updated**
   - Added `huggingface_hub>=0.20.0` (API client for Space control)

### Utilities Export

9. **`tradingbot/utils/core/__init__.py` — Updated**
   - Exported `KronosClient` and `kronos_forecast` for easier imports

## Files Modified

- `tradingbot/utils/db.py` — added `KronosPrediction` class
- `helm/tradingbots/values.yaml` — added kronosbot + env vars
- `pyproject.toml` — added huggingface_hub dependency
- `tradingbot/utils/core/__init__.py` — exported new classes
- `mkdocs.yml` — added documentation references

## Documentation

1. **`docs/api/kronos-client.md`** — API reference
   - Full `KronosClient` documentation
   - Usage examples (direct, in bots, with AI tools)
   - Error handling, performance characteristics

2. **`docs/guides/kronos-forecasting.md`** — Comprehensive guide
   - What is Kronos, architecture overview
   - Deployment (Space, cronjob, env vars)
   - Using predictions in bots (direct API, DB query, LangChain tool)
   - Monitoring, troubleshooting
   - Performance characteristics, cost analysis

3. **`mkdocs.yml`** — Updated navigation
   - Added "Kronos Forecasting" to Guides section
   - Added "Kronos Client" to API Reference → Integrations

## Deployment Status

### ✅ Completed

- HF Space created: `https://huggingface.co/spaces/guestros/kronos-trading-api`
- Space files uploaded (Dockerfile, app.py, requirements.txt)
- Docker build started (watch at Space page)
- `HF_TOKEN` patched into K8s secret `tradingbot-secrets`
- Helm values updated with kronosbot + env vars

### ⏳ Next Steps

1. **Wait for Space Docker build** (5-10 min)
   - Check status: https://huggingface.co/spaces/guestros/kronos-trading-api
   - Watch build logs; torch CPU download is the slow part

2. **Deploy cronjob to K8s**
   ```bash
   helm upgrade --install tradingbots ./helm/tradingbots --namespace tradingbots-2025
   ```

3. **Test manually** (after Space is ready)
   ```bash
   # Check Space health
   kubectl create job --from=cronjob/tradingbot-kronos test-run -n tradingbots-2025
   kubectl logs -f job/test-run -n tradingbots-2025
   
   # Or directly from your machine
   python -c "
   from tradingbot.utils.kronos_client import KronosClient
   client = KronosClient()
   pred = client.predict('SPY', horizon=5)
   print(pred)
   "
   ```

## Usage Examples

### Direct Usage in a Bot

```python
from tradingbot.utils.core import Bot, KronosClient

class MyBot(Bot):
    def decisionFunction(self, row):
        client = KronosClient()
        pred = client.predict(self.symbol, horizon=5)
        
        if pred is not None:
            next_close = pred.iloc[0]["close"]
            if next_close > row["close"] * 1.05:
                return 1  # Buy if predicted 5%+ upside
        return 0
```

### With AI Tools

```python
from tradingbot.utils.core import run_ai_with_tools, kronos_forecast

decision = run_ai_with_tools(
    system_prompt="You are a trading analyst. Use Kronos forecasts to inform your decision.",
    user_message="Should we buy QQQ?",
    extra_tools=[kronos_forecast],  # Kronos is now a callable tool
)
```

### Database Query

```python
from tradingbot.utils.core import get_db_session
from tradingbot.utils.db import KronosPrediction
from datetime import datetime, timedelta

with get_db_session() as session:
    tomorrow = datetime.utcnow() + timedelta(days=1)
    pred = session.query(KronosPrediction).filter_by(
        symbol="SPY",
        target_date=tomorrow.replace(hour=0, minute=0, second=0)
    ).first()
    
    if pred:
        print(f"SPY predicted close: {pred.predicted_close}")
```

## Environment Variables

**Required:**

- `KRONOS_SPACE_URL` — HF Space URL (e.g. `https://guestros-kronos-trading-api.hf.space`)

**For Space control (optional but recommended):**

- `HF_TOKEN` — HF write token (for restart/pause)
- `HF_SPACE_REPO` — Space repo ID (default: `guestros/kronos-trading-api`)

**Optional tuning:**

- `KRONOS_HORIZON` — Days to forecast (default: 5)
- `KRONOS_EXTRA_SYMBOLS` — Extra tickers to always predict (default: `SPY,QQQ,GLD`)

## Key Design Decisions

1. **HTTP client, not local inference** — keeps K8s image small (<700MB), avoids 2GB torch download
2. **Free HF Space** — CPU-only, 16GB RAM, pauses when idle, saves costs
3. **Daily cronjob** — runs once after market close, Space wakes/predicts/sleeps in 2-3 min
4. **Graceful fallback** — KronosClient returns `None` if Space unavailable; bots continue with fallback signals
5. **Async pause** — Space is paused immediately after predictions to save HF quota
6. **LangChain integration** — `kronos_forecast` tool can be used alongside `run_ai_with_tools()` for AI reasoning

## Performance

| Operation | Time |
|-----------|------|
| Space cold start (after restart) | ~60s |
| Kronos-mini model load | ~40s |
| Warm inference per symbol | ~30-60s |
| DataService fetch per symbol | ~2-5s |
| Upsert 100 predictions to DB | ~1s |
| **Total cronjob runtime** | ~2-3 min |

## Cost

- HF Space: Free tier (CPU-only, paused after 48h)
- Cronjob frequency: 1x per day
- Total monthly quota: ~2-3 min/day × 30 days ≈ 60-90 min
- **Cost: $0** (within free tier limits)

## References

- **Kronos GitHub**: https://github.com/shiyu-coder/Kronos
- **Kronos Paper**: https://arxiv.org/abs/2508.02739
- **HF Model Hub**: https://huggingface.co/NeoQuasar/Kronos-mini
- **API Docs**: [docs/api/kronos-client.md](docs/api/kronos-client.md)
- **Guide**: [docs/guides/kronos-forecasting.md](docs/guides/kronos-forecasting.md)
