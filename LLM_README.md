# Trading Bot System - LLM Guide

This document provides essential information for LLMs working with this trading bot codebase. It explains the architecture, the Bot class system, and how to effectively work with the code.

## Repository Overview

This is an automated trading bot system that:
- Fetches market data from Yahoo Finance
- Executes trading strategies based on technical analysis
- Manages portfolios and tracks trades in PostgreSQL
- Runs as Kubernetes CronJobs on a schedule
- Uses Helm charts for deployment

## Architecture

### Directory Structure

```
tradingbot/
├── utils/
│   ├── botclass.py      # Base Bot class (core functionality)
│   └── db.py            # Database models and session management
├── eurusdtreebot.py     # Example bot implementations
├── feargreedbot.py
├── swingtitaniumbot.py
└── ... (other bot files)

kubernetes/
└── helm/
    └── tradingbots/     # Helm chart for deployment
        ├── Chart.yaml
        ├── values.yaml  # Bot configurations
        └── templates/
            ├── cronjob.yaml
            ├── postgresql-secret.yaml
            ├── postgresql-deployment.yaml
            └── postgresql-service.yaml
```

### Key Technologies

- **Python 3.12+** with type hints
- **PostgreSQL** via SQLAlchemy ORM
- **yfinance** for market data
- **ta** library for technical analysis indicators
- **Kubernetes CronJobs** for scheduled execution
- **Helm** for deployment management

## The Bot Class System

### Core Concept

The `Bot` class (`tradingbot/utils/botclass.py`) is the foundation. All trading bots inherit from it and implement one of the following approaches, **in order of preference from simplest to most complex**:

#### 1. **Simplest (Preferred)**: Implement only `decisionFunction(row)`
   - **When to use**: Your strategy can be expressed as logic on a single data row with technical indicators
   - **How it works**: Base class fetches data, applies your function to each row, averages the last N decisions, and executes trades
   - **Examples**: `xauaisyntheticmetalbot.py`, `xauzenbot.py`, `gptbasedstrategytabased.py`, `eurusdtreebot.py`
   - **Best practice**: This is the recommended approach for most bots

#### 2. **Medium complexity**: Override `makeOneIteration()` for custom data sources or simple custom logic
   - **When to use**: You need external APIs (e.g., Fear & Greed Index), custom data processing, or different timeframe handling
   - **How it works**: You control the entire iteration but still use base class methods for trading
   - **Examples**: `feargreedbot.py` (uses external API instead of market data)

#### 3. **Complex**: Override `makeOneIteration()` for portfolio optimization or multi-symbol strategies
   - **When to use**: Portfolio rebalancing, multiple symbols, complex optimization algorithms, external data sources
   - **How it works**: Full control over data fetching, decision logic, and trade execution
   - **Examples**: 
     - `sharpePortfoliooptWeekly.py` (portfolio optimization with multiple assets)
     - `aihedgefundbot.py` (reads trading decisions from external database and rebalances)

### Bot Class Lifecycle

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
   ├── Fetches data: getYFDataWithTA(saveToDB=True, interval=self.interval, period=self.period)
   ├── Gets decision: getLatestDecision(data) [applies decisionFunction to each row]
   └── Executes trade if decision != 0
```

### Key Bot Class Methods

#### Data Fetching

```python
# Fetch raw market data
data = bot.getYFData(interval="1m", period="1d", saveToDB=True)
# Returns: DataFrame with columns [symbol, timestamp, open, high, low, close, volume]

# Fetch data with technical analysis indicators
data = bot.getYFDataWithTA(interval="1m", period="1d", saveToDB=True)
# Returns: Same DataFrame + ~150+ TA indicators (RSI, MACD, Bollinger Bands, etc.)
# Indicators are prefilled/backfilled to handle NaN values
```

**Important**: Data is cached in `self.data` based on `(interval, period)` tuple. If you call with the same settings, it returns cached data.

#### Decision Making

```python
# Standard approach: Implement decisionFunction
def decisionFunction(self, row: pd.Series) -> int:
    """
    Args:
        row: Single row from DataFrame with all TA indicators
        
    Returns:
        -1: Sell signal
         0: Hold (no action)
         1: Buy signal
    """
    if row["rsi"] < 30:
        return 1  # Oversold, buy
    elif row["rsi"] > 70:
        return -1  # Overbought, sell
    return 0

# The base class then:
# 1. Applies decisionFunction to each row: data.apply(self.decisionFunction, axis=1)
# 2. Takes the mean of the last N rows (default: 1)
# 3. Returns -1, 0, or 1
```

**When to override `makeOneIteration()`**:
- You need external data sources (e.g., Fear & Greed Index API)
- You need portfolio optimization with multiple symbols
- You need custom data processing beyond what `decisionFunction` can handle
- See `feargreedbot.py` (external API) and `sharpePortfoliooptWeekly.py` (portfolio optimization) for examples

#### Trading Operations

```python
# Buy with all available cash
bot.buy(symbol="QQQ")

# Buy specific USD amount
bot.buy(symbol="QQQ", quantityUSD=1000)

# Sell all holdings
bot.sell(symbol="QQQ")

# Sell specific USD amount
bot.sell(symbol="QQQ", quantityUSD=500)
```

**Important**: 
- `buy()` and `sell()` automatically update the portfolio in the database
- They log trades to the `trades` table
- Portfolio is stored as `{"USD": 10000, "QQQ": 5.5, ...}` in the `bots` table

#### Portfolio Management

```python
# Access portfolio
cash = bot.dbBot.portfolio.get("USD", 0)
holding = bot.dbBot.portfolio.get("QQQ", 0)

# Portfolio is a JSON field in database, automatically synced
# After buy/sell, portfolio is updated via __updateBotInDB()
```

#### Price Fetching

```python
# Get latest price (uses cached data if available, otherwise fetches fresh)
price = bot.getLatestPrice(symbol="QQQ")
```

**Note**: If `self.datasettings == ("1m", "1d")` and data is loaded, uses cached data. Otherwise fetches fresh from yfinance.

### Database Models (tradingbot/utils/db.py)

#### Bot Model
```python
class Bot(Base):
    name: str (primary key)
    description: str (optional)
    portfolio: dict (JSON, default: {"USD": 10000})
    created_at: datetime
    updated_at: datetime
```

#### Trade Model
```python
class Trade(Base):
    id: int (auto-increment)
    bot_name: str (foreign key to Bot.name)
    symbol: str
    isBuy: bool
    quantity: float
    price: float
    timestamp: datetime
    profit: float (nullable, for sells)
```

#### HistoricData Model
```python
class HistoricData(Base):
    symbol: str (primary key)
    timestamp: datetime (primary key)
    open: float
    high: float
    low: float
    close: float
    volume: float
```

#### RunLog Model
```python
class RunLog(Base):
    id: int (auto-increment)
    bot_name: str (foreign key to Bot.name)
    start_time: datetime
    success: bool
    result: str (nullable, contains decision/error info)
```

### Database Session Management

**Always use the context manager**:
```python
from utils.db import get_db_session

with get_db_session() as session:
    # Do database operations
    bot = session.query(Bot).filter_by(name="MyBot").first()
    # Context manager automatically commits on success, rolls back on error
```

**Important**: The context manager handles:
- Automatic commit on success
- Automatic rollback on exceptions
- Connection retry logic (3 attempts with exponential backoff)
- Proper session cleanup

#### Connecting to Multiple Databases

If you need to query a different database (e.g., `ai_hedge_fund`) while the bot's portfolio is stored in the main `postgres` database:

```python
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from os import environ

def _get_other_database_session(self):
    """Create a separate connection to another database."""
    # Read POSTGRES_URI but don't modify the environment variable
    base_uri = environ.get("POSTGRES_URI", "")
    
    # Modify URI to point to different database (e.g., ai_hedge_fund)
    if "/" in base_uri:
        parts = base_uri.rsplit("/", 1)
        other_db_uri = parts[0] + "/other_database"
    else:
        other_db_uri = base_uri + "/other_database"
    
    # Create separate engine (doesn't affect base Bot class connection)
    database_url = "postgresql+psycopg2://" + other_db_uri
    engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

# Usage with raw SQL
session = self._get_other_database_session()
try:
    result = session.execute(text("SELECT * FROM some_table")).fetchone()
finally:
    session.close()
```

**Key points**:
- Base Bot class uses main `postgres` database (via `utils/db.py`)
- Create separate engine for other database connections
- Don't modify `POSTGRES_URI` environment variable
- Use `text()` from SQLAlchemy for raw SQL queries on tables without models

## Creating a New Bot

### Step 1: Create Bot File

Create `tradingbot/{botname}bot.py`:

```python
from utils.botclass import Bot

class MyNewBot(Bot):
    # Optional: Define hyperparameter search space for tuning
    param_grid = {
        "rsi_buy": [65, 70, 75],
        "rsi_sell": [25, 30, 35],
    }
    
    def __init__(
        self,
        rsi_buy: float = 70.0,
        rsi_sell: float = 30.0,
        **kwargs
    ):
        # Symbol like "QQQ", "EURUSD=X", "^XAU"
        # Optional: interval="1d", period="1mo" for daily/weekly strategies
        super().__init__("MyNewBot", "SYMBOL", interval="1m", period="1d", **kwargs)
        
        # Store parameters as instance variables
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
    
    def decisionFunction(self, row):
        # Your trading logic here
        # Access TA indicators via row["indicator_name"]
        # Return -1, 0, or 1
        if row["momentum_rsi"] < self.rsi_buy:
            return 1  # Oversold, buy
        elif row["momentum_rsi"] > self.rsi_sell:
            return -1  # Overbought, sell
        return 0  # Hold

# Standard entry point for local development
bot = MyNewBot()

bot.local_development()  # Runs hyperparameter optimization + backtest
# bot.run()  # Uncomment for production (or use environment detection)
```

**Note**: If you only need to change the timeframe (interval/period), you can set it in the constructor and don't need to override `makeOneIteration()`. See `gptbasedstrategytabased.py` for an example.

### Step 2: Add to Helm Chart

Edit `kubernetes/helm/tradingbots/values.yaml`:

```yaml
bots:
  - name: mynewbot
    schedule: "*/5 * * * 1-5"  # Every 5 minutes, Mon-Fri
```

**Important**: 
- Filename must be `{name}bot.py` (e.g., `mynewbot.py`)
- Helm automatically uses `{name}.py` as the script filename
- Container name is auto-generated as `tradingbot-{name}` (removes "bot" suffix)

### Step 3: Deploy

The GitLab CI pipeline will:
1. Build Docker image
2. Deploy via Helm (creates CronJob automatically)
3. Bot runs on schedule

## Important Patterns and Conventions

### 1. Bot Naming
- Bot class name: `CamelCaseBot` (e.g., `EURUSDTreeBot`)
- Bot database name: Same as class name (passed to `super().__init__()`)
- Filename: `{name}bot.py` (lowercase, e.g., `eurusdtreebot.py`)
- Helm name: `{name}bot` (e.g., `eurusdtreebot`)

### 2. Decision Function Contract
- **Must** return `int`: -1, 0, or 1
- Receives a `pd.Series` with all TA indicators
- Is called for **each row** in the DataFrame
- Base class averages the last N decisions (default: 1)

### 3. Data Format
All DataFrames have this structure:
```python
columns = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
# Plus ~150+ TA indicators after getYFDataWithTA()
```

### 4. Portfolio Structure
```python
portfolio = {
    "USD": 10000.0,      # Cash
    "QQQ": 5.5,          # Holdings (quantity, not value)
    "EURUSD=X": 1000.0,  # More holdings
}
```

### 5. Error Handling
- `run()` catches all exceptions and logs to `RunLog` table
- Database operations use retry logic automatically
- Empty data returns decision `0` (hold)

### 6. Data Caching
- `self.data` caches the last fetched DataFrame (per-instance cache)
- `self.datasettings` stores `(interval, period)` tuple
- If same settings requested, returns cached data (no API call)
- **Database persistence**: For cross-run data reuse (e.g., hyperparameter tuning), set `saveToDB=True` when fetching data. Subsequent calls (even from new Bot instances) will check the database first and only fetch from yfinance if data is missing or stale (older than 10 minutes by default).

### 7. Timezone Handling
**Important**: Always use timezone-aware datetimes when comparing with database timestamps.

```python
from datetime import datetime, timezone, timedelta

# ❌ Wrong: datetime.utcnow() returns timezone-naive
one_day_ago = datetime.utcnow() - timedelta(days=1)

# ✅ Correct: Use timezone-aware UTC datetime
now_utc = datetime.now(timezone.utc)
one_day_ago = now_utc - timedelta(days=1)

# Handle timezone-naive datetimes from database
if db_datetime.tzinfo is None:
    db_datetime = db_datetime.replace(tzinfo=timezone.utc)  # Assume UTC
```

## Common Pitfalls

### 1. DataFrame Mutation
**Problem**: `getLatestDecision()` used to mutate input DataFrame
**Solution**: Now works on a copy - safe to reuse DataFrames

### 2. Redundant Commits
**Problem**: Explicit `session.commit()` inside `get_db_session()` context manager
**Solution**: Context manager commits automatically - removed redundant commits

### 3. Index Out of Bounds
**Problem**: Accessing rows when DataFrame is too small
**Solution**: `getLatestDecision()` now handles empty/small DataFrames gracefully

### 4. Container Name Mismatch
**Problem**: Manual container names in CronJobs
**Solution**: Auto-generated from bot name: `tradingbot-{name}` (removes "bot" suffix)

### 5. Script Filename Mismatch
**Problem**: Manual script names in Helm values
**Solution**: Auto-generated from bot name: `{name}.py`

### 6. Timezone Comparison Errors
**Problem**: `TypeError: can't compare offset-naive and offset-aware datetimes` when comparing database timestamps
**Solution**: Use `datetime.now(timezone.utc)` instead of `datetime.utcnow()`, and handle timezone-naive datetimes from database by adding UTC timezone info

## Technical Analysis Indicators

After calling `getYFDataWithTA()`, the DataFrame includes indicators from the `ta` library:

**Categories**:
- **Trend**: `trend_sma_fast`, `trend_macd`, `trend_adx`, `trend_ichimoku_*`, etc.
- **Momentum**: `momentum_rsi`, `momentum_stoch`, `momentum_roc`, etc.
- **Volatility**: `volatility_bbh`, `volatility_bbl`, `volatility_atr`, `volatility_kch`, etc.
- **Volume**: `volume_*` indicators

**Naming**: All lowercase with underscores (e.g., `trend_sma_slow`, `momentum_rsi`)

**Access**: `row["indicator_name"]` in `decisionFunction()`

## Deployment Architecture

### Kubernetes CronJobs
- Each bot runs as a separate CronJob
- Schedule defined in `values.yaml`
- All use the same Docker image (tagged by branch)

### Helm Chart Structure
```
helm/tradingbots/
├── Chart.yaml              # Chart metadata
├── values.yaml             # Bot configurations
└── templates/
    ├── cronjob.yaml        # Generates CronJobs for each bot
    ├── postgresql-secret.yaml
    ├── postgresql-deployment.yaml
    └── postgresql-service.yaml
```

### GitLab CI Pipeline
1. **build-docker**: Builds and pushes Docker image
2. **helm-kubectl-deploy**: 
   - Extracts image repo/tag from `$IMAGE`
   - Deploys via Helm with image overrides
   - Creates namespace if needed

## Local Development and Hyperparameter Tuning

### Local Development Workflow

The Bot class provides convenient methods for local development and optimization:

```python
bot = MyBot()

# Option 1: Full workflow (optimize + backtest)
bot.local_development()
# - Runs hyperparameter optimization using param_grid
# - Backtests the best parameters
# - Prints results in easy-to-copy format

# Option 2: Just optimize
results = bot.local_optimize()
# Returns optimization results dictionary

# Option 3: Just backtest current parameters
results = bot.local_backtest()
# Returns backtest results dictionary
```

### Hyperparameter Tuning

**Define `param_grid` as a class attribute**:

```python
class MyBot(Bot):
    # Define hyperparameter search space
    param_grid = {
        "rsi_buy": [65, 70, 75],
        "rsi_sell": [25, 30, 35],
        "adx_threshold": [15, 20, 25],
    }
    
    def __init__(self, rsi_buy=70.0, rsi_sell=30.0, adx_threshold=20.0, **kwargs):
        super().__init__("MyBot", "QQQ", **kwargs)
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        self.adx_threshold = adx_threshold
```

**Key Features**:
- **Data pre-fetching**: Historical data is fetched once and reused for all parameter combinations (dramatically faster)
- **Database caching**: Data is saved to DB on first fetch, subsequent runs reuse cached data
- **Parallel execution**: Uses multiple CPU cores by default (configurable via `n_jobs`)
- **Automatic period adjustment**: For minute-level intervals, automatically uses 7 days instead of 1 year (respects Yahoo Finance limits)

**Optimization Process**:
1. Pre-fetches 1 year of data (or appropriate period based on interval) with TA indicators
2. Saves data to database for future reuse
3. Tests all parameter combinations in parallel
4. Returns best parameters and full results

**Backtesting Period Limits**:
- **Minute intervals** (1m, 5m, 15m, etc.): Uses 7 days (Yahoo Finance limit: 8 days)
- **Hourly intervals**: Uses 60 days
- **Daily/weekly/monthly**: Uses 1 year

### Standard Bot File Pattern

All bot files follow this pattern:

```python
class MyBot(Bot):
    param_grid = {...}  # Optional: for hyperparameter tuning
    
    def __init__(self, param1=default1, param2=default2, **kwargs):
        super().__init__("MyBot", "SYMBOL", interval="1d", period="1mo", **kwargs)
        self.param1 = param1
        self.param2 = param2
    
    def decisionFunction(self, row):
        # Trading logic
        return 0

# Local development: optimize and backtest
bot = MyBot()
bot.local_development()
# bot.run()  # Uncomment for production
```

**Production vs Development**:
- **Local development**: Use `bot.local_development()` to optimize and test
- **Production**: Uncomment `bot.run()` or use environment detection (Kubernetes sets `KUBERNETES_SERVICE_HOST`)

## Working with the Code

### When Adding Features
1. **Database changes**: Update models in `utils/db.py`, migrations handled by SQLAlchemy
2. **Bot functionality**: Extend `Bot` class methods
3. **New bots**: Follow the pattern in existing bots (include `param_grid` if tunable)
4. **Deployment**: Update `values.yaml` and redeploy

### When Debugging
1. Check `run_logs` table for execution history
2. Check `trades` table for trade history
3. Check `bots` table for portfolio state
4. Logs are printed to stdout (captured by Kubernetes)

### When Modifying Bot Logic

**Choose the simplest approach that works for your needs:**

1. **Start with `decisionFunction()`** - If your strategy can be expressed as logic on a single row:
   - Access TA indicators from `row["indicator_name"]`
   - Return -1, 0, or 1 based on conditions
   - Base class handles data fetching, averaging, and trade execution
   - **Example**: "Buy when RSI < 30 and MACD is bullish"

2. **Override `makeOneIteration()` only if needed** - For:
   - External APIs (Fear & Greed Index, sentiment data, etc.)
   - Portfolio optimization with multiple symbols
   - Custom data processing that can't be done row-by-row
   - **Example**: Portfolio rebalancing based on Sharpe ratio optimization

3. **Data fetching**: Use `getYFData()` or `getYFDataWithTA()` (both support interval/period)
4. **Trading**: Use `buy()` and `sell()` methods

## Key Files Reference

| File | Purpose |
|------|---------|
| `tradingbot/utils/botclass.py` | Base Bot class - core functionality |
| `tradingbot/utils/db.py` | Database models and session management |
| `kubernetes/helm/tradingbots/values.yaml` | Bot configurations and schedules |
| `kubernetes/helm/tradingbots/templates/cronjob.yaml` | CronJob template |
| `.gitlab-ci.yml` | CI/CD pipeline configuration |

## Summary

This system provides a robust framework for automated trading:

### Implementation Strategy (Simple → Complex)
1. **Start simple**: Implement only `decisionFunction(row)` - works for most strategies
2. **Add complexity only if needed**: Override `makeOneIteration()` for external APIs or portfolio optimization
3. **Use constructor parameters**: Set `interval` and `period` in `__init__()` to change timeframes without overriding methods

### Key Features
- **Inherit from Bot** and implement `decisionFunction()` (preferred) or `makeOneIteration()` (when needed)
- **Use provided methods** for data fetching, trading, and portfolio management
- **Database is handled automatically** - portfolio, trades, and logs are persisted
- **Deployment is template-based** - add bot to `values.yaml` and deploy
- **Error handling is built-in** - exceptions are caught and logged
- **Hyperparameter tuning** - Define `param_grid` and use `local_development()` for optimization
- **Efficient data loading** - Pre-fetches and caches data for hyperparameter tuning (avoids redundant API calls)
- **Smart period adjustment** - Automatically adjusts backtest period based on interval (respects Yahoo Finance limits)

The Bot class abstracts away database operations, data fetching, and trade execution, allowing you to focus on the trading strategy logic. **Always prefer the simplest approach that works for your needs.**

