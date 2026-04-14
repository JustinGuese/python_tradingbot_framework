# Kronos Financial Forecasting Service

This guide covers the Kronos integration: a foundation model for financial K-line (candlestick) forecasting that predicts future OHLCV prices.

## What is Kronos?

**Kronos** ([NeoQuasar/Kronos-mini](https://huggingface.co/NeoQuasar/Kronos-mini)) is a 4.1M-parameter decoder-only Transformer pre-trained on 12+ billion K-line records from 45+ global exchanges. It forecasts open, high, low, close, and volume for future time periods given historical OHLCV data.

Key facts:

- **Trained on**: 12B+ K-lines across stocks, forex, crypto, commodities from multiple exchanges
- **Architecture**: Specialized financial foundation model (not a generic time-series adapter)
- **Models**: Available in 4 sizes (mini: 4.1M, small: 24.7M, base: 102M, large: 499M parameters)
- **Output**: Predicted OHLCV DataFrame matching input structure
- **Paper**: [Kronos: An Event-Driven Architecture for Autonomous Systems](https://arxiv.org/abs/2508.02739)

## Architecture

The framework splits Kronos inference across two systems to work within hardware constraints:

```
┌──────────────────────────────────────────┐
│ K8s Pod (kronosbot cronjob)              │
│  ├─ Fetch active symbols from DB         │
│  ├─ Call KronosClient (lightweight)      │
│  └─ Upsert predictions to PostgreSQL     │
└────────────┬─────────────────────────────┘
             │
             │ HTTP POST /predict
             │
┌────────────▼─────────────────────────────┐
│ HF Space (guestros/kronos-trading-api)   │
│ Docker Container (CPU-only, 16GB RAM)    │
│  ├─ FastAPI server                       │
│  ├─ Kronos-mini + tokenizer loaded       │
│  ├─ Torch CPU inference                  │
│  └─ Pre-built model weights              │
└──────────────────────────────────────────┘
```

**Why split?**

- K8s pod has strict memory limits (2Gi) — torch + Kronos would exceed this
- HF Spaces free tier offers 16GB RAM and CPU, enough for Kronos-mini
- HTTP separation allows reusability — any service can call the Space

## Deployment

### 1. HF Space (Already Done)

The Space is deployed at `https://huggingface.co/spaces/guestros/kronos-trading-api`.

**Contents:**

- `Dockerfile`: Python 3.11, CPU-only PyTorch, FastAPI
- `app.py`: Two endpoints: `GET /health`, `POST /predict`
- `requirements.txt`: torch, transformers, fastapi, huggingface_hub, pandas
- Model weights pre-baked at build time (instant startup after restart)

**Build status**: Watch logs at [guestros/kronos-trading-api](https://huggingface.co/spaces/guestros/kronos-trading-api)

### 2. K8s Cronjob (kronosbot)

Scheduled at `22:05 UTC Mon-Fri` (right after market close) via Helm values:

```yaml
- name: kronosbot
  schedule: "5 22 * * 1-5"  # 10:05 PM UTC, Monday-Friday
```

**Lifecycle:**

1. Wake the Space: `HfApi.restart_space("guestros/kronos-trading-api")`
2. Wait for Kronos-mini to load: Poll `/health` with 30s retry intervals (max 3 min wait)
3. Predict: Loop over active tickers, call `KronosClient.predict()` for each
4. Store: Upsert `KronosPrediction` rows to Postgres (deduplicated on symbol+target_date)
5. Pause: `HfApi.pause_space(...)` to save HF quota

**Total runtime**: ~2-3 minutes (includes cold start + all predictions)

### 3. Environment Variables

Set in Kubernetes secret or helm values:

**Required:**

- `KRONOS_SPACE_URL`: Base URL of the Space (e.g. `https://guestros-kronos-trading-api.hf.space`)

**For Space lifecycle control (optional but recommended):**

- `HF_TOKEN`: HuggingFace write token (stored in `tradingbot-secrets`)
- `HF_SPACE_REPO`: Space repo ID (default: `guestros/kronos-trading-api`)

**Optional tuning:**

- `KRONOS_HORIZON`: Days ahead to forecast (default: 5)
- `KRONOS_EXTRA_SYMBOLS`: Comma-separated tickers to always forecast (default: `SPY,QQQ,GLD`)

Example helm patch:

```bash
kubectl patch secret tradingbot-secrets -n tradingbots-2025 \
  --type=merge \
  -p '{"stringData":{"HF_TOKEN":"hf_your_write_token_here"}}'
```

## Using Kronos Predictions in Bots

### Direct API Usage

```python
from tradingbot.utils.core import KronosClient

class MyBot(Bot):
    def decisionFunction(self, row):
        client = KronosClient()
        pred = client.predict(self.symbol, horizon=5)
        
        if pred is not None:
            next_close = pred.iloc[0]["close"]
            current = row["close"]
            
            if next_close > current * 1.03:
                return 1  # Buy if model forecasts 3%+ upside
        
        return 0
```

### Query Predictions from Database

Other bots can query the `kronos_predictions` table:

```python
from tradingbot.utils.core import get_db_session
from tradingbot.utils.db import KronosPrediction
from datetime import datetime, timedelta

def get_kronos_signal(symbol, days_ahead=1):
    with get_db_session() as session:
        target_date = datetime.utcnow() + timedelta(days=days_ahead)
        
        pred = (
            session.query(KronosPrediction)
            .filter_by(symbol=symbol)
            .filter(
                KronosPrediction.target_date >= target_date.replace(hour=0, minute=0, second=0),
                KronosPrediction.target_date < target_date.replace(hour=23, minute=59, second=59)
            )
            .order_by(KronosPrediction.prediction_made_at.desc())
            .first()
        )
        
        if pred:
            # Compare predicted close to current close
            return pred.predicted_close
        return None
```

### LangChain Tool Integration

Use Kronos as a tool in AI flows:

```python
from tradingbot.utils.core import run_ai_with_tools, kronos_forecast

decision = run_ai_with_tools(
    system_prompt="Analyze the symbol and decide buy/hold/sell.",
    user_message="Should we buy SPY right now?",
    extra_tools=[kronos_forecast],  # Add Kronos
)
# AI can now call kronos_forecast("SPY") as a tool when reasoning
```

## Monitoring & Troubleshooting

### Check Space Status

```bash
python -c "
from tradingbot.utils.kronos_client import KronosClient
client = KronosClient()
print('Space healthy:', client.is_healthy())
"
```

### Manual Prediction Test

```bash
python -c "
from tradingbot.utils.kronos_client import KronosClient
client = KronosClient()
pred = client.predict('SPY', horizon=5)
if pred is not None:
    print(pred)
else:
    print('Prediction failed (Space unreachable or data insufficient)')
"
```

### Check Cronjob Logs

```bash
# Most recent job
kubectl logs -l batch.kubernetes.io/job-name=tradingbot-kronos-xxx -n tradingbots-2025 --tail=100

# All kronosbot jobs
kubectl logs -l app=tradingbot,cronjob=kronosbot -n tradingbots-2025
```

### Database Query

```bash
# Latest predictions
kubectl exec -n tradingbots-2025 $(kubectl get pods -n tradingbots-2025 -l app=psql -o name | head -1) -- \
  psql -U postgres -c "
    SELECT symbol, target_date, predicted_close, prediction_made_at
    FROM kronos_predictions
    ORDER BY prediction_made_at DESC
    LIMIT 20;
  "
```

### Common Issues

**"Space did not become healthy within retry window"**

→ Space is still building (Dockerfile running). Wait 5-10 min for Docker build to complete. Check build logs at [huggingface.co/spaces/guestros/kronos-trading-api](https://huggingface.co/spaces/guestros/kronos-trading-api).

**"Request timeout (>120s)"**

→ Space is cold (just woke up). kronosbot retries up to 6 times with 30s delays. If it times out, increase `_PREDICT_TIMEOUT` in `tradingbot/kronosbot.py`.

**"Insufficient data (<50 rows)"**

→ yfinance returned less than 50 bars for the symbol. Use a longer `period` parameter (default "2y") or check if the ticker is valid.

**"KRONOS_SPACE_URL not set"**

→ Set `KRONOS_SPACE_URL` environment variable in helm values or K8s secret.

## Performance Characteristics

### Latency

| Operation | Time |
|-----------|------|
| Space cold start (after restart) | ~60s |
| Model load (Kronos-mini) | ~40s |
| Warm inference per symbol | ~30-60s |
| DataService fetch per symbol | ~2-5s |
| DB upsert (100 predictions) | ~1s |

### Space Quota

- Cronjob runs once per day (~2-3 min runtime)
- Space paused after each run
- Free HF tier quota should easily cover this

### Memory

- K8s pod: stays under 500 MB (no torch)
- HF Space: ~5GB (torch + Kronos-mini loaded)

## Cost & Limits

**HF Spaces:**

- **Free tier**: CPU-only, 2 vCPU, 16GB RAM, paused after 48h inactivity
- **Pro tier** ($15/month): Always running, same resources
- **Cost for this setup**: Free (Space only runs ~2-3 min/day)

**API calls:**

- No rate limits on the Space itself (it's your own)
- HTTP client has default 120s timeout (configurable)

## Advanced Configuration

### Use a Larger Model

To use Kronos-small (24.7M params, better accuracy):

1. Edit `kronos_space/app.py`: change `MODEL_KEY = "kronos-mini"` → `"kronos-small"`
2. Rebuild Space: git push to the Space repo or trigger rebuild manually
3. Expect longer inference times (~60-90s per symbol on CPU)

### Add Custom Tickers

In helm values, set `KRONOS_EXTRA_SYMBOLS`:

```yaml
env:
  - name: KRONOS_EXTRA_SYMBOLS
    value: "SPY,QQQ,GLD,BTC/USD,EUR/USD"
```

kronosbot will predict all active bot tickers plus these extras.

### Change Forecast Horizon

In helm values:

```yaml
env:
  - name: KRONOS_HORIZON
    value: "10"  # Predict 10 days ahead instead of 5
```

## References

- **Kronos Paper**: [arxiv.org/abs/2508.02739](https://arxiv.org/abs/2508.02739)
- **HF Model Hub**: [NeoQuasar/Kronos-mini](https://huggingface.co/NeoQuasar/Kronos-mini), [Kronos-small](https://huggingface.co/NeoQuasar/Kronos-small)
- **GitHub**: [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos)
- **KronosClient API**: [api/kronos-client.md](../api/kronos-client.md)
