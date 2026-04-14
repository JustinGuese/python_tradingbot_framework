# KronosClient API Reference

::: tradingbot.utils.kronos_client.KronosClient
    options:
      show_root_heading: false

## Overview

`KronosClient` is a lightweight HTTP client that calls the Kronos foundation-model forecasting service running on a Hugging Face Docker Space. It fetches clean OHLCV data via `DataService`, posts it to the Space's inference API, and returns a forecast DataFrame.

**Key features:**

- **Stateless HTTP calls** — no model weights in the K8s image (stays under 2Gi memory limit)
- **CPU-only inference** — the Space runs on free HF tier (2 vCPU, 16GB RAM) with CPU-only PyTorch
- **Graceful fallback** — returns `None` and logs warnings on network/model errors; callers degrade gracefully
- **LangChain integration** — the `kronos_forecast` tool can be passed to `run_ai_with_tools(extra_tools=[kronos_forecast])`

## Configuration

Set environment variables:

- **KRONOS_SPACE_URL** (required): Base URL of the HF Space (e.g. `https://guestros-kronos-trading-api.hf.space`)

## Usage

### Basic prediction

```python
from tradingbot.utils.core import KronosClient

client = KronosClient()
pred_df = client.predict("SPY", horizon=5)

if pred_df is not None:
    print(pred_df)
    # DataFrame with columns: target_date, open, high, low, close, volume
```

### In a bot context

```python
from tradingbot.utils.core import Bot

class MyBot(Bot):
    def decisionFunction(self, row):
        # Get Kronos forecast for this bot's symbol
        from tradingbot.utils.core import KronosClient
        
        client = KronosClient()
        forecast = client.predict(self.symbol, horizon=5)
        
        if forecast is not None:
            next_close = forecast.iloc[0]["close"]
            current_close = row["close"]
            
            if next_close > current_close * 1.02:
                return 1  # Buy signal
        
        return 0
```

### With AI tools

Use the `kronos_forecast` LangChain tool in complex AI flows:

```python
from tradingbot.utils.core import Bot, kronos_forecast

class AIBot(Bot):
    def decisionFunction(self, row):
        decision = self.run_ai_with_tools(
            system_prompt="You are a trading analyst. Use all available tools to make a buy/sell decision.",
            user_message=f"Should we buy {self.symbol}? Current close: {row['close']}",
            extra_tools=[kronos_forecast],  # Add Kronos as a tool
        )
        
        if "buy" in decision.lower():
            return 1
        return 0
```

## API Details

### KronosClient.predict()

```python
def predict(
    symbol: str,
    horizon: int = 5,
    interval: str = "1d",
    period: str = "2y",
) -> Optional[pd.DataFrame]
```

**Args:**

- **symbol**: yfinance ticker (e.g. "SPY", "AAPL", "EURUSD=X")
- **horizon**: Number of future bars to predict (default 5 trading days)
- **interval**: OHLCV bar interval: "1m", "5m", "1h", "1d", etc. (passed to DataService)
- **period**: Historical lookback window (default "2y" = ~500 daily bars)

**Returns:**

- **DataFrame** with columns: `target_date`, `open`, `high`, `low`, `close`, `volume` (or `None` on error)
- Rows are indexed 0 to `horizon-1`
- `target_date` is a pandas Timestamp for the predicted date

**Returns `None` if:**

- Space URL not set
- DataService fails to fetch data
- Insufficient data (<50 rows)
- Network error or Space unreachable
- Kronos inference fails

Errors are logged as warnings; callers should check for `None` before using results.

### KronosClient.is_healthy()

```python
def is_healthy(self) -> bool
```

Poll the Space's `/health` endpoint. Used by kronosbot to detect when Kronos-mini has finished loading (~60s after restart).

## Error Handling

The client is designed for graceful degradation. If the Space is unavailable or inference fails:

```python
client = KronosClient()
pred_df = client.predict("SPY")

if pred_df is None:
    # Space unreachable or data insufficient — use fallback signal
    logger.info("Kronos unavailable, using technical analysis instead")
    return technical_signal
else:
    return kronos_signal
```

## Performance

On a free HF Space (CPU-only, 2 vCPU):

- **Cold start** (after Space wakes): ~60 seconds to load Kronos-mini
- **Warm inference** (Space already running): ~30-60 seconds per prediction
- **Data fetch** (DataService): ~2-5 seconds per symbol
- **Total per symbol**: ~35-70 seconds

kronosbot restarts the Space once daily (after market close), so the cold start penalty is paid once per day across all tickers.

## Deployment

See [Kronos Forecasting Service](../guides/kronos-forecasting.md) for deployment instructions.
