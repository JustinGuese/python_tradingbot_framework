# Portfolio Management

> [!WARNING]
> **DISCLAIMER:** This software is for educational and research purposes only. Trading involves significant risk of loss. Any portfolio management actions (Buy/Sell/Rebalance) are strictly at your own risk.

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

## Portfolio Visualization Dashboard

The system includes a web-based dashboard for monitoring bot performance and portfolio metrics in real-time.

### Overview Dashboard

The overview page displays a comprehensive table of all trading bots with key performance metrics:

![Portfolio Overview](../overview.png)

The dashboard shows:
- **Current Worth**: Real-time portfolio value
- **Total Return %**: Overall return since inception
- **Annualized Return %**: Yearly return projection
- **Sharpe Ratio**: Risk-adjusted return metric
- **Sortino Ratio**: Downside risk-adjusted return
- **Max Drawdown %**: Largest peak-to-trough decline
- **Volatility %**: Portfolio price fluctuation
- **Total Trades**: Number of executed trades
- **Start Date**: Bot initialization date

### Bot Detail Page

Clicking on any bot from the overview opens a detailed performance page with:

![Bot Detail Page](../detailpage.png)

The detail page includes:
- **Key Performance Indicators**: Total return, annualized return, Sharpe ratio, Sortino ratio, Calmar ratio, max drawdown, and volatility
- **Portfolio Value Chart**: Time series visualization of portfolio value over time
- **Daily Returns Distribution**: Histogram showing return frequency
- **Monthly Returns Heatmap**: Calendar view of monthly performance
- **Drawdown Chart**: Visual representation of portfolio drawdowns over time
- **Current Holdings Table**: Real-time portfolio composition
- **Trade History**: Complete log of all executed trades with pagination and search

### Accessing the Dashboard

The visualization dashboard is deployed as part of the Helm chart. After deployment, access it via the configured service URL. The dashboard uses HTTP Basic Authentication - credentials are configured via environment variables (`BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD`).

## Next Steps

- [PortfolioManager API](../api/portfolio-manager.md) - Complete API docs
- [Database Models](../architecture/database-models.md) - Data structure
- [Deployment Guide](../deployment/overview.md) - Setup instructions
