# Bot Class System

The `Bot` class is the foundation of the trading bot system. All bots inherit from it and implement trading strategies.

## Implementation Approaches

### 1. Simple (Recommended): Implement `decisionFunction(row)`

**When to use**: Your strategy can be expressed as logic on a single data row with technical indicators.

**How it works**: 
- Base class fetches data
- Applies your function to each row
- Averages the last N decisions
- Executes trades automatically

**Example**:
```python
class MyBot(Bot):
    def decisionFunction(self, row):
        if row["momentum_rsi"] < 30:
            return 1  # Buy
        elif row["momentum_rsi"] > 70:
            return -1  # Sell
        return 0  # Hold
```

### 2. Medium Complexity: Override `makeOneIteration()`

**When to use**: 
- External APIs (e.g., Fear & Greed Index)
- Custom data processing
- Different timeframe handling

**Example**: See `feargreedbot.py`

### 3. Complex: Portfolio Optimization

**When to use**:
- Multi-asset strategies
- Portfolio rebalancing
- Complex optimization algorithms

**Example**: See `sharpeportfoliooptweekly.py`

## Bot Lifecycle

```
1. Bot.__init__(name, symbol, interval="1m", period="1d")
   ├── Creates/retrieves bot from database
   ├── Initializes portfolio with {"USD": 10000} if new
   ├── Sets up symbol and data cache
   └── Stores interval and period for data fetching

2. Bot.run()
   ├── Calls makeOneIteration()
   ├── Executes buy/sell based on decision
   └── Logs result to database (RunLog)

3. Bot.makeOneIteration() [default implementation]
   ├── Fetches data: getYFDataWithTA(saveToDB=True, ...)
   ├── Gets decision: getLatestDecision(data)
   └── Executes trade if decision != 0
```

## Key Methods

### Data Fetching

```python
# Raw market data
data = bot.getYFData(interval="1m", period="1d", saveToDB=True)

# With technical indicators
data = bot.getYFDataWithTA(interval="1m", period="1d", saveToDB=True)
```

### Trading Operations

```python
# Buy with all cash
bot.buy(symbol="QQQ")

# Buy specific amount
bot.buy(symbol="QQQ", quantityUSD=1000)

# Sell all holdings
bot.sell(symbol="QQQ")

# Rebalance portfolio
bot.rebalancePortfolio({"QQQ": 0.8, "GLD": 0.1, "USD": 0.1})
```

### Portfolio Access

```python
cash = bot.dbBot.portfolio.get("USD", 0)
holding = bot.dbBot.portfolio.get("QQQ", 0)
```

## Data Caching

- **Instance cache**: `self.data` caches last fetched DataFrame
- **Database persistence**: Set `saveToDB=True` for cross-run data reuse
- **Automatic freshness check**: Stale data (>10 minutes) is refetched

## Next Steps

- [Bot API Reference](../api/bot.md) - Complete method documentation
- [Creating a Bot](../getting-started/creating-a-bot.md) - Step-by-step guide
