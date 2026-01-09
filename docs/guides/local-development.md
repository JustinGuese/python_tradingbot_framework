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

## Next Steps

- [Hyperparameter Tuning API](../api/hyperparameter-tuning.md) - Complete API docs
- [Backtesting API](../api/backtest.md) - Backtest details
