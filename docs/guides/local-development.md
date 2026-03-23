# Local Development

Tools and workflows for developing and testing trading bots locally.

## Hyperparameter Tuning

### Define Parameter Grid

```python
class MyBot(Bot):
    param_grid = {
        "rsi_buy": [65, 70, 75],
        "rsi_sell": [25, 30, 35],
        "adx_threshold": [15, 20, 25],
    }
```

### Run Optimization

```python
bot = MyBot()

# Full workflow: optimize + backtest
bot.local_development()

# Or just optimize
results = bot.local_optimize()
print(f"Best params: {results['best_params']}")

# Or just backtest
results = bot.local_backtest()
print(f"Sharpe: {results['sharpe_ratio']:.2f}")
```

## Backtesting

### Simple Backtest

```python
from tradingbot.utils.backtest import backtest_bot

bot = MyBot()
results = backtest_bot(bot, initial_capital=10000.0)

print(f"Yearly Return: {results['yearly_return']:.2%}")
print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
print(f"Max Drawdown: {results['maxdrawdown']:.2%}")
```

### Backtest with Pre-fetched Data

```python
# Fetch data once
data = bot.getYFDataWithTA(saveToDB=True, interval="1m", period="7d")

# Reuse for multiple backtests
results1 = backtest_bot(bot, data=data)
results2 = backtest_bot(another_bot, data=data)
```

## Data Caching

For efficient development:

```python
# First run: fetch and save to DB
data = bot.getYFDataWithTA(saveToDB=True, interval="1m", period="1d")

# Subsequent runs: reuse DB data (much faster)
data = bot.getYFDataWithTA(saveToDB=True, interval="1m", period="1d")
```

## Development Workflow

1. **Create bot** with `decisionFunction()`
2. **Test locally** with `bot.run()`
3. **Optimize parameters** with `bot.local_development()`
4. **Copy best params** into `__init__()` defaults
5. **Deploy** via Helm

## Debugging

### Check Database

```python
from tradingbot.utils.db import get_db_session, Bot, Trade

with get_db_session() as session:
    # Check bot state
    bot = session.query(Bot).filter_by(name="MyBot").first()
    print(bot.portfolio)
    
    # Check trades
    trades = session.query(Trade).filter_by(bot_name="MyBot").all()
    for trade in trades:
        print(f"{trade.symbol}: {trade.quantity} @ {trade.price}")
```

### View Logs

```python
from tradingbot.utils.db import RunLog

with get_db_session() as session:
    logs = session.query(RunLog).filter_by(bot_name="MyBot").all()
    for log in logs[-10:]:  # Last 10 runs
        print(f"{log.start_time}: {log.success} - {log.result}")
```

## QuantStats Report Generation

Backtest results are automatically analyzed and visualized via **QuantStats** — a professional portfolio analysis library. When you backtest, two HTML reports are generated and uploaded to Google Cloud Storage.

**[📊 View Example Report](../examplequantstatsreport.html)** - See what a generated report looks like

### Automatic Report Generation

When `local_backtest()` or `local_development()` completes:

1. **QuantStats generates a rich HTML report** with:
   - Cumulative returns chart
   - Drawdown analysis
   - Monthly/yearly returns heatmap
   - Risk metrics (Sharpe, Sortino, max drawdown)
   - Win rate and trade statistics
   - Comparison vs. buy-and-hold benchmark

2. **Two report variants are created**:
   - `sharpewinner/report.html` — Optimized for Sharpe ratio
   - `yearlyreturnwinner/report.html` — Optimized for yearly return

3. **Reports are uploaded to GCS** (if credentials are configured):
   - Path: `gs://tradingbotrunresults/{BotName}/{metric}/report.html`
   - Example: `gs://tradingbotrunresults/AdaptiveMeanReversionBot/sharpewinner/report.html`

### Configuring GCS Upload

Add to `.env`:

```bash
GCS_ACCESS_KEY_ID=GOOG1E42PG...        # Your GCS HMAC access key
GCS_SECRET_ACCESS_KEY=DEhAhzG6Ok...    # Your GCS HMAC secret
GCS_BUCKET_NAME=tradingbotrunresults   # Cloud Storage bucket name
```

**To get HMAC credentials**:
1. Go to Google Cloud Console → Service Accounts
2. Create or select a service account
3. Create HMAC key (Keys tab → "Create key" → HMAC)
4. Copy Access Key ID and Secret from the JSON

**Without GCS configured**, backtests still run and generate local insights—reports are simply skipped. This is safe for local development.

### What to Look For in Reports

- **Cumulative Returns**: Should show consistent growth or stay positive during drawdowns
- **Monthly Heatmap**: Look for consistency across months (avoid concentrated returns)
- **Sharpe Ratio**: Higher is better; >1.0 is good, >2.0 is excellent
- **Max Drawdown**: How much the strategy lost at its worst point (smaller is better)
- **Win Rate**: Percentage of profitable trades
- **Comparison vs Benchmark**: Did your bot beat buy-and-hold?

## Next Steps

- [Hyperparameter Tuning API](../api/hyperparameter-tuning.md) - Complete API docs
- [Backtesting API](../api/backtest.md) - Backtest details
