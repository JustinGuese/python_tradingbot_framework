# Creating a Bot

Learn how to create your own trading bot in three simple steps.

## Step 1: Create Bot File

Create `tradingbot/{botname}bot.py`:

```python
from tradingbot.utils.botclass import Bot

class MyNewBot(Bot):
    # Optional: Define hyperparameter search space
    param_grid = {
        "rsi_buy": [65, 70, 75],
        "rsi_sell": [25, 30, 35],
    }
    
    def __init__(self, rsi_buy: float = 70.0, rsi_sell: float = 30.0, **kwargs):
        """
        Initialize the bot.
        
        Args:
            rsi_buy: RSI threshold for buy signal
            rsi_sell: RSI threshold for sell signal
            **kwargs: Additional parameters passed to base class
        """
        super().__init__("MyNewBot", "QQQ", interval="1m", period="1d", **kwargs)
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
    
    def decisionFunction(self, row):
        """
        Trading decision based on technical indicators.
        
        Args:
            row: Pandas Series with market data and TA indicators
            
        Returns:
            -1: Sell signal
             0: Hold (no action)
             1: Buy signal
        """
        if row["momentum_rsi"] < self.rsi_buy:
            return 1  # Oversold, buy
        elif row["momentum_rsi"] > self.rsi_sell:
            return -1  # Overbought, sell
        return 0  # Hold

# Entry point
if __name__ == "__main__":
    bot = MyNewBot()
    bot.run()
```

## Step 2: Add to Helm Chart

Edit `helm/tradingbots/values.yaml`:

```yaml
bots:
  - name: mynewbot
    schedule: "*/5 * * * 1-5"  # Every 5 minutes, Mon-Fri
```

**Important**: 
- Filename must be `{name}bot.py` (e.g., `mynewbot.py`)
- Helm automatically uses `{name}.py` as the script filename

## Step 3: Deploy

Deploy using Helm:

```bash
helm upgrade --install tradingbots \
      ./helm/tradingbots \
      --create-namespace \
      --namespace tradingbots-2025
```

## Bot Naming Conventions

- **Bot class name**: `CamelCaseBot` (e.g., `EURUSDTreeBot`)
- **Bot database name**: Same as class name (passed to `super().__init__()`)
- **Filename**: `{name}bot.py` (lowercase, e.g., `eurusdtreebot.py`)
- **Helm name**: `{name}bot` (e.g., `eurusdtreebot`)

## Implementation Approaches

### Simple (Recommended): `decisionFunction()`

For strategies that can be expressed as logic on a single data row:

```python
def decisionFunction(self, row):
    # Access TA indicators via row["indicator_name"]
    if row["momentum_rsi"] < 30:
        return 1
    return 0
```

### Medium Complexity: Override `makeOneIteration()`

For external APIs or custom data processing:

```python
def makeOneIteration(self):
    # Fetch external data
    fear_greed = get_fear_greed_index()
    
    # Custom logic
    if fear_greed < 20:
        self.buy("QQQ")
    return 1
```

### Complex: Portfolio Optimization

For multi-asset strategies:

```python
def makeOneIteration(self):
    # Fetch multiple symbols
    data = self.getYFDataMultiple(["QQQ", "GLD", "TLT"])
    
    # Portfolio optimization
    weights = optimize_portfolio(data)
    self.rebalancePortfolio(weights)
    return 0
```

## Next Steps

- Learn about [Technical Analysis](../guides/technical-analysis.md)
- Explore [Example Bots](../examples/example-bots.md)
- Read the [Bot API Reference](../api/bot.md)
