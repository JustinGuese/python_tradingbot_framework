# Portfolio Management

The system provides built-in portfolio management with automatic trade logging.

## Portfolio Structure

Portfolio is stored as a JSON dictionary:

```python
portfolio = {
    "USD": 10000.0,      # Cash
    "QQQ": 5.5,          # Holdings (quantity, not value)
    "GLD": 10.0,         # More holdings
}
```

**Important**: Holdings are stored as quantities, not dollar values.

## Trading Operations

### Buy

```python
# Buy with all available cash
bot.buy(symbol="QQQ")

# Buy specific USD amount
bot.buy(symbol="QQQ", quantityUSD=1000)
```

### Sell

```python
# Sell all holdings
bot.sell(symbol="QQQ")

# Sell specific USD amount
bot.sell(symbol="QQQ", quantityUSD=500)
```

### Rebalance

```python
# Rebalance to target weights
bot.rebalancePortfolio({
    "QQQ": 0.8,   # 80% in QQQ
    "GLD": 0.1,   # 10% in GLD
    "USD": 0.1    # 10% cash
})

# Filter out small positions (< $50)
bot.rebalancePortfolio(
    {"QQQ": 0.8, "GLD": 0.1, "USD": 0.1},
    onlyOver50USD=True
)
```

## Accessing Portfolio

```python
# Get cash
cash = bot.dbBot.portfolio.get("USD", 0)

# Get holdings
holding = bot.dbBot.portfolio.get("QQQ", 0)

# Iterate all holdings
for symbol, quantity in bot.dbBot.portfolio.items():
    if symbol != "USD":
        print(f"{symbol}: {quantity}")
```

## Automatic Trade Logging

All buy/sell operations automatically:
- Update portfolio in database
- Log trade to `trades` table
- Calculate profit (for sells)
- Refresh bot state

## Portfolio Worth

Calculate current portfolio value:

```python
from tradingbot.utils.portfolio_worth_calculator import calculate_portfolio_worth

worth = calculate_portfolio_worth(bot.dbBot, bot._data_service)
print(f"Portfolio worth: ${worth:,.2f}")
```

## Next Steps

- [PortfolioManager API](../api/portfolio-manager.md) - Complete API docs
- [Database Models](../architecture/database-models.md) - Data structure
