# 🤖 Trading Bot Framework

**A Production-Ready, Kubernetes-Native Algorithmic Trading System**

![Trading Bot Framework](docs/overview.png)

This framework allows developers to build, backtest, and deploy automated trading strategies as **Kubernetes CronJobs**. It handles the "boring stuff"—data ingestion, technical analysis, database persistence, and portfolio tracking—so you can focus on the alpha.

## 🚀 Why this Framework?

* **Batteries Included**: 150+ Technical Indicators (RSI, MACD, etc.) ready out of the box.
* **Infrastructure as Code**: Native Helm charts for easy scaling on K8s.
* **Data Consistency**: Built-in caching and PostgreSQL persistence for trade history and market data.
* **Backtesting to Production**: One class handles local testing, hyperparameter optimization, and live execution.


## 🛠 System Architecture

The system is designed to be lightweight and stateless. Each "Bot" is a containerized instance triggered by a schedule.

1. **Ingestion**: Fetches data from Yahoo Finance (with DB caching).
2. **Analysis**: Enriches data with the `ta` library (Technical Analysis).
3. **Execution**: `BotClass` manages the state of your portfolio in PostgreSQL.
4. **Monitoring**: Real-time performance tracking via the included Dashboard.


## ⚡ Quick Start

### 1. Requirements

* **Python 3.12+** (We recommend [uv](https://github.com/astral-sh/uv) for speed)
* **Docker** (for local DB)

### 2. Launch Local Environment

```bash
# Start PostgreSQL
docker run -d --name pg-trading -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=tradingbot -p 5432:5432 postgres:17-alpine

# Install project
uv sync
export POSTGRES_URI="postgresql://postgres:pass@localhost:5432/tradingbot"

```

### 3. Your First Strategy

Create a simple RSI Mean Reversion bot in seconds:

```python
from tradingbot.utils.botclass import Bot

class RSIBot(Bot):
    def __init__(self):
        super().__init__("RSIBot", "AAPL", interval="1m", period="1d")
    
    def decisionFunction(self, row):
        if row["momentum_rsi"] < 30: return 1  # Buy
        if row["momentum_rsi"] > 70: return -1 # Sell
        return 0

if __name__ == "__main__":
    bot = RSIBot()
    bot.run() # Single iteration
```

### 4. Local Development & Testing

**Backtest your strategy** before going live:

```python
bot = RSIBot()
results = bot.local_backtest(initial_capital=10000.0)
print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
print(f"Yearly Return: {results['yearly_return']:.2%}")
```

**Optimize hyperparameters** automatically:

```python
class RSIBot(Bot):
    # Define search space
    param_grid = {
        "rsi_buy": [25, 30, 35],
        "rsi_sell": [65, 70, 75],
    }
    
    def __init__(self, rsi_buy=30.0, rsi_sell=70.0, **kwargs):
        super().__init__("RSIBot", "AAPL", interval="1m", period="1d", **kwargs)
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
    
    def decisionFunction(self, row):
        if row["momentum_rsi"] < self.rsi_buy: return 1
        if row["momentum_rsi"] > self.rsi_sell: return -1
        return 0

# Optimize and backtest
bot = RSIBot()
bot.local_development()  # Finds best params, then backtests
```

**Key Features**:
- Data pre-fetching: Historical data fetched once, reused for all parameter combinations
- Database caching: Data persisted to DB, subsequent runs are instant
- Parallel execution: Uses multiple CPU cores automatically


## 📈 Dashboard & Monitoring

The framework includes a built-in visualization suite to track your bots' performance.

![Portfolio Overview](docs/overview.png)

**Overview Dashboard** shows:
- Current Worth, Total Return %, Annualized Return %
- Sharpe Ratio, Sortino Ratio, Max Drawdown %
- Volatility, Total Trades, Start Date

![Bot Detail Page](docs/detailpage.png)

**Bot Detail Page** includes:
- Portfolio value charts, daily returns distribution
- Monthly returns heatmap, drawdown visualization
- Current holdings table, complete trade history

The dashboard is deployed automatically with the Helm chart. See [Deployment](#-deployment) for setup.


## 🏗 Deployment

### Production (Kubernetes)

The system treats every bot as a **CronJob**. Define your schedule in `values.yaml` and deploy:

**1. Create Kubernetes Secret**:

```bash
# Create .env file with:
# POSTGRES_PASSWORD=yourpassword
# POSTGRES_URI=postgresql://postgres:yourpassword@psql-service:5432/postgres
# OPENROUTER_API_KEY=yourkey (if using AI bots)
# BASIC_AUTH_PASSWORD=yourpassword (for dashboard)

# Create namespace
kubectl create namespace tradingbots-2025

# Create secret
kubectl create secret generic tradingbot-secrets \
  --from-env-file=.env \
  --namespace=tradingbots-2025
```

**2. Configure Bots**:

```yaml
# helm/tradingbots/values.yaml
bots:
  - name: rsibot
    schedule: "*/5 * * * 1-5" # Every 5 mins, Mon-Fri
```

**3. Deploy**:

```bash
helm upgrade --install tradingbots \
  ./helm/tradingbots \
  --create-namespace \
  --namespace tradingbots-2025
```

**PostgreSQL** is automatically deployed via Helm (if `postgresql.enabled: true` in `values.yaml`).

**For detailed guides**, see:
- [Deployment Overview](docs/deployment/overview.md) - Complete deployment options
- [Kubernetes Deployment](docs/deployment/kubernetes.md) - Cluster setup
- [Helm Charts](docs/deployment/helm.md) - Configuration details


## 🧰 Developer Reference

### Bot Implementation Levels

**1. Simple (Recommended)**: `decisionFunction(row)`
For strategies based on single-row technical indicators:

```python
def decisionFunction(self, row):
    if row["momentum_rsi"] < 30: return 1
    if row["momentum_rsi"] > 70: return -1
    return 0
```

**2. Medium Complexity**: Override `makeOneIteration()`
For external APIs or custom data processing:

```python
def makeOneIteration(self):
    fear_greed = get_fear_greed_index()  # External API
    if fear_greed >= 70: self.buy("QQQ")
    return 1
```

**3. Complex**: Portfolio Optimization
For multi-asset strategies and rebalancing:

```python
def makeOneIteration(self):
    data = self.getYFDataMultiple(["QQQ", "GLD", "TLT"])
    weights = optimize_portfolio(data)  # Your optimization
    self.rebalancePortfolio(weights)
    return 0
```

### Key Methods

| Method | Description |
| --- | --- |
| `getYFDataWithTA()` | Fetches OHLCV + 150 indicators. |
| `decisionFunction(row)` | Logic applied to every candle. Return `-1, 0, 1`. |
| `makeOneIteration()` | Override for custom logic. |
| `local_backtest()` | Simulates strategy performance on historical data. |
| `local_development()` | Optimize hyperparameters + backtest. |
| `buy(symbol)` / `sell(symbol)` | Automated portfolio and DB logging. |
| `rebalancePortfolio(weights)` | Rebalance to target weights. |

### Portfolio Structure

Portfolio is stored as JSON in the database:

```python
portfolio = {
    "USD": 10000.0,      # Cash
    "QQQ": 5.5,          # Holdings (quantity, not value)
    "AAPL": 10.0,        # More holdings
}
```

Access via: `bot.dbBot.portfolio.get("USD", 0)`

### Available Indicators

Access over 150 indicators via the `row` object:

* **Trend**: `trend_macd`, `trend_adx`, `trend_ichimoku_a`, `trend_sma_fast`, `trend_sma_slow`
* **Momentum**: `momentum_rsi`, `momentum_stoch`, `momentum_ao`, `momentum_roc`, `momentum_ppo`
* **Volatility**: `volatility_bbh` (Bollinger High), `volatility_bbl` (Bollinger Low), `volatility_atr`
* **Volume**: `volume_vwap`, `volume_obv`, `volume_mfi`

See [Technical Analysis Guide](docs/guides/technical-analysis.md) for complete list.


## 📖 Documentation

**Online Documentation**: [justinguese.github.io/python_tradingbot_framework/](https://justinguese.github.io/python_tradingbot_framework/)

### Getting Started
* [Quick Start Guide](docs/getting-started/quick-start.md) - Complete local development workflow with PostgreSQL setup, bot creation at different abstraction levels, backtesting, and hyperparameter tuning
* [Installation](docs/getting-started/installation.md) - System requirements and dependency installation
* [Creating a Bot](docs/getting-started/creating-a-bot.md) - Detailed bot creation patterns and examples

### Deployment
* [Deployment Overview](docs/deployment/overview.md) - Kubernetes vs local deployment options
* [Kubernetes Deployment](docs/deployment/kubernetes.md) - Cluster setup and configuration
* [Helm Charts](docs/deployment/helm.md) - Bot scheduling and Helm configuration

### Guides
* [Technical Analysis](docs/guides/technical-analysis.md) - Complete indicator reference
* [Portfolio Management](docs/guides/portfolio-management.md) - Advanced portfolio operations
* [Local Development](docs/guides/local-development.md) - Development workflows

### API Reference
* [Bot API](docs/api/bot.md) - Complete Bot class documentation
* [Data Service](docs/api/data-service.md) - Data fetching and caching
* [Portfolio Manager](docs/api/portfolio-manager.md) - Trading operations

## 🎯 Example Bots

* **eurusdtreebot.py** - Decision tree-based strategy for EUR/USD
* **feargreedbot.py** - Uses Fear & Greed Index API for market sentiment
* **swingtitaniumbot.py** - Swing trading strategy
* **xauzenbot.py** - Gold (XAU) trading bot
* **sharpeportfoliooptweekly.py** - Portfolio optimization with Sharpe ratio
* **aihedgefundbot.py** - AI-driven portfolio rebalancing
* **gptbasedstrategytabased.py** - GPT-based strategy with technical analysis

See [Example Bots](docs/examples/example-bots.md) for implementation details.
