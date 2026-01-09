# Quick Start

Get your first trading bot up and running locally in minutes. This guide covers local development, testing, and running bots live.

## Prerequisites

- Python 3.12+
- Docker (for local PostgreSQL)

## Step 1: Set Up PostgreSQL

The trading bot system requires a PostgreSQL database to store bot state, trades, and historical data.

### Run PostgreSQL with Docker

Start a PostgreSQL container:

```bash
docker run -d --name postgres-tradingbot \
  -e POSTGRES_PASSWORD=yourpassword \
  -e POSTGRES_DB=tradingbot \
  -p 5432:5432 \
  postgres:17-alpine
```

### Set Environment Variable

Configure the database connection:

```bash
export POSTGRES_URI="postgresql://postgres:yourpassword@localhost:5432/tradingbot"
```

**Note**: For production deployment, see the [Deployment Guide](../deployment/overview.md) for Kubernetes/Helm setup.

## Step 2: Install Dependencies

Install project dependencies using `uv`:

```bash
uv sync
```

## Step 3: Write Your Own Bot

The trading bot framework supports three abstraction levels, depending on your strategy complexity.

### Simple: `decisionFunction(row)` (Recommended)

For strategies that can be expressed as logic on a single data row with technical indicators.

**How it works**: The base class fetches data, applies your function to each row, averages the decisions, and executes trades automatically.

**Example**:

```python
from tradingbot.utils.botclass import Bot

class MyBot(Bot):
    def __init__(self):
        super().__init__("MyBot", "QQQ", interval="1m", period="1d")
    
    def decisionFunction(self, row):
        """
        Trading decision based on technical indicators.
        
        Returns:
            -1: Sell signal
             0: Hold (no action)
             1: Buy signal
        """
        # Access 150+ technical indicators via row["indicator_name"]
        if row["momentum_rsi"] < 30:
            return 1  # Buy - oversold
        elif row["momentum_rsi"] > 70:
            return -1  # Sell - overbought
        return 0  # Hold
```

**Available Indicators**: After calling `getYFDataWithTA()`, you have access to ~150+ indicators:
- Momentum: `momentum_rsi`, `momentum_stoch`, `momentum_roc`, etc.
- Trend: `trend_macd`, `trend_adx`, `trend_sma_fast`, etc.
- Volatility: `volatility_bbh`, `volatility_bbl`, `volatility_atr`, etc.
- Volume: `volume_obv`, `volume_mfi`, `volume_vwap`, etc.

See [Technical Analysis Guide](../guides/technical-analysis.md) for the complete list.

### Medium Complexity: Override `makeOneIteration()`

For bots that need external APIs, custom data processing, or different timeframe handling.

**Example** (using external Fear & Greed Index API):

```python
import fear_and_greed
from tradingbot.utils.botclass import Bot

class FearGreedBot(Bot):
    def __init__(self):
        super().__init__("FearGreedBot", "QQQ")
    
    def makeOneIteration(self):
        """
        Custom iteration logic with external API.
        
        Returns:
            -1: Sell signal
             0: Hold
             1: Buy signal
        """
        # Fetch external data
        fear_greed_index = fear_and_greed.get().value
        
        cash = self.dbBot.portfolio.get("USD", 0)
        holding = self.dbBot.portfolio.get("QQQ", 0)
        
        if fear_greed_index >= 70 and cash > 0:
            self.buy("QQQ")  # Extreme greed - buy
            return 1
        elif fear_greed_index <= 30 and holding > 0:
            self.sell("QQQ")  # Extreme fear - sell
            return -1
        return 0  # Hold
```

### Complex: Portfolio Optimization

For multi-asset strategies, portfolio rebalancing, or complex optimization algorithms.

**Example** (Sharpe ratio portfolio optimization):

```python
from pypfopt import EfficientFrontier, expected_returns, risk_models
from tradingbot.utils.botclass import Bot

class PortfolioBot(Bot):
    def __init__(self):
        super().__init__("PortfolioBot", symbol=None)  # Multi-asset bot
        self.symbols = ["QQQ", "GLD", "TLT", "AAPL"]
    
    def makeOneIteration(self):
        """
        Rebalance portfolio using optimization.
        
        Returns:
            0: Rebalancing completed
        """
        # Fetch data for multiple symbols
        data = self.getYFDataMultiple(
            self.symbols, 
            interval="1d", 
            period="3mo", 
            saveToDB=True
        )
        
        # Convert to wide format for optimization
        df = self.convertToWideFormat(data, value_column="close")
        
        # Calculate optimal weights
        mu = expected_returns.mean_historical_return(df)
        S = risk_models.sample_cov(df)
        ef = EfficientFrontier(mu, S)
        ef.max_sharpe()
        weights = ef.clean_weights()
        
        # Rebalance portfolio
        self.rebalancePortfolio(weights)
        return 0
```

## Step 4: Local Development Workflow

Before running your bot live, test and optimize it locally.

### Backtesting

Test your bot's performance on historical data:

```python
from tradingbot.utils.botclass import Bot

bot = MyBot()

# Run backtest with current parameters
results = bot.local_backtest(initial_capital=10000.0)

print(f"Yearly Return: {results['yearly_return']:.2%}")
print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
print(f"Max Drawdown: {results['maxdrawdown']:.2%}")
print(f"Number of Trades: {results['nrtrades']}")
```

### Hyperparameter Tuning (Optional)

Optimize your bot's parameters automatically:

```python
class MyBot(Bot):
    # Define hyperparameter search space
    param_grid = {
        "rsi_buy": [65, 70, 75],
        "rsi_sell": [25, 30, 35],
        "adx_threshold": [15, 20, 25],
    }
    
    def __init__(self, rsi_buy=70.0, rsi_sell=30.0, adx_threshold=20.0, **kwargs):
        super().__init__("MyBot", "QQQ", interval="1m", period="1d", **kwargs)
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        self.adx_threshold = adx_threshold
    
    def decisionFunction(self, row):
        if row["momentum_rsi"] < self.rsi_buy:
            return 1
        elif row["momentum_rsi"] > self.rsi_sell:
            return -1
        return 0

# Optimize and backtest
bot = MyBot()
bot.local_development()
# Prints best parameters in copy-paste format
# Then backtests with those parameters
# Copy the printed parameters into __init__ defaults
```

**Key Features**:
- **Data pre-fetching**: Historical data is fetched once and reused for all parameter combinations (dramatically faster)
- **Database caching**: Data is saved to DB on first fetch, subsequent runs reuse cached data
- **Parallel execution**: Uses multiple CPU cores by default
- **Automatic period adjustment**: For minute-level intervals, automatically uses 7 days instead of 1 year (respects Yahoo Finance limits)

**Methods**:
- `bot.local_development()` - Full workflow: optimize + backtest
- `bot.local_optimize()` - Just optimize parameters
- `bot.local_backtest()` - Just backtest current parameters

## Step 5: Run Your Bot Live

Once you're satisfied with backtesting results, run your bot live:

```python
from tradingbot.utils.botclass import Bot

bot = MyBot()
bot.run()  # Executes one iteration, makes trades, logs to database
```

**What `bot.run()` does**:
1. Calls `makeOneIteration()` (or uses default implementation with `decisionFunction()`)
2. Executes buy/sell operations based on the decision
3. Updates portfolio in the database
4. Logs the result to `RunLog` table
5. Handles errors gracefully (logs to database before re-raising)

**Complete Example**:

```python
from tradingbot.utils.botclass import Bot

class MyBot(Bot):
    def __init__(self):
        super().__init__("MyBot", "QQQ", interval="1m", period="1d")
    
    def decisionFunction(self, row):
        if row["momentum_rsi"] < 30:
            return 1
        elif row["momentum_rsi"] > 70:
            return -1
        return 0

if __name__ == "__main__":
    bot = MyBot()
    
    # Optional: Test locally first
    # bot.local_backtest()
    # bot.local_development()  # If you have param_grid defined
    
    # Run live
    bot.run()
```

Save this to `tradingbot/mybot.py` and run:

```bash
python tradingbot/mybot.py
```

## Next Steps

- **Deployment**: Learn how to deploy bots to Kubernetes with Helm in the [Deployment Guide](../deployment/overview.md)
- **Creating Bots**: See detailed bot creation patterns in [Creating a Bot](creating-a-bot.md)
- **Architecture**: Understand the [Bot Class System](../architecture/bot-class-system.md)
- **API Reference**: Explore the complete [Bot API Reference](../api/bot.md)
- **Examples**: Check out [Example Bots](../examples/example-bots.md) for more inspiration
