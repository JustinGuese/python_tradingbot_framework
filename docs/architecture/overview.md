# Architecture Overview

## System Components

The Trading Bot System consists of several key components:

### Core Classes

- **Bot**: Base class for all trading bots
- **DataService**: Handles market data fetching and caching
- **PortfolioManager**: Manages buy/sell operations and rebalancing
- **BotRepository**: Database operations for bot state and trades

### Database Models

- **Bot**: Stores bot configuration and portfolio state
- **Trade**: Logs all trade executions
- **HistoricData**: Caches market data for performance
- **RunLog**: Tracks bot execution history
- **PortfolioWorth**: Historical portfolio valuations

### Utilities

- **Backtesting**: Simulate strategies on historical data
- **Hyperparameter Tuning**: Optimize bot parameters
- **Helpers**: Timezone handling and data validation

## Data Flow

```
1. Bot Initialization
   └──> Creates/retrieves bot from database
   └──> Initializes portfolio with {"USD": 10000}
   └──> Sets up DataService and PortfolioManager

2. Bot Execution (run())
   └──> Calls makeOneIteration()
   └──> Fetches data with technical indicators
   └──> Gets trading decision
   └──> Executes buy/sell if needed
   └──> Logs result to database

3. Data Fetching
   └──> Checks database cache first
   └──> Fetches from yfinance if needed
   └──> Saves to database for future reuse
   └──> Applies technical analysis indicators
```

## Key Technologies

- **Python 3.12+** with type hints
- **PostgreSQL** via SQLAlchemy ORM
- **yfinance** for market data
- **ta** library for technical analysis
- **Kubernetes CronJobs** for scheduled execution
- **Helm** for deployment management

## Next Steps

- [Bot Class System](bot-class-system.md) - Detailed architecture
- [Database Models](database-models.md) - Data structure
- [API Reference](../api/bot.md) - Complete API docs
