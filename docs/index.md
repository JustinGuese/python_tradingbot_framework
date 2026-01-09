# Trading Bot System

Welcome to the Trading Bot System documentation!

This system provides a framework for building and deploying automated trading bots that:

- Fetch market data from Yahoo Finance
- Apply technical analysis indicators
- Make trading decisions based on configurable strategies
- Manage portfolios and track trades in PostgreSQL
- Run on configurable schedules via Kubernetes CronJobs

## Quick Links

- [Quick Start Guide](getting-started/quick-start.md) - Get up and running in minutes
- [Creating a Bot](getting-started/creating-a-bot.md) - Learn how to build your first bot
- [Bot Class System](architecture/bot-class-system.md) - Understand the core architecture
- [API Reference](api/bot.md) - Complete API documentation

## Key Features

### Simple Bot Creation

Create a trading bot by simply implementing a `decisionFunction()`:

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
```

### Technical Analysis

Access 150+ technical indicators automatically:

- Trend indicators (MACD, SMA, EMA, ADX, Ichimoku, etc.)
- Momentum indicators (RSI, Stochastic, ROC, etc.)
- Volatility indicators (Bollinger Bands, ATR, Keltner Channels, etc.)
- Volume indicators (OBV, CMF, MFI, etc.)

### Portfolio Management

Built-in portfolio management with automatic trade logging:

```python
bot.buy(symbol="QQQ", quantityUSD=1000)
bot.sell(symbol="QQQ", quantityUSD=500)
bot.rebalancePortfolio({"QQQ": 0.8, "GLD": 0.1, "USD": 0.1})
```

### Hyperparameter Tuning

Optimize your bot's parameters automatically:

```python
bot = MyBot()
bot.local_development()  # Optimize and backtest
```

### Deployment

Deploy to Kubernetes with Helm:

```bash
helm upgrade --install tradingbots ./helm/tradingbots \
  --create-namespace --namespace tradingbots-2025
```

## Documentation Structure

- **Getting Started**: Installation and quick start guides
- **Architecture**: System design and core concepts
- **API Reference**: Complete API documentation with examples
- **Deployment**: Kubernetes and Helm deployment guides
- **Guides**: In-depth tutorials and best practices
- **Examples**: Real-world bot implementations

## Need Help?

- Check the [LLM_README.md](../LLM_README.md) for detailed developer documentation
- Review the [Example Bots](examples/example-bots.md) for implementation patterns
- See the [Common Pitfalls](../README.md#common-pitfalls) section in the README
