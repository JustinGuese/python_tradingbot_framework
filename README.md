# 🤖 Trading Bot Framework

**A Production-Ready, Kubernetes-Native Algorithmic Trading System**

kubectl create secret generic tradingbot-secrets --from-env-file=.env --namespace=tradingbots-2025 --dry-run=client -o yaml | kubectl apply -f -

![Trading Bot Framework](docs/overview.png)

This framework allows developers to build, backtest, and deploy automated trading strategies as **Kubernetes CronJobs**. It handles the "boring stuff"—data ingestion, technical analysis, database persistence, and portfolio tracking—so you can focus on the alpha.

## 🚀 Why this Framework?

- **Batteries Included**: 150+ Technical Indicators (RSI, MACD, etc.) ready out of the box.
- **Infrastructure as Code**: Native Helm charts for easy scaling on K8s.
- **Data Consistency**: Built-in caching and PostgreSQL persistence for trade history and market data.
- **Backtesting to Production**: One class handles local testing, hyperparameter optimization, and live execution.

## 🎯 The Target: Alpha vs QQQ, Not Return

Bots are judged by their **annualised alpha against QQQ**: the daily return left
over after removing the bot's QQQ exposure (beta), along with that alpha's
t-stat. Raw return and Sharpe alone are not the target.

**Why:** QQQ exposure costs nothing, since you can just buy QQQ. In a bull market,
raw return rewards bots that merely *are* QQQ, or leveraged QQQ, and punishes
low-beta bots that are doing exactly their job. A bot with beta ≈ 1 and zero
alpha adds nothing to a portfolio that already holds QQQ. A low-correlation
bot with positive alpha is the only kind that makes the whole book better.

Rules of thumb:
- `beta = cov(bot, QQQ) / var(QQQ)`, `alpha = mean(bot − beta·QQQ) × 252`,
  computed on weekday `portfolio_worth` returns.
- **t-stat ≥ 2 before calling an edge real.** A few months of live data rarely
  gets there, so treat anything less as unproven.
- Prefer **beta / correlation < 0.5**. A high-beta, zero-alpha bot should be
  replaced with QQQ itself.
- Pause bots whose alpha is significantly negative (t ≤ −2).

See [CLAUDE.md](CLAUDE.md) for the full method and the latest fleet snapshot.

## 🛠 System Architecture

The system is designed to be lightweight and stateless. Each "Bot" is a containerized instance triggered by a schedule.

1. **Ingestion**: Fetches data from Yahoo Finance (with DB caching).
2. **Analysis**: Enriches data with the `ta` library (Technical Analysis).
3. **Execution**: `BotClass` manages the state of your portfolio in PostgreSQL.
4. **Monitoring**: Real-time performance tracking via the included Dashboard.

## ⚡ Quick Start

### 1. Requirements

- **Python 3.12+** (We recommend [uv](https://github.com/astral-sh/uv) for speed)
- **Docker** (for local DB)

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
        if row["momentum_rsi"] < 30:
            return 1  # Buy
        if row["momentum_rsi"] > 70:
            return -1  # Sell
        return 0


if __name__ == "__main__":
    bot = RSIBot()
    bot.run()  # Single iteration
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
        if row["momentum_rsi"] < self.rsi_buy:
            return 1
        if row["momentum_rsi"] > self.rsi_sell:
            return -1
        return 0


# Optimize and backtest
bot = RSIBot()
bot.local_development()  # Finds best params (by alpha t-stat vs QQQ), then backtests
```

Every backtest reports `alpha`, `alpha_t`, `beta` and `benchmark_corr` against QQQ.
The optimizer ranks by `alpha_t` by default. Pass `objective="sharpe_ratio"` or
`"yearly_return"` to rank the old way, but in a bull market both of those favour
bots that are just QQQ.

**Key Features**:

- Data pre-fetching: Historical data fetched once, reused for all parameter combinations
- Database caching: Data persisted to DB, subsequent runs are instant
- Parallel execution: Uses multiple CPU cores automatically
- Automatic QuantStats reports: Generated and uploaded to Google Cloud Storage with Sharpe/return optimizations and drawdown analysis

## 📈 Dashboard & Monitoring

The framework includes a built-in visualization suite to track your bots' performance. Each backtest generates a professional **QuantStats report** with cumulative returns, drawdown analysis, monthly heatmaps, and more ([see example](docs/examplequantstatsreport.html)).

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
    schedule: '*/5 * * * 1-5' # Every 5 mins, Mon-Fri
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

See the comprehensive [**Bot Implementation Levels guide**](docs/guides/bot-implementation-levels.md) for detailed explanations, examples, trade-offs, and pitfalls for each pattern.

**Quick summary:**

**1. Simple (Recommended)**: `decisionFunction(row)`
For single-row TA signals. The base class handles data fetching, `self.data` (full history slice), and buy/sell execution automatically — no `makeOneIteration` needed:

```python
def decisionFunction(self, row):
    if row["momentum_rsi"] < 30:
        return 1
    if row["momentum_rsi"] > 70:
        return -1
    return 0
```

If your signal needs the full historical DataFrame (e.g. Hurst exponent, rolling z-scores), use `self.data` — the base class sets it automatically before calling `decisionFunction`:

```python
def decisionFunction(self, row):
    lookback = self.data.tail(50)  # self.data is always the slice up to current bar
    zscore = (row["close"] - lookback["close"].mean()) / lookback["close"].std()
    return 1 if zscore < -2 else (-1 if zscore > 2 else 0)
```

**1b. Multi-Asset**: `tickers=[...]` + `decisionFunction(row)`
For strategies that trade multiple instruments. The framework calls `decisionFunction` once per ticker per bar, sets `self._current_ticker` and populates `self.datas` with all tickers' history up to the current bar:

```python
def __init__(self):
    super().__init__("MyBot", tickers=["QQQ", "GLD", "TLT"], interval="1d", period="1y")


def decisionFunction(self, row):
    ticker = self._current_ticker
    # self.datas[ticker] has history up to current bar for all tickers
    ...
    return 1  # -1 or 0
```

**2. Override `makeOneIteration()`** — only when you need external APIs or portfolio-weight rebalancing that can't map to -1/0/1 signals:

```python
def makeOneIteration(self):
    fear_greed = get_fear_greed_index()  # External API — not backtestable
    if fear_greed >= 70:
        self.buy("QQQ")
    return 1
```

```python
def makeOneIteration(self):
    data = self.getYFDataMultiple(["QQQ", "GLD", "TLT"])
    weights = optimize_portfolio(data)  # Portfolio optimizer — outputs weights, not signals
    self.rebalancePortfolio(weights)
    return 0
```

> **Backtesting**: Only bots using `decisionFunction()` (Levels 1 and 1b) are backtestable
> via `local_backtest()`. Bots that override `makeOneIteration()` with external API calls,
> AI models, or portfolio-weight optimizers cannot be replayed on historical data and must
> be validated via live runs. If your strategy is yfinance-only, prefer `decisionFunction`.

### Key Methods

| Method                                                                                              | Description                                                                           |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `getYFDataWithTA()`                                                                                 | Fetches OHLCV + 150 indicators.                                                       |
| `decisionFunction(row)`                                                                             | Logic applied to every candle. Return `-1, 0, 1`.                                     |
| `makeOneIteration()`                                                                                | Override for custom logic.                                                            |
| `local_backtest()`                                                                                  | Simulates strategy performance on historical data.                                    |
| `local_development()`                                                                               | Optimize hyperparameters + backtest.                                                  |
| `buy(symbol)` / `sell(symbol)`                                                                      | Automated portfolio and DB logging.                                                   |
| `rebalancePortfolio(weights)`                                                                       | Rebalance to target weights.                                                          |
| `run_ai(system_prompt, user_message)`                                                               | Runs AI with tools (main LLM); returns model response. Requires `OPENROUTER_API_KEY`. |
| `run_ai_simple(system_prompt, user_message)`                                                        | Single-turn, no tools (cheap LLM); for summarization, extraction, classification.     |
| `run_ai_simple_with_fallback(system_prompt, user_message, sanity_check=..., fallback_to_main=True)` | Cheap LLM first; validates output; retries with main LLM if sanity check fails.       |

### AI Tools (LangChain + OpenRouter)

Two LLMs: **main** (OPENROUTER_MAIN_MODEL, default `deepseek/deepseek-v3.2`) for tool-using flows; **cheap** (OPENROUTER_CHEAP_MODEL, default `openrouter/free`) for simple single-turn text tasks. Set `OPENROUTER_API_KEY` (required); optionally set the two model env vars.

**With tools (main LLM):**

```python
response = bot.run_ai(
    system_prompt="You are a trading assistant.", user_message="Summarize my recent trades and portfolio."
)
print(response)  # Model response as string
```

**Simple tasks, no tools (cheap LLM):** summarization, extraction, classification, rewriting:

```python
summary = bot.run_ai_simple(system_prompt="You summarize in one sentence.", user_message="Summarize: ...")
```

**Cheap-first with fallback:** Try cheap LLM first, validate output for sanity, and retry with main LLM if the result fails. Use for simple tasks when you want to save cost but guarantee sane results:

```python
result = bot.run_ai_simple_with_fallback(
    system_prompt="You classify sentiment.",
    user_message="Classify as buy/hold/sell: ...",
    sanity_check=None,  # optional; default rejects empty/refusal/error prefix
    fallback_to_main=True,
)
```

**Tools available to the model (when using run_ai):**

1. **get_market_data(symbol, period)** – Market data (OHLCV), default last two weeks.
2. **get_portfolio_status()** – Current portfolio worth (USD) and holdings.
3. **get_recent_trades(limit)** – Recent trades; for sells, profit of the closed trade is shown.
4. **get_stock_news(symbol, limit)** – Recent news for a symbol (title, link, publisher, published_at) from the database.
5. **get_stock_earnings(symbol, limit)** – Recent earnings dates and EPS (estimate, reported, surprise %) for a symbol from the database.
6. **get_stock_insider_trades(symbol, limit)** – Recent insider transactions (insider, type, shares, value) for a symbol from the database.

See [AI Tools Guide](docs/guides/ai-tools.md) and [AITools API](docs/api/aitools.md) for details.

### Portfolio Structure

Portfolio is stored as JSON in the database:

```python
portfolio = {
    "USD": 10000.0,  # Cash
    "QQQ": 5.5,  # Holdings (quantity, not value)
    "AAPL": 10.0,  # More holdings
}
```

Access via: `bot.dbBot.portfolio.get("USD", 0)`

### Available Indicators

Access over 150 indicators via the `row` object:

- **Trend**: `trend_macd`, `trend_adx`, `trend_ichimoku_a`, `trend_sma_fast`, `trend_sma_slow`
- **Momentum**: `momentum_rsi`, `momentum_stoch`, `momentum_ao`, `momentum_roc`, `momentum_ppo`
- **Volatility**: `volatility_bbh` (Bollinger High), `volatility_bbl` (Bollinger Low), `volatility_atr`
- **Volume**: `volume_vwap`, `volume_obv`, `volume_mfi`

See [Technical Analysis Guide](docs/guides/technical-analysis.md) for complete list.

## 📖 Documentation

**Online Documentation**: [justinguese.github.io/python_tradingbot_framework/](https://justinguese.github.io/python_tradingbot_framework/)

### Getting Started

- [Quick Start Guide](docs/getting-started/quick-start.md) - Complete local development workflow with PostgreSQL setup, bot creation at different abstraction levels, backtesting, and hyperparameter tuning
- [Installation](docs/getting-started/installation.md) - System requirements and dependency installation
- [Creating a Bot](docs/getting-started/creating-a-bot.md) - Detailed bot creation patterns and examples

### Deployment

- [Deployment Overview](docs/deployment/overview.md) - Kubernetes vs local deployment options
- [Kubernetes Deployment](docs/deployment/kubernetes.md) - Cluster setup and configuration
- [Helm Charts](docs/deployment/helm.md) - Bot scheduling and Helm configuration

### Guides

- [Technical Analysis](docs/guides/technical-analysis.md) - Complete indicator reference
- [Portfolio Management](docs/guides/portfolio-management.md) - Advanced portfolio operations
- [Local Development](docs/guides/local-development.md) - Development workflows
- [AI Tools](docs/guides/ai-tools.md) - LangChain + OpenRouter tools; cheap-first with fallback and sanity checks
- [Telegram Monitor](docs/guides/telegram-monitor.md) - Channel monitoring, AI summarization, ticker extraction

### API Reference

- [Bot API](docs/api/bot.md) - Complete Bot class documentation
- [Data Service](docs/api/data-service.md) - Data fetching and caching
- [Portfolio Manager](docs/api/portfolio-manager.md) - Trading operations
- [AITools API](docs/api/aitools.md) - `run_ai_with_tools`, `run_ai_simple`, `run_ai_simple_with_fallback`

## 📡 Telegram Channel Monitor

The framework includes an optional **Telegram channel monitor** that polls channels for new messages, summarizes them with AI, extracts the primary asset ticker, and writes results to the database.

```yaml
# helm/tradingbots/values.yaml
telegramMonitor:
  enabled: true
  schedule: '*/30 * * * *' # Every 30 minutes
  channels: 'some_news_channel,-1001234567890'
  fetchLimit: '50'
```

**How it works**: Runs as a CronJob — connects via a Telethon StringSession (no persistent process), fetches recent messages, skips already-stored ones, summarizes each with the cheap LLM, and persists to `telegram_messages` table with `channel`, `text`, `summary`, `symbol`, and `published_at`.

**Required secrets**: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` (from [my.telegram.org](https://my.telegram.org/apps)).

See [Telegram Monitor Guide](docs/guides/telegram-monitor.md) for full setup instructions.

## 🧪 quants.md Strategy Bots (`quantsmd_*`)

45 re-implementations of the [quants.md](https://quants.md/?sort=sharpe) (QuantJourney) example strategies plus 10 `v2`
variants, all `tradingbot/quantsmd_*.py`, all **paper** CronJobs, none wired into a live copier. Each file has a pure
`compute_weights(...)` and a `targetWeights` bot; shared helpers are in `tradingbot/utils/quantsmd_common.py`
(universes, rebalance gates), `quantsmd_orders.py` (OHLC fill model for order-style bots) and `quantsmd_v2.py`
(hysteresis, minimum hold, vol targeting, SHV cash parking, SPY SMA200 regime gate).

Backtest them with `uv run python scripts/onetime_quantsmd_backtest.py [module ...]`; results are judged on alpha vs
QQQ and t-stat, not return:

- [Top 10](docs/backtests/quantsmd-2026-11.md) and [the other 35](docs/backtests/quantsmd-2026-11-all.md): nothing
  has real edge (t >= 2 in both halves). Most bots are under-invested (beta 0.03-0.3), so their small positive alpha
  is a low-beta artefact. FX and intraday bots lose.
- [v2 variants](docs/backtests/quantsmd-v2-2026-11.md): `quantsmd_sma_daily_gated` (SPY below SMA200 -> cash) is the
  first to clear t >= 2 with both halves positive (t 3.08, beta 0.16), still a watch-list candidate. Hysteresis,
  rank buffers, currency-ETF carry, a pairs basket and a 12-bot order ensemble did not help.

## 📈 Live Trading (Collective2, Interactive Brokers, eToro, Darwinex, MT5/MQL5 & Numerai)

> [!WARNING]
> **DISCLAIMER:** This software is for educational and research purposes only. Trading involves significant risk of loss and is not suitable for all investors. Use of "Live Trading" features is strictly at your own risk. The authors and contributors are not liable for any financial losses, damages, or unintended trades incurred. Always test strategies thoroughly in a paper-trading environment before deploying real capital.

The framework can mirror your paper-bot portfolios to a live brokerage account. Supported brokers and venues: **Collective2** (World API v4), **Interactive Brokers** (IB Gateway socket API via `ib_async`), **eToro** (Public REST API), **Darwinex** (DXtrade API), **MetaTrader 5 / MQL5** (RPyC bridge to MT5 terminal for MQL5 signal publishing), and **Numerai Signals** (tournament signal submissions).

### 1. Configure Environment

Add these to your `.env` or Kubernetes secrets:

```bash
# Collective2
COLLECTIVE2_API_KEY=your_api_key
COLLECTIVE2_SYSTEM_ID=12345678

# Interactive Brokers (IB Gateway socket API; the gateway holds the IBKR login)
IB_ACCOUNT_ID=DU1234567   # paper accounts start with DU; live with U
IB_GATEWAY_HOST=127.0.0.1 # in-cluster: ib-gateway-service.interactivebrokersgateway
IB_GATEWAY_PORT=4002
IB_CLIENT_ID=17           # keep stable: cancel_open_orders only sees this clientId's orders

# eToro (Public REST API)
ETORO_API_KEY=your_public_key
ETORO_USER_KEY=your_user_key
ETORO_DEMO=true             # true for demo/paper, false for live

# Darwinex (DXtrade API)
DARWINEX_USERNAME=your_username
DARWINEX_PASSWORD=your_password
DARWINEX_ACCOUNT_ID=12345   # optional
DARWINEX_DEMO=true          # true for demo/paper, false for live

# Shared
LIVETRADE_BOT_WEIGHTS='{"adaptivemeanreversionbot": 1.0}'
LIVETRADE_DRY_RUN=false
```

### Interactive Brokers gateway

The CronJob talks to the IB Gateway deployed in the `interactivebrokersgateway` namespace
(`gnzsnz/ib-gateway`, paper). The gateway owns the IBKR login, 2FA and daily restart, so the
job needs no credentials. Its NetworkPolicy only admits pods labelled `ib-gateway-client=true`
(the Helm template adds the label); Service port 4002 maps to pod port 4004.

```yaml
liveTrade:
  interactiveBrokers:
    enabled: true
    accountId: "DU6347896"
```

To test locally, port-forward the gateway and run the module directly:

```bash
kubectl -n interactivebrokersgateway port-forward svc/ib-gateway-service 4002:4002
IB_GATEWAY_PORT=4002 uv run python -m tradingbot.livetrade.interactive_brokers
```

Full runbook: [docs/guides/live-trading.md](docs/guides/live-trading.md).

### Inspect Account & Portfolio

Each broker module is runnable directly to print the account summary and current
positions — useful for sanity-checking credentials, account IDs, and mappings
before running the copier:

```bash
# Collective2
uv run python -m tradingbot.livetrade.collective2

# Interactive Brokers (IB Gateway socket; reads IB_GATEWAY_HOST/PORT + IB_ACCOUNT_ID from .env)
uv run python -m tradingbot.livetrade.interactive_brokers

# eToro (reads ETORO_API_KEY, ETORO_USER_KEY, ETORO_DEMO from .env)
uv run python -m tradingbot.livetrade.etoro

# Darwinex (reads DARWINEX_USERNAME, DARWINEX_PASSWORD, DARWINEX_DEMO from .env)
uv run python -m tradingbot.livetrade.darwinex
```

### 2. Map Your Tickers

Yfinance symbols often differ from broker symbols (e.g., `EURUSD=X` vs `EURUSD`). The framework includes an **Assisted Ticker Discovery** script to help you map them:

```bash
# 1. Discover unmapped tickers from your bots and trades
uv run python -m tradingbot.livetrade.discover_symbols

# 2. Edit symbol_map.review.json in your editor
#    Add "selected_symbol" and "selected_type" for the tickers you want to map.

# 3. Apply the approved mappings to the master symbol_map.json
uv run python -m tradingbot.livetrade.discover_symbols --apply
```

### 3. Deploy the Copier

The copier runs as a standalone script per broker. Deploy as a CronJob to run shortly after your trading bots:

```bash
# Collective2
uv run python -m tradingbot.livetrade_collective2

# Interactive Brokers
uv run python -m tradingbot.livetrade_interactive_brokers

# eToro
uv run python -m tradingbot.livetrade_etoro

# Darwinex
uv run python -m tradingbot.livetrade_darwinex
```

Each broker is its own Helm CronJob gated by an independent flag in `values.yaml` — enable them separately (`liveTrade.collective2.enabled`, `liveTrade.interactiveBrokers.enabled`, `liveTrade.etoro.enabled`, `liveTrade.darwinex.enabled`), so you can run any combination or none. All default to `false`. You can also cap how much of the account each broker mirrors via `LIVETRADE_PORTFOLIO_FRACTION` (default `1.0` = full account; e.g. `0.5` = half).

See the [Live Trading Guide](docs/guides/live-trading.md) for advanced configuration and mapping rules.

### Collective2 status and C2 blend review (2026-10-26)

**Collective2 does not work right now: every order is rejected.** The CronJob
(`livetrade-collective2-xauzerifine`, strategy 155809898) completes green, which
hides it. Each `NewStrategyOrder` returns HTTP 400, error `1020`: *"A paid Trade
Leader plan is required"*. Evidence from the last three runs (2026-10-21 to 10-23):

- All ten target legs (DBC, DBA, UUP, GLD, EFA, SPY, IWM, SLV, EEM, ...) failed.
- `live_equity` shows $60,152 equity, all cash, `positions = {}`. It has not moved
  since 2026-09-22. The blend has never been live in the broker.
- The earlier `get_cash()` bug (cash read as $0) is fixed: cash now reads
  $60,152 from 2026-10-19. That was a separate problem.
- Nothing in the code can fix this. It is an account-plan restriction.
- `livetrade-ib` is also failing on every run (the known `ssodh/init` 500), so
  there is no working live broker at the moment. eToro and Darwinex are not
  scheduled, and Darwinex has no equities.

**Decision needed (yours):** buy the C2 Trade Leader plan, or move the blend to a
broker that can take the orders (unblock IBKR, or enable eToro). Until then the
live track record is zero. The paper sleeves below are the only evidence.

**How the blend is done today**

- `liveTrade.collective2XauZerifine.botWeights` in `helm/tradingbots/values.yaml`:
  TARegimeMultiAssetBot 0.39 / GoldenButterflyMomBot 0.37 / TSMOMTrendBot 0.24.
- Weights are inverse volatility, fit on 2019-01..2023-06 only. Idle weight is
  parked in SHV. Legs C2 cannot trade are dropped to cash, not redistributed.
- The copier reads each sleeve's paper portfolio from `bots.portfolio`, scales by
  weight, and reconciles the whole C2 strategy to that target (full sync, so it
  liquidates anything not in the target).
- Backtest (`docs/backtests/c2-blend-2026-09.md`): alpha +3.5%/yr, t 2.02, beta
  0.13, max DD -8.5%, CAGR 6.6% at 5.7% vol.

**Live paper sleeves since 2026-09-21** (4.5 weeks, far too short to judge):

| Sleeve | Return | Weight |
|---|---|---|
| TARegimeMultiAssetBot | -0.6% | 0.39 |
| TSMOMTrendBot | -1.9% | 0.24 |
| GoldenButterflyMomBot | -0.4% | 0.37 |
| Weighted blend | about -0.9% | |
| QQQ | -5.0% | |

The blend held up in a down tape, as a beta 0.13 book should. That says nothing
about alpha. The weekly report gives TARegimeMultiAsset and TSMOM only 19
observations, and GoldenButterfly has alpha -7%/yr at t -1.13 over 101 days
(backtest: +2.7%, t 1.43).

**What I would do instead.** None of this has been backtested; these are
proposals to test, not changes to ship.

1. **Fix execution before touching weights.** A blend that places no orders
   cannot be improved by re-weighting. Pick the broker first.
2. **Do not re-fit now.** There are 4.5 weeks of live data. The doc's own plan
   (re-score on `portfolio_worth` after ~6 months, about March 2027) stands.
   Re-weighting on a month of noise is how a t of 2.02 becomes a t of 0.
3. **Treat the three sleeves as about 1.5 independent bets, not 3.** Pairwise
   correlation is 0.58-0.67, and the blend's t (2.02) barely beats
   TARegimeMultiAsset alone (1.94). Inverse-vol ignores correlation. Test
   covariance-aware weights (equal risk contribution or min-variance, fit on H1
   only) against the current ones on the same H2 window.
4. **Add a sleeve that is actually uncorrelated, not another trend ETF book.**
   The candidates are weak: FearGreedInverse is a QQQ clone, IndexVolBot's edge
   is gone on real SPY prices, and the option bots have under 15 live days.
   Without one, the honest ceiling is about +3.5%/yr alpha.
5. **If the live record confirms it, scale instead of adding.** At beta 0.13 the
   book is too small to matter next to QQQ. Vol-target the blend to ~10% (about
   1.7x) rather than adding weaker sleeves. The blend doc already says this is
   only defensible after the live record confirms the backtest.
6. **Keep GoldenButterfly under review, but do not drop it yet.** Its live t is
   -1.13 on 101 days against a positive backtest. That is not significant. If it
   is still negative in March, drop it and re-weight the other two.

## 🎯 Example Bots

- **eurusdtreebot.py** - Decision tree-based strategy for EUR/USD
- **feargreedbot.py** - Uses Fear & Greed Index API for market sentiment
- **swingtitaniumbot.py** - Swing trading strategy
- **xauzenbot.py** - Gold (XAU) trading bot
- **sharpeportfoliooptweekly.py** - Portfolio optimization with Sharpe ratio
- **aihedgefundbot.py** - AI-driven portfolio rebalancing
- **deepseektoolbot.py** - AI with tools (research + submit weights); cheap LLM sanity-check and main-LLM retry
- **gptbasedstrategytabased.py** - GPT-based strategy with technical analysis

See [Example Bots](docs/examples/example-bots.md) for implementation details.

## 🧭 Next Steps

### Options

Parked on purpose, as of 2026-09-29:

- **LEAP / PMCC on real quotes: not possible with the current data.** The DoltHub
  SPY/AAPL backfill in `option_quotes` holds only ~3 expiries a day, the longest
  about 65 days out. A real-quote test of `option_LeapCallBot` and
  `option_PMCCBot` needs a source with long-dated history. Until then their
  synthetic results in `docs/backtests/option-bots-2026-09.md` are all there is.
- **Dispersion backtest: needs member chains.** DoltHub has SPY and AAPL only.
  Our own `optionchainsnapshot` capture of 52 names started 2026-09-28. Revisit
  once `implied_correlation` holds 60+ days, around January 2027. That is also
  when `option_DispersionBot`'s own history gate opens.
- **The other round-3 bots (CrossVol, MispricingScan, EarningsCrush): wait for
  the capture.** `scripts/onetime_option_replay_backtest.py` works on the
  captured full chains as it is. Allow about 60–80 sessions before reading a
  t-stat.
- **SVI fitter iteration cap: low value.** `svi.fit_svi` stops at scipy's
  default 500 evaluations and treats that as a failed fit (about 15 of 52 names
  on a typical day). Raising `max_nfev` only takes the usable fits from 30/52 to
  33/52. Most rejections are butterfly arbitrage in the extrapolated wings, so
  the better fix is to run the arbitrage check only over the quoted strike
  range. `smile_outliers` covers for it in the meantime.
