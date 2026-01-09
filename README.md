# Trading Bot System

An automated trading bot system that fetches market data, executes trading strategies based on technical analysis, manages portfolios, and tracks trades in PostgreSQL. Bots run as Kubernetes CronJobs on configurable schedules.

![Portfolio Overview](docs/overview.png)

## Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Creating a Bot](#creating-a-bot)
- [Deployment](#deployment)
- [Technical Analysis Indicators](#technical-analysis-indicators)
- [Documentation](#documentation)

## Overview

This system provides a framework for building and deploying automated trading bots. Each bot:

- Fetches market data from Yahoo Finance
- Applies technical analysis indicators
- Makes trading decisions based on configurable strategies
- Manages portfolios and tracks trades in PostgreSQL
- Runs on configurable schedules via Kubernetes CronJobs

The system is built with Python 3.12+, uses SQLAlchemy for database operations, and is deployed via Helm charts to Kubernetes.

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL database
- Kubernetes cluster (for deployment)

### Running a Bot Locally

1. Install dependencies:
```bash
uv sync
```

2. Set up environment variables:
```bash
export POSTGRES_URI="postgresql://user:password@host:5432/database"
```

3. Run a bot:
```python
from tradingbot.eurusdtreebot import EURUSDTreeBot

bot = EURUSDTreeBot()
bot.run()
```

### Local Development Workflow

The Bot class provides convenient methods for local development and optimization:

```python
from tradingbot.eurusdtreebot import EURUSDTreeBot

bot = EURUSDTreeBot()

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

Define `param_grid` as a class attribute for automatic hyperparameter optimization:

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

## Architecture

### Bot Class System

All trading bots inherit from the base `Bot` class (`tradingbot/utils/botclass.py`). The system supports three implementation approaches:

#### 1. Simple (Recommended): Implement `decisionFunction(row)`
For strategies that can be expressed as logic on a single data row with technical indicators.

**Example:**
```python
from utils.botclass import Bot

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

#### 2. Medium Complexity: Override `makeOneIteration()`
For bots that need external APIs, custom data processing, or different timeframe handling.

**Example:** See `feargreedbot.py` (uses external Fear & Greed Index API)

#### 3. Complex: Override `makeOneIteration()` for Portfolio Optimization
For multi-asset strategies, portfolio rebalancing, or complex optimization algorithms.

**Example:** See `sharpeportfoliooptweekly.py` (portfolio optimization with multiple assets)

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

**Important**: Data is cached in `self.data` based on `(interval, period)` tuple. If you call with the same settings, it returns cached data. For cross-run data reuse (e.g., hyperparameter tuning), set `saveToDB=True` to persist data to the database.

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

# Portfolio structure: {"USD": 10000.0, "QQQ": 5.5, ...}
# Holdings are stored as quantities, not values
```

### Key Components

- **Bot Class**: Base class providing data fetching, trading operations, and portfolio management
- **Data Service**: Handles market data fetching from Yahoo Finance with database caching
- **Portfolio Manager**: Manages buy/sell operations and portfolio rebalancing
- **Bot Repository**: Database operations for bot state and trade logging
- **Database Models**: PostgreSQL models for bots, trades, historical data, and run logs

## Creating a Bot

### Step 1: Create Bot File

Create `tradingbot/{botname}bot.py`:

```python
from utils.botclass import Bot

class MyNewBot(Bot):
    def __init__(self, threshold: float = 0.5, **kwargs):
        """
        Initialize the bot.
        
        Args:
            threshold: Trading threshold parameter
            **kwargs: Additional parameters passed to base class
        """
        super().__init__("MyNewBot", "QQQ", interval="1m", period="1d", threshold=threshold, **kwargs)
        self.threshold = threshold
    
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
        if row["momentum_rsi"] < 30:
            return 1
        elif row["momentum_rsi"] > 70:
            return -1
        return 0

# Entry point
if __name__ == "__main__":
    bot = MyNewBot()
    bot.run()
```

### Step 2: Add to Helm Chart

Edit `helm/tradingbots/values.yaml`:

```yaml
bots:
  - name: mynewbot
    schedule: "*/5 * * * 1-5"  # Every 5 minutes, Mon-Fri
```

**Important Bot Naming Conventions**:
- Bot class name: `CamelCaseBot` (e.g., `EURUSDTreeBot`)
- Bot database name: Same as class name (passed to `super().__init__()`)
- Filename: `{name}bot.py` (lowercase, e.g., `eurusdtreebot.py`)
- Helm name: `{name}bot` (e.g., `eurusdtreebot`)
- Script filename: Automatically derived as `{name}.py` by Helm

### Step 3: Deploy

Deploy using Helm (see [Deployment](#deployment) section below).

## Deployment

### Manual Deployment

#### 1. Create Kubernetes Secret

Before deploying, you must create a Kubernetes secret from a `.env` file. Create a `.env` file with the following variables:

```bash
# PostgreSQL credentials
POSTGRES_PASSWORD=your_postgres_password_here
POSTGRES_URI=postgres:your_postgres_password_here@psql-service:5432/postgres
# Separate URI for ai-hedge-fund (uses different database)
POSTGRES_URI_AI_HEDGE_FUND=postgres:your_postgres_password_here@psql-service:5432/ai_hedge_fund

# Visualization dashboard basic auth
BASIC_AUTH_PASSWORD=your_basic_auth_password_here

# OpenRouter API key (for aihedgefundbot)
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

Then create the secret using kubectl. The secret name is defined in `values.yaml` under `secretName` (default: `tradingbot-secrets`):

```bash
# Create namespace if it doesn't exist
kubectl create namespace tradingbots-2025

# Create secret from .env file
# Secret name matches .Values.secretName in values.yaml (default: tradingbot-secrets)
kubectl create secret generic tradingbot-secrets \
  --from-env-file=.env \
  --namespace=tradingbots-2025
```

**Note**: The `--from-env-file` flag reads all key-value pairs from the `.env` file and creates the secret. All secrets (database, API keys, passwords) are stored in this single secret.

**Security Best Practice**: Never commit your `.env` file to version control. Add it to `.gitignore`.

#### 2. Deploy with Helm

```bash
helm upgrade --install tradingbots \
      ./helm/tradingbots \
      --create-namespace \
      --namespace tradingbots-2025
```

### Deployment Architecture

- **Kubernetes CronJobs**: Each bot runs as a separate CronJob with configurable schedules
- **Helm Charts**: Manages deployment configuration and bot schedules
- **PostgreSQL**: Stores bot state, trades, historical data, and run logs
- **Docker**: All bots run from the same container image

### Schedule Format

Bot schedules use standard cron syntax:
- `*/5 * * * 1-5` - Every 5 minutes, Monday-Friday
- `0 17 * * 1-5` - Daily at 5:00 PM, Monday-Friday
- `0 17 * * 2` - Weekly on Tuesday at 5:00 PM

## Technical Analysis Indicators

After calling `getYFDataWithTA()`, the DataFrame includes ~150+ technical analysis indicators from the `ta` library:

### Volume Indicators
`volume_adi`, `volume_obv`, `volume_cmf`, `volume_fi`, `volume_em`, `volume_sma_em`, `volume_vpt`, `volume_vwap`, `volume_mfi`, `volume_nvi`

### Volatility Indicators
`volatility_bbm`, `volatility_bbh`, `volatility_bbl`, `volatility_bbw`, `volatility_bbp`, `volatility_bbhi`, `volatility_bbli`, `volatility_kcc`, `volatility_kch`, `volatility_kcl`, `volatility_kcw`, `volatility_kcp`, `volatility_kchi`, `volatility_kcli`, `volatility_dcl`, `volatility_dch`, `volatility_dcm`, `volatility_dcw`, `volatility_dcp`, `volatility_atr`, `volatility_ui`

### Trend Indicators
`trend_macd`, `trend_macd_signal`, `trend_macd_diff`, `trend_sma_fast`, `trend_sma_slow`, `trend_ema_fast`, `trend_ema_slow`, `trend_vortex_ind_pos`, `trend_vortex_ind_neg`, `trend_vortex_ind_diff`, `trend_trix`, `trend_mass_index`, `trend_dpo`, `trend_kst`, `trend_kst_sig`, `trend_kst_diff`, `trend_ichimoku_conv`, `trend_ichimoku_base`, `trend_ichimoku_a`, `trend_ichimoku_b`, `trend_stc`, `trend_adx`, `trend_adx_pos`, `trend_adx_neg`, `trend_cci`, `trend_visual_ichimoku_a`, `trend_visual_ichimoku_b`, `trend_aroon_up`, `trend_aroon_down`, `trend_aroon_ind`, `trend_psar_up`, `trend_psar_down`, `trend_psar_up_indicator`, `trend_psar_down_indicator`

### Momentum Indicators
`momentum_rsi`, `momentum_stoch_rsi`, `momentum_stoch_rsi_k`, `momentum_stoch_rsi_d`, `momentum_tsi`, `momentum_uo`, `momentum_stoch`, `momentum_stoch_signal`, `momentum_wr`, `momentum_ao`, `momentum_roc`, `momentum_ppo`, `momentum_ppo_signal`, `momentum_ppo_hist`, `momentum_pvo`, `momentum_pvo_signal`, `momentum_pvo_hist`, `momentum_kama`

### Other Indicators
`others_dr`, `others_dlr`, `others_cr`

**Access**: Use `row["indicator_name"]` in your `decisionFunction()` method.

## Important Patterns and Conventions

### Portfolio Structure

Portfolio is stored as a JSON dictionary:
```python
portfolio = {
    "USD": 10000.0,      # Cash
    "QQQ": 5.5,          # Holdings (quantity, not value)
    "EURUSD=X": 1000.0,  # More holdings
}
```

### Data Format

All DataFrames have this structure:
```python
columns = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
# Plus ~150+ TA indicators after getYFDataWithTA()
```

### Decision Function Contract

- **Must** return `int`: -1 (sell), 0 (hold), or 1 (buy)
- Receives a `pd.Series` with all TA indicators
- Is called for **each row** in the DataFrame
- Base class averages the last N decisions (default: 1)

### Data Caching

- `self.data` caches the last fetched DataFrame (per-instance cache)
- `self.datasettings` stores `(interval, period)` tuple
- If same settings requested, returns cached data (no API call)
- **Database persistence**: For cross-run data reuse (e.g., hyperparameter tuning), set `saveToDB=True` when fetching data. Subsequent calls (even from new Bot instances) will check the database first and only fetch from yfinance if data is missing or stale.

### Timezone Handling

**Critical**: Always use timezone-aware datetimes when comparing with database timestamps.

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

### Error Handling

- `run()` catches all exceptions and logs to `RunLog` table
- Database operations use retry logic automatically
- Empty data returns decision `0` (hold)

## Common Pitfalls

1. **DataFrame Mutation**: `getLatestDecision()` now works on a copy - safe to reuse DataFrames
2. **Redundant Commits**: Context manager commits automatically - don't call `session.commit()` inside `get_db_session()` context
3. **Index Out of Bounds**: `getLatestDecision()` handles empty/small DataFrames gracefully
4. **Timezone Comparison Errors**: Use `datetime.now(timezone.utc)` instead of `datetime.utcnow()`, and handle timezone-naive datetimes from database

## Portfolio Visualization Dashboard

The system includes a web-based dashboard for monitoring bot performance and portfolio metrics.

### Overview Dashboard

The overview page displays a comprehensive table of all trading bots with key performance metrics:

![Portfolio Overview](docs/overview.png)

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

![Bot Detail Page](docs/detailpage.png)

The detail page includes:
- **Key Performance Indicators**: Total return, annualized return, Sharpe ratio, Sortino ratio, Calmar ratio, max drawdown, and volatility
- **Portfolio Value Chart**: Time series visualization of portfolio value over time
- **Daily Returns Distribution**: Histogram showing return frequency
- **Monthly Returns Heatmap**: Calendar view of monthly performance
- **Drawdown Chart**: Visual representation of portfolio drawdowns over time
- **Current Holdings Table**: Real-time portfolio composition
- **Trade History**: Complete log of all executed trades

### Accessing the Dashboard

The visualization dashboard is deployed as part of the Helm chart and accessible via the configured service. See the [Deployment](#deployment) section for setup instructions.

## Documentation

### Online Documentation

A comprehensive documentation site is automatically deployed to GitHub Pages:

- **Live Site**: [https://justinguese.github.io/python_tradingbot_framework/](https://justinguese.github.io/python_tradingbot_framework/)
- **MkDocs Site**: Auto-generated from code docstrings and markdown guides
- **API Reference**: Complete API documentation for all classes and methods
- **Guides**: Step-by-step tutorials and best practices

**Auto-deployment**: Documentation is automatically built and deployed on every push to `main`/`master` branch via GitHub Actions.

### Setup GitHub Pages

1. Go to repository **Settings** → **Pages**
2. Under **Source**, select **GitHub Actions**
3. Push to `main` branch - documentation will deploy automatically

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed setup instructions.

### Local Documentation

Build and serve the documentation locally:

```bash
# Install docs dependencies
uv sync --extra docs

# Serve locally
uv run mkdocs serve
```

Visit `http://127.0.0.1:8000` to view the documentation.

### Build Documentation

```bash
# Build static site
uv run mkdocs build
```

Output will be in the `site/` directory.

### Developer Documentation

For detailed developer documentation including:
- Complete Bot class API reference
- Database models and session management
- Advanced patterns and best practices
- Working with multiple databases
- GitLab CI pipeline details

See [LLM_README.md](LLM_README.md) for comprehensive developer documentation.

## Example Bots

- **eurusdtreebot.py**: Decision tree-based strategy for EUR/USD
- **feargreedbot.py**: Uses Fear & Greed Index API for market sentiment
- **swingtitaniumbot.py**: Swing trading strategy
- **xauzenbot.py**: Gold (XAU) trading bot
- **sharpeportfoliooptweekly.py**: Portfolio optimization with Sharpe ratio
- **aihedgefundbot.py**: AI-driven portfolio rebalancing
- **gptbasedstrategytabased.py**: GPT-based strategy with technical analysis
