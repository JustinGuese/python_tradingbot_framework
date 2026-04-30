# Trading Bot System - LLM Guide

Trading Bot Framework — Research Briefing  
This is a Python-based automated trading framework running on Kubernetes. Bots run as CronJobs, fetch market data, make decisions, and execute
paper/live trades stored in PostgreSQL.

---

Bot Interface — Two Patterns

Pattern A: decisionFunction(row) -> int (simple, backtestable)

- Override this method; the base class handles data fetching, looping, and execution
- Called once per OHLCV row. Return 1 (buy), -1 (sell), 0 (hold)
- Supports local_backtest(), local_optimize(), hyperparameter tuning

Pattern A1 — Single-ticker (symbol=):

- The framework buys/sells self.symbol automatically

class MyBot(Bot):
def **init**(self):
super().**init**("MyBot", symbol="QQQ", interval="1d", period="1y")

      def decisionFunction(self, row) -> int:
          if row["momentum_rsi"] < 30:
              return 1
          elif row["momentum_rsi"] > 70:
              return -1
          return 0

Pattern A2 — Multi-ticker (tickers=[...]):

- Pass tickers= instead of symbol=; decisionFunction is called per ticker per bar
- Position sizing: equal-weight — each ticker targets total_portfolio_value / N
- Fully backtestable via local_backtest() and local_optimize()

class MyMultiBot(Bot):
def **init**(self):
super().**init**("MyMultiBot", tickers=["SPY", "QQQ", "GLD"],
interval="1d", period="1y")

      def decisionFunction(self, row) -> int:
          if row["momentum_rsi"] < 30:
              return 1   # buy this ticker toward equal-weight target
          elif row["momentum_rsi"] > 70:
              return -1  # sell all holdings of this ticker
          return 0

Pattern B: makeOneIteration() -> int (complex, not backtestable)

- Override this method directly for multi-asset bots or external data sources
- Manually call self.buy(symbol), self.sell(symbol), self.rebalancePortfolio(weights)
- Use when: portfolio rebalancing across N symbols, signals come from a DB table, external API (Fear & Greed), AI agent flows

---

Data Available

1. Yahoo Finance OHLCV — via self.getYFDataWithTA(interval, period)

- Returns a DataFrame with timestamp, open, high, low, close, volume + ~150 TA indicators
- Intervals: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1wk, 1mo
- Periods: 1d, 5d, 7d, 1mo, 3mo, 6mo, 1y, 2y, max (minute data capped at 60 days by Yahoo)
- Multi-symbol: self.getYFDataMultiple(symbols, interval, period) — returns long-format DataFrame

2. Technical Indicators — via ta library (add_all_ta_features)
   All ~150 indicators are pre-computed and available as columns. Key ones:

- Momentum: momentum_rsi, momentum_stoch, momentum_stoch_signal, momentum_macd, momentum_macd_signal, momentum_cci, momentum_williams_r
- Trend: trend*macd, trend_macd_signal, trend_macd_diff, trend_sma_fast, trend_sma_slow, trend_ema_fast, trend_ema_slow, trend_adx,
  trend_adx_pos, trend_adx_neg, trend_ichimoku*\*, trend_aroon_up/down
- Volatility: volatility_bbm, volatility_bbh, volatility_bbl, volatility_bbw, volatility_atr, volatility_kcp, volatility_dcp
- Volume: volume_obv, volume_adi, volume_cmf, volume_fi, volume_mfi, volume_em, volume_vpt

3. PostgreSQL Tables

┌──────────────────────┬───────────────────────────────────────────────────┬────────────────────────┐
│ Table │ Contents │ Used by │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ bots │ Portfolio state JSON per bot │ All bots │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ trades │ Trade history (symbol, price, qty, isBuy) │ All bots │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ run_logs │ Execution history, success/error │ All bots │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ portfolio_worth │ Daily portfolio value snapshots │ Dashboard │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ historic_data │ Cached OHLCV (avoids re-fetching) │ All bots │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ stock_news │ Recent news headlines per symbol from yfinance │ StockNewsSentimentBot │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ stock_earnings │ Earnings dates, EPS estimate vs actual, surprise% │ EarningsInsiderTiltBot │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ stock_insider_trades │ Insider buy/sell transactions │ EarningsInsiderTiltBot │
├──────────────────────┼───────────────────────────────────────────────────┼────────────────────────┤
│ telegram_messages │ Telegram channel messages + AI summaries + symbol │ TelegramSignalsBankBot │
└──────────────────────┴───────────────────────────────────────────────────┴────────────────────────┘

4. AI — via OpenRouter

- Cheap model (default openrouter/free): self.run_ai_simple(system, user) — classification, extraction, single-turn
- Main model (default deepseek/deepseek-v3.2): self.run_ai(system, user) — multi-turn with tools (portfolio lookup, market data, trade  
  history, news lookup)
- Fallback wrapper: self.run_ai_simple_with_fallback(system, user, sanity_check) — cheap first, retries with main if output fails sanity check

5. Regime / Sentiment Utilities

- utils.regime — detects bull/bear/sideways regime from price data
- utils.ta_regime — TA-based regime via ADX + trend filters
- utils.sentiment — fear/greed or other sentiment adapters

6. Tradeable Universe — utils.portfolio.TRADEABLE

- Pre-defined list of liquid ETFs/stocks suitable for the portfolio rebalancing bots

---

Portfolio Operations

self.buy(symbol, quantityUSD=-1) # -1 = all cash
self.sell(symbol, quantityUSD=-1) # -1 = all holdings
self.rebalancePortfolio({"QQQ": 0.6, "GLD": 0.3, "USD": 0.1})
self.getLatestPrice(symbol) # float
self.getLatestPricesBatch(symbols) # dict[str, float]

Portfolio state is a JSON dict in PostgreSQL: {"USD": 8432.10, "QQQ": 12.5, "GC=F": 0.03}.

---

Guardrails & Limitations

Hard limitations:

1. Event-driven bots (makeOneIteration only) cannot be backtested with the built-in engine. Multi-ticker decisionFunction bots (tickers=[...]) ARE backtestable via local_backtest() and local_optimize().
2. No short selling — sell() only sells existing holdings; going short is not supported.
3. No leverage — position sizing is bounded by available cash.
4. No fractional lot enforcement — the framework buys fractional quantities; fine for crypto/forex, may not reflect reality for equities.
5. Minute data capped at 60 days — Yahoo Finance hard limit for intervals ≤ 90m.
6. Backtest warmup skip — first ~26 bars are skipped when trend_adx == 0.0 (TA warmup period); strategies that need very few bars may lose  
   meaningful data.

Backtest realism (recently fixed):

- Slippage: 0.05% per side (configurable via slippage_pct)
- Commission: 0% default (configurable via commission_pct)
- Risk-free rate: 0% default for Sharpe (configurable via risk_free_rate)
- No look-ahead bias — bfill() removed from TA computation
- QuantStats reports: Automatically generated and uploaded to GCS (if credentials configured) showing Sharpe/return optimization views, drawdown analysis, and performance vs. buy-and-hold benchmark. Local backtest() and local_optimize() both produce reports. [Example report](docs/examplequantstatsreport.html)

Practical constraints:

- Bots run as Kubernetes CronJobs — no real-time streaming, no intra-bar execution
- Minimum meaningful trade: quantityUSD > $10 (enforced in signal bots)
- All times are UTC; market hours not enforced (strategy must handle weekends/holidays if needed)
- acted_on flag pattern is used for event-driven bots (Telegram signals, stock news) to prevent double-execution on crash

Common pitfalls:

- decisionFunction is called once per historical row (~252 calls/ticker for 1y daily). Any external lookup (DB query, API call) inside it runs 252 times per ticker. Always cache per-ticker: check a dict before querying, store the result, reuse it for subsequent rows of the same ticker.
- SQLAlchemy detached instance: ORM objects become inaccessible after their session closes. When querying inside get_db_session(), extract all needed values as plain Python types (float(), str(), etc.) before the `with` block exits. Never return an ORM object from a function that closes the session — attributes will raise DetachedInstanceError on access.

---

Existing Strategies (don't duplicate)

┌────────────────────────┬──────────────┬────────────────────────────────────────────────┐
│ Bot │ Asset │ Strategy │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ EURUSDTreeBot │ EURUSD │ Decision tree on TA indicators │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ XAUZenBot │ Gold (GC=F) │ Multi-indicator TA threshold rules │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ XAUAISyntheticMetalBot │ Gold │ AI agent with TA + market data tools │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ SwingTitaniumBot │ configurable │ Swing highs/lows detection on close │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ TARegimeBot │ configurable │ TA regime (ADX + trend) → buy/sell │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ FearGreedBot │ QQQ │ CNN Fear & Greed Index thresholds │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ RegimeAdaptiveBot │ multi │ AI decides allocation by market regime │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ AIHedgeFundBot │ multi │ Full AI hedge fund analysis → rebalance │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ AIDeepSeekToolBot │ configurable │ AI agent with full tool suite │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ EarningsInsiderTiltBot │ multi │ Equal-weight + tilt by earnings/insider scores │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ TelegramSignalsBankBot │ multi │ AI classifies Telegram signals → trade │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ StockNewsSentimentBot │ multi │ AI classifies news headlines → trade │
├────────────────────────┼──────────────┼────────────────────────────────────────────────┤
│ SqueezeMomentumBot │ GLD │ EMA/MACD/RSI zone momentum on Gold ETF │
└────────────────────────┴──────────────┴────────────────────────────────────────────────┘

---

What a New Strategy Should Specify

1. Pattern: A (decisionFunction) or B (makeOneIteration)?
2. Symbol(s): single ticker or portfolio?
3. Interval + period: what data granularity?
4. Signal logic: what columns/data drive the decision?
5. Hyperparameter grid (optional): dict of lists for local_optimize() to tune
6. Schedule: how often to run (cron string)?

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
  - `telegramsignalsbankbot.py` (reads `telegram_messages` table, classifies signals via AI, partial buys at 20% of cash per signal; uses `acted_on` column for crash-safe deduplication)

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

## Telegram Channel Monitor

A standalone channel monitor that is **not** a `Bot` subclass — it uses Telethon directly.

### Architecture

- **`tradingbot/telegram_monitor.py`**: entry point — parses env vars, connects via Telethon, calls module-level functions
- **`helm/tradingbots/templates/cronjob-telegram-monitor.yaml`**: optional Helm CronJob, gated on `telegramMonitor.enabled`

### How it runs

Stateless CronJob pattern: connect → fetch last N messages per channel → skip known IDs → summarize new ones with AI → disconnect. No persistent process.

Session is stored as a Telethon `StringSession` in the `TELEGRAM_SESSION_STRING` K8s secret.

### Key env vars

| Variable                  | Purpose                                             |
| ------------------------- | --------------------------------------------------- |
| `TELEGRAM_API_ID`         | From my.telegram.org                                |
| `TELEGRAM_API_HASH`       | From my.telegram.org                                |
| `TELEGRAM_SESSION_STRING` | Telethon StringSession                              |
| `TELEGRAM_CHANNELS`       | Comma-separated channel usernames or IDs            |
| `TELEGRAM_FETCH_LIMIT`    | Messages to check per channel per run (default: 50) |

### AI summarization

`summarize_message(text)` calls `run_ai_simple` (cheap LLM) with a prompt that returns JSON:

```json
{ "summary": "1-3 sentence summary...", "symbol": "AAPL" }
```

Falls back to raw response as summary if JSON parsing fails. `symbol` is `null` when no specific asset is mentioned.

### Database model: `TelegramMessage`

```python
class TelegramMessage(Base):
    id: int                  # Auto-increment primary key
    channel: str             # Channel username or numeric ID (indexed)
    message_id: int          # Telegram message ID — unique per channel
    text: str                # Original text (nullable, max 4000 chars)
    summary: str             # AI summary (nullable)
    symbol: str              # Primary ticker extracted by AI (nullable, indexed)
    acted_on: bool           # Set True before any trade action — prevents duplicate processing
    published_at: datetime   # UTC posting time
    created_at: datetime
    # Unique constraint: (channel, message_id)
```

Enabling in `values.yaml`:

```yaml
telegramMonitor:
  enabled: true
  schedule: '*/30 * * * *'
  channels: 'some_channel,-1001234567890'
  fetchLimit: '50'
```

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
    schedule: '*/5 * * * 1-5' # Every 5 minutes, Mon-Fri
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

### 8. Wrapper vs Implementation Pattern

**Principle**: Keep "wrappers" (entry points) in `tradingbot/` and application logic in `tradingbot/utils/`.

**Purpose**: Separation of concerns - wrappers handle orchestration (env vars, logging setup) while utils contain reusable logic.

**Pattern**:

```
tradingbot/telegram_monitor.py         # Wrapper: entry point, env var parsing
tradingbot/utils/telegram_monitor.py   # Implementation: core logic, reusable functions
```

**Wrapper responsibility** (`tradingbot/telegram_monitor.py`):

- Parse environment variables
- Set up logging
- Call utils functions with parsed parameters
- Handle script execution (`if __name__ == "__main__"`)

```python
import os
from telethon.sessions import StringSession
from utils.telegram_monitor import monitor_channels

def main():
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session_string = os.environ["TELEGRAM_SESSION_STRING"]
    channels = [c.strip() for c in os.environ.get("TELEGRAM_CHANNELS", "").split(",") if c.strip()]

    monitor_channels(api_id, api_hash, StringSession(session_string), channels)

if __name__ == "__main__":
    main()
```

**Implementation responsibility** (`tradingbot/utils/telegram_monitor.py`):

- Core business logic (functions: `get_existing_message_ids()`, `summarize_message()`, `process_channel()`)
- Database operations
- External API calls
- Returns data, doesn't know about env vars or logging setup

```python
def monitor_channels(api_id: int, api_hash: str, session_string, channels: list[str]):
    """Core implementation - no env vars, no logging setup."""
    # Connect to Telegram
    # Process channels
    # Store results
```

**Examples in codebase**:

- `calculate_portfolio_worth.py` (wrapper) → imports `utils.portfolio_worth_calculator` (impl)
- `aitools.py` (in utils) → provides `run_ai_simple()`, `run_ai_with_tools()` — reusable functions

**When to apply this pattern**:

- Creating a new script/cronjob
- Extracting reusable logic that other modules might use
- Simplifying complex entrypoints

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

### 7. Bot Name Casing — Never Normalize

**Problem**: Bot names in the `bots` table are stored **exactly** as the bot constructs itself, e.g. `"AdaptiveMeanReversionBot"` (CamelCase). Lookups via `BotRepository.create_or_get_bot(name)` use a case-sensitive `filter_by(name=...)` exact match. If the lookup misses, **a new row is silently created with default `{"USD": 10000}`** — no error.

This combination is a footgun: any code that lowercases (or otherwise normalizes) a bot name before lookup will silently spawn a duplicate stub row with $10k of fake cash. The bug surfaces as "no target weights / portfolio appears empty," not as an error.

**Solution**:

- Never call `.lower()` / `.upper()` / `.strip()` etc. on bot names before passing them to `BotRepository`. Pass user input through verbatim.
- When accepting bot names from external input (env vars, JSON config, CLI args), validate against `session.query(Bot).all()` first and abort with a clear error if the user's name isn't an exact match — do **not** rely on `create_or_get_bot` for validation, it will happily create whatever you ask for.
- Filename / Helm `name:` are lowercase by convention (`adaptivemeanreversionbot.py`, `name: adaptivemeanreversionbot`), but the DB row name is **CamelCase** (whatever the bot passes to `super().__init__("CamelCaseName", ...)`). Don't conflate them.

### 8. `create_or_get_bot` is Not a Validator

**Problem**: The name `create_or_get_bot` suggests safe lookup, but it's actually `INSERT IF NOT EXISTS` — it never returns `None` and never raises on a missing bot. Calling it with a typo'd or normalized name pollutes the DB with empty stub rows.

**Solution**: For paths that should fail loudly on a missing bot (live-trade copier, AI bot weight configs, anything driven by user input), do an explicit existence check first:

```python
with get_db_session() as s:
    if not s.query(Bot).filter_by(name=name).first():
        raise ValueError(f"Bot {name!r} not in DB. Existing: {[b.name for b in s.query(Bot).all()]}")
```

Only use `create_or_get_bot` when the caller genuinely owns the bot's identity (i.e., the bot itself, registering on first run).

### 9. LiveTrade Buy Sizing — Clamp to Cash, Not Equity

**Problem**: `LiveTradeCopier` sizes buy orders against `total_equity` (broker's `ModelAccountValue` for C2, NetLiquidation for IB). But brokers run a margin/cash check at order submission and reject if cash < notional. Sells in the same sync don't necessarily release cash before the buy batch fires (C2 paper accounts in particular are slow to settle), so a target like 100% QQQ on a previously-diversified account will get rejected even though equity covers it. Symptom on C2: `PreMarginCheck api2 b cs` — "Current account cash is $X; proposed trade requires cash of $Y".

**Solution**: `_execute_orders` re-fetches `broker.get_cash()` after the settle delay and scales all buys proportionally if their total notional exceeds available cash (with a 2% buffer). Buys that scale below `min_order_usd` are dropped. Don't skip the cash clamp by sizing buys against equity directly — settlement timing is broker-specific and not something the copier should pretend to know.

If buys are still rejected, bump `LIVETRADE_SETTLE_DELAY_SECONDS` (defaults to 10s — too short for C2 paper).

### 10. `POSTGRES_URI` Required Even for Non-DB Tests

**Problem**: `pytest tests/...` fails with `KeyError: 'Set POSTGRES_URI or (POSTGRES_HOST + POSTGRES_PASSWORD) for database connection'` even when running tests that don't touch the DB (e.g. pure-mock tests under `tests/test_livetrade.py`).

**Cause**: `tradingbot/utils/__init__.py` imports `botclass` → `bot_repository` → `db`, and `db.py` resolves `DATABASE_URL` at module import time. Any test that imports anything from `tradingbot.utils` (or transitively, like the livetrade copier) triggers this.

**Solution**: Pass a stub URI for non-DB test runs:

```bash
POSTGRES_URI="postgresql://x:x@localhost:5432/x" PYTHONPATH=. uv run pytest tests/ -q
```

The connection isn't opened until a session is actually used, so a syntactically-valid bogus URI is enough to satisfy import.

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

| File                                                 | Purpose                                |
| ---------------------------------------------------- | -------------------------------------- |
| `tradingbot/utils/botclass.py`                       | Base Bot class - core functionality    |
| `tradingbot/utils/db.py`                             | Database models and session management |
| `kubernetes/helm/tradingbots/values.yaml`            | Bot configurations and schedules       |
| `kubernetes/helm/tradingbots/templates/cronjob.yaml` | CronJob template                       |
| `.gitlab-ci.yml`                                     | CI/CD pipeline configuration           |

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
