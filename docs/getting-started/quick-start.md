# Quick Start

Get your first trading bot up and running in minutes.

## Prerequisites

- Python 3.12+
- PostgreSQL database
- Kubernetes cluster (for deployment)

## Installation

1. Install dependencies:

```bash
uv sync
```

2. Set up environment variables:

```bash
export POSTGRES_URI="postgresql://user:password@host:5432/database"
```

## Running a Bot Locally

### Option 1: Use an Existing Bot

```python
from tradingbot.eurusdtreebot import EURUSDTreeBot

bot = EURUSDTreeBot()
bot.run()
```

### Option 2: Create Your Own Bot

Create `tradingbot/mybot.py`:

```python
from tradingbot.utils.botclass import Bot

class MyBot(Bot):
    def __init__(self):
        super().__init__("MyBot", "QQQ", interval="1m", period="1d")
    
    def decisionFunction(self, row):
        if row["momentum_rsi"] < 30:
            return 1  # Buy - oversold
        elif row["momentum_rsi"] > 70:
            return -1  # Sell - overbought
        return 0  # Hold

if __name__ == "__main__":
    bot = MyBot()
    bot.run()
```

Run it:

```bash
python tradingbot/mybot.py
```

## Local Development Workflow

For hyperparameter tuning and backtesting:

```python
from tradingbot.eurusdtreebot import EURUSDTreeBot

bot = EURUSDTreeBot()

# Optimize hyperparameters and backtest
bot.local_development()

# Or just backtest current parameters
results = bot.local_backtest()
print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
```

## Next Steps

- Learn about [Creating a Bot](creating-a-bot.md)
- Understand the [Bot Class System](../architecture/bot-class-system.md)
- Explore the [API Reference](../api/bot.md)
