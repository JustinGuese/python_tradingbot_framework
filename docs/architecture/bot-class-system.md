# Bot Class System

> [!WARNING]
> **DISCLAIMER:** This software is for educational and research purposes only. Trading involves significant risk of loss. Use of this framework for live trading is strictly at your own risk.

The `Bot` class is the foundation of the trading bot system. All bots inherit from it and implement trading strategies.

## Implementation Approaches

### 1a. Simple (Recommended): Single-ticker `decisionFunction(row)`

**When to use**: Your strategy can be expressed as logic on a single data row with technical indicators, for one symbol.

**How it works**:
- Base class fetches data for `self.symbol` (or `self.tickers` for multi-asset bots)
- Applies your function to each row
- Averages the last N decisions
- Executes trades automatically
- Fully backtestable via `local_backtest()` / `local_optimize()`

**Example**:
```python
class MyBot(Bot):
    def __init__(self):
        super().__init__("MyBot", symbol="QQQ", interval="1d", period="1y")

    def decisionFunction(self, row):
        if row["momentum_rsi"] < 30:
            return 1  # Buy
        elif row["momentum_rsi"] > 70:
            return -1  # Sell
        return 0  # Hold
```

### 1b. Multi-ticker `decisionFunction(row)` — equal-weight

**When to use**: Same row-by-row logic applied across a basket of tickers with equal-weight sizing.

**How it works**:
- Pass `tickers=["SPY", "QQQ", "GLD"]` to `__init__` instead of `symbol=`
- `decisionFunction` is called once per ticker per bar (unchanged signature)
- Buy signal: top up that ticker toward `total_value / N`
- Sell signal: liquidate that ticker's position
- Live trading: `makeOneIteration()` routes to `_run_multi_ticker_iteration()` automatically
- Backtesting: `backtest_bot()` inner-joins all ticker DataFrames on timestamp and simulates equal-weight

**Example**:
```python
class MyMultiBot(Bot):
    def __init__(self):
        super().__init__("MyMultiBot", tickers=["SPY", "QQQ", "GLD"], interval="1d", period="1y")

    def decisionFunction(self, row):
        if row["momentum_rsi"] < 30:
            return 1
        elif row["momentum_rsi"] > 70:
            return -1
        return 0
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

# Sell all holdings (clamps at zero, never goes short)
bot.sell(symbol="QQQ")

# Short specific amount (opted-in bots only, whole shares)
bot.short(symbol="QQQ", quantityUSD=1000)

# Cover short position (-1 covers full position)
bot.cover(symbol="QQQ", quantityUSD=-1)

# Rebalance portfolio (supports signed weights when ALLOW_SHORT=True)
bot.rebalancePortfolio({"QQQ": 0.8, "GLD": 0.1, "USD": 0.1})
```

## Short Selling Controls

The `Bot` base class provides class-level controls for strategies utilizing short selling or market-neutral hedging:

```python
class MyHedgedBot(Bot):
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 2.0
    SHORTABLE: ClassVar[set[str] | None] = {"QQQ", "SPY"}
```

- **`ALLOW_SHORT: ClassVar[bool] = False`**:
  Guard flag. Defaults to `False` so all existing bots remain strictly long-only. When `False`, any negative weight passed to `targetWeights` is clamped to `0.0` (treated as an exit signal), and any attempt to cross zero into a short position raises an exception. Opted-in bots set `ALLOW_SHORT = True` to enable short execution and negative target weights.
- **`MAX_GROSS: ClassVar[float] = 1.0`**:
  Gross leverage ceiling:
  $$\text{gross} = \frac{\text{long\_value} + \text{short\_value}}{\text{equity}} \le \text{MAX\_GROSS}$$
  Long-only bots default to 1.0. A dollar-neutral hedge (e.g., 100% long stocks + 100% short index ETF) sets `MAX_GROSS = 2.0`. If a bot's `targetWeights` produce a gross exposure exceeding `MAX_GROSS`, weights are automatically scaled down proportionally.
- **`SHORTABLE: ClassVar[set[str] | list[str] | tuple[str, ...] | None] = None`**:
  Allowlist of shortable symbols. When set to `None`, defaults to `tradingbot.utils.config.SHORTABLE` (liquid ETFs and S&P 100 components). Symbols outside this allowlist cannot be shorted.

## Signed Target Weights

Strategies using `targetWeights` or `rebalancePortfolio()` can express directional hedges or short legs using signed weights when `ALLOW_SHORT = True`:

```python
weights = {
    "AAPL": 0.50,  # 50% long
    "MSFT": 0.50,  # 50% long
    "QQQ": -1.00,  # 100% short index hedge
    "USD": 1.00,  # 100% cash (proceeds + initial capital)
}
# Signed sum: 0.50 + 0.50 - 1.00 + 1.00 = 1.0 (normalized)
# Gross exposure: |0.50| + |0.50| + |-1.00| = 2.0 (fits MAX_GROSS=2.0)
```

1. **Signed Normalization**: The signed sum of weights (including cash `"USD"`) must equal `1.0` within `WEIGHT_SUM_TOLERANCE` (`require_normalized`).
2. **Gross Validation**: If `ALLOW_SHORT = True`, total gross exposure $\sum |w_i|$ is validated against `MAX_GROSS`.
3. **Atomic Execution**: `rebalance_portfolio` executes position reductions and covers before expansions, ensuring capital is freed prior to entering new long or short positions.

### Portfolio Access

```python
cash = bot.dbBot.portfolio.get("USD", 0)
holding = bot.dbBot.portfolio.get("QQQ", 0)
```

## Data Caching

- **Instance cache**: `self.data` caches last fetched DataFrame (for single-ticker). For multi-ticker bots, `self.datas` is a dictionary caching DataFrames keyed by ticker.
- **Database persistence**: Set `saveToDB=True` for cross-run data reuse
- **Automatic freshness check**: Stale data (>10 minutes) is refetched

## Next Steps

- [Bot API Reference](../api/bot.md) - Complete method documentation
- [Creating a Bot](../getting-started/creating-a-bot.md) - Step-by-step guide
