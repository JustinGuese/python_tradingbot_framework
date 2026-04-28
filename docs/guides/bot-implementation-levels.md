# Bot Implementation Levels

> [!WARNING]
> **DISCLAIMER:** This software is for educational and research purposes only. Trading involves significant risk of loss and is not suitable for all investors. Use of this framework (including "Live Trading" features) is strictly at your own risk. The authors and contributors are not liable for any financial losses, damages, or unintended trades incurred. Always test strategies thoroughly in a paper-trading environment before deploying real capital.

This guide explains the three implementation patterns for trading bots and when to use each one. Choosing the right pattern is critical for backtestability, maintainability, and development speed.

## Quick Decision Tree

```
Does your signal depend on live external data?
  └─ YES (Fear & Greed, news, earnings, AI, Telegram, etc.)
     └─ Use Level 2: makeOneIteration() [NOT backtestable]

Does your strategy output portfolio weights instead of -1/0/1 signals?
  └─ YES (Sharpe optimization, equal-weight with tilts, etc.)
     └─ Use Level 2: makeOneIteration() [NOT backtestable]

Does your signal fit in a single row of data or self.data history?
  └─ YES (RSI, moving averages, Hurst exponent, z-scores, etc.)
     └─ Use Level 1/1b: decisionFunction() [BACKTESTABLE ✓]
```

---

## Level 1: Simple `decisionFunction(row)` — Single-Asset

**When to use**: Single ticker, signal is deterministic from yfinance data, no external APIs.

**Backtestable**: ✅ Yes — use `local_backtest()`, `local_optimize()`, `local_development()`

**Boilerplate**: Minimal. The base class handles data fetching, portfolio management, buy/sell execution automatically.

### Basic Example

```python
from tradingbot.utils.botclass import Bot

class RSIMeanReversionBot(Bot):
    def __init__(self):
        super().__init__("RSIMeanReversionBot", "SPY", interval="1d", period="1y")

    def decisionFunction(self, row):
        rsi = row.get("momentum_rsi", 50)
        if rsi < 30:
            return 1  # Buy oversold
        elif rsi > 70:
            return -1  # Sell overbought
        return 0  # Hold

if __name__ == "__main__":
    bot = RSIMeanReversionBot()
    results = bot.local_backtest()
    print(f"Sharpe: {results['sharpe_ratio']:.2f}")
    bot.run()  # Live execution
```

### Using `self.data` for Historical Context

If your signal needs the full historical slice (e.g., Hurst exponent, rolling z-scores, regime detection), use `self.data` — the base class populates it automatically:

```python
import numpy as np

class HurstMeanReversionBot(Bot):
    def __init__(self):
        super().__init__("HurstMeanReversionBot", "QQQ", interval="1d", period="2y")

    def decisionFunction(self, row):
        if self.data is None or len(self.data) < 100:
            return 0  # Warmup

        # Compute Hurst exponent on last 100 bars
        lookback = self.data.tail(100)["close"].values
        lags = range(10, 100, 5)
        tau = [np.std(np.diff(lookback, lag)) for lag in lags]
        poly = np.polyfit(np.log(lags), np.log(tau), 1)
        hurst = poly[0] * 2

        # Mean-reversion signal
        if hurst < 0.5:
            return 1  # Mean-reverting
        elif hurst > 0.5:
            return -1  # Trending
        return 0
```

**Key point**: `self.data` is always the historical slice **up to the current bar** — no look-ahead bias in backtest.

---

## Level 1b: Multi-Asset `tickers=[]` + `decisionFunction(row)`

**When to use**: Multiple tickers, per-ticker signals from yfinance, no external APIs. The strategy may read all tickers' history.

**Backtestable**: ✅ Yes — use `local_backtest()`, `local_optimize()`, `local_development()`

**Boilerplate**: Minimal. The framework calls `decisionFunction` once per ticker per bar and handles position sizing.

### Basic Example

```python
class GoldenButterflyMomBot(Bot):
    UNIVERSE = ["VTI", "IJS", "TLT", "SHY", "IAU"]
    BENCHMARK = "SPY"

    def __init__(self):
        super().__init__(
            "GoldenButterflyMomBot",
            tickers=self.UNIVERSE + [self.BENCHMARK],
            interval="1d",
            period="2y",
        )

    def decisionFunction(self, row):
        ticker = self._current_ticker
        if ticker == self.BENCHMARK:
            return 0  # Don't trade benchmark

        # Compute RRG signals from self.datas
        signals = self._compute_rrg_signals()
        return signals.get(ticker, 0)

    def _compute_rrg_signals(self):
        """Compute momentum signals using all tickers' history."""
        # self.datas[ticker] contains history up to current bar for each ticker
        spy_12m = self._log_return(self.datas["SPY"], 252)

        signals = {}
        for ticker in self.UNIVERSE:
            ticker_12m = self._log_return(self.datas[ticker], 252)
            rs_ratio = ticker_12m - spy_12m
            # ... RRG logic ...
            signals[ticker] = 1 if rs_ratio > 0 else -1
        return signals
```

**Key points**:
- `self._current_ticker` tells which ticker the current `decisionFunction` call is for
- `self.datas[ticker]` contains the full history (up to current bar) for each ticker
- Framework sets both before each call
- Position sizing is equal-weight across tickers (`target = total_value / N`)

---

## Level 2: Override `makeOneIteration()`

**When to use**: External APIs, AI models, portfolio-weight optimization, or custom data pipelines.

**Backtestable**: ❌ No — cannot be replayed on historical data. Must validate via live runs.

**Boilerplate**: Significant. You must fetch data, compute decisions, execute trades yourself.

### When Level 2 is Necessary

#### A. External APIs (Live-Only)

```python
from utils.portfolio import get_fear_greed_index

class FearGreedBot(Bot):
    def __init__(self):
        super().__init__("FearGreedBot", "QQQ", interval="1d", period="1y")

    def makeOneIteration(self):
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)

        # Fetch live Fear & Greed index — NOT available historically
        fg = get_fear_greed_index()
        if fg is None:
            return 0

        portfolio = self.dbBot.portfolio
        cash = portfolio.get("USD", 0)
        holding = portfolio.get(self.symbol, 0)

        # Execute based on live API data
        if fg >= 75 and cash > 0:
            self.buy(self.symbol)
            return 1
        elif fg <= 25 and holding > 0:
            self.sell(self.symbol)
            return -1
        return 0

# Cannot call: bot.local_backtest() ← RuntimeError
# Can only run: bot.run()  # Live execution
```

**Why not backtestable**: The `get_fear_greed_index()` call has no historical equivalent. You can't replay decisions that depend on "today's fear level."

#### B. Portfolio-Weight Optimization

```python
from utils.portfolio import TRADEABLE, sharpe_compute_weights

class SharpePortfolioOptBot(Bot):
    def __init__(self):
        super().__init__("SharpePortfolioOptBot", symbol=None)
        self.tickers = TRADEABLE

    def makeOneIteration(self):
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)

        # Fetch data for all tickers
        data_long = self.getYFDataMultiple(
            self.tickers,
            interval="1d",
            period="3mo",
            saveToDB=True,
        )

        # Convert to wide format (symbols as columns)
        wide = self.convertToWideFormat(data_long, value_column="close", fill_method="both")

        # Compute optimal weights via PyPortfolioOpt
        weights = sharpe_compute_weights(wide)

        # Rebalance to optimal allocation
        self.rebalancePortfolio(weights, onlyOver50USD=True)
        return 0

# Cannot call: bot.local_backtest() ← will use equal-weight, not Sharpe weights
# Can only run: bot.run()  # Live rebalancing
```

**Why not backtestable**: The backtest loop uses equal-weight position sizing per ticker (`target = total_value / N`), but the strategy's edge comes from **Sharpe-optimal weighting**. Backtesting with equal-weight silently produces a different strategy.

#### C. AI / LLM Models

```python
class AIResearchBot(Bot):
    def __init__(self):
        super().__init__("AIResearchBot", "QQQ", interval="1d", period="1y")

    def makeOneIteration(self):
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)

        # Fetch data and recent news/context
        data = self.getYFDataWithTA(saveToDB=True, interval=self.interval, period=self.period)
        recent_context = fetch_market_context()

        # Ask AI for decision
        decision_text = self.run_ai(
            system_prompt="You are a trading analyst.",
            user_message=f"Should I buy QQQ? Context: {recent_context}",
        )

        # Parse AI response and execute
        if "BUY" in decision_text.upper():
            self.buy("QQQ")
            return 1
        return 0

# Cannot call: bot.local_backtest() ← AI model behavior is not reproducible
# Can only run: bot.run()  # Live AI execution
```

**Why not backtestable**: AI model outputs are non-deterministic and change with model updates. You can't replay historical decisions.

---

## Trade-Offs: Which Pattern to Choose?

| Feature | Level 1/1b | Level 2 |
|---------|-----------|---------|
| **Backtestable** | ✅ Yes | ❌ No |
| **Hyperparameter tuning** | ✅ Via `local_optimize()` | ❌ Manual |
| **Development speed** | ⭐ Fast (minimal code) | 🐢 Slow (boilerplate) |
| **External APIs** | ❌ No | ✅ Yes |
| **Portfolio optimization** | ❌ (equal-weight only) | ✅ Custom weights |
| **AI integration** | ❌ No | ✅ Yes |
| **Confidence before deployment** | 🟢 High (backtested) | 🟡 Medium (live-only) |

**General rule**: If your signal can be computed from yfinance data alone, **always use Level 1/1b**. Only use Level 2 when you genuinely need external data or custom weighting.

---

## Why Self-Data Works Without Overriding `makeOneIteration`

In Level 1/1b bots, the base class `makeOneIteration()` automatically:

1. Fetches data: `data = self.getYFDataWithTA(...)`
2. **Sets `self.data = data`** ← This is new!
3. Sets `self.datasettings = (interval, period)`
4. Calls `decisionFunction(row)` for each row
5. Executes buy/sell based on the decision

So if you previously had:

```python
# OLD: unnecessary override
def makeOneIteration(self):
    self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
    data = self.getYFDataWithTA(...)
    self.data = data  # ← not needed anymore!
    self.datasettings = (...)  # ← not needed anymore!
    decision = self.getLatestDecision(data)
    # ... buy/sell ...
```

You can now delete the entire `makeOneIteration` override. The base class does the same thing, plus it's backtestable:

```python
# NEW: just use decisionFunction
def decisionFunction(self, row):
    # self.data is automatically available here!
    lookback = self.data.tail(50)
    # ... signal logic ...
    return 1
```

---

## Common Pitfalls

### ❌ Pitfall 1: Using `makeOneIteration` When You Don't Need It

```python
# WRONG: unnecessary override
class MyBot(Bot):
    def __init__(self):
        super().__init__("MyBot", "QQQ")

    def makeOneIteration(self):
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
        data = self.getYFDataWithTA(saveToDB=True, interval="1d", period="1y")
        decision = self.getLatestDecision(data)
        # ... buy/sell boilerplate ...
```

**Fix**: Remove `makeOneIteration`, just implement `decisionFunction`:

```python
# RIGHT: minimal code, backtestable
class MyBot(Bot):
    def __init__(self):
        super().__init__("MyBot", "QQQ")

    def decisionFunction(self, row):
        if row["momentum_rsi"] < 30:
            return 1
        return 0
```

### ❌ Pitfall 2: Trying to Backtest Level 2 Bots

```python
# WRONG: will crash
bot = FearGreedBot()
results = bot.local_backtest()  # ← NotImplementedError: not backtestable
```

**Fix**: Only use `local_backtest()` on Level 1/1b bots. For Level 2, validate via live runs:

```python
bot = FearGreedBot()
bot.run()  # Live execution, no backtest
```

### ❌ Pitfall 3: Confusing `self.data` and `self.datas`

- **`self.data`** (Level 1/1b single-asset): Full historical slice for the single ticker
- **`self.datas`** (Level 1b multi-asset): Dict mapping each ticker to its full slice: `self.datas["QQQ"]`, `self.datas["GLD"]`, etc.

```python
class GoldenButterflyBot(Bot):
    def decisionFunction(self, row):
        # WRONG: confusing the two
        # lookback = self.data  # ← This is None for multi-asset!

        # RIGHT: use self.datas for multi-asset
        lookback = self.datas[self._current_ticker].tail(50)
        # ... logic ...
```

---

## Next Steps

- **Ready to build?** Start with [Quick Start](../README.md#-quick-start) and implement a Level 1 bot.
- **Need backtesting?** See [Local Development & Testing](./local-development.md).
- **Want external APIs?** See [AI Tools](./ai-tools.md) and accept that you'll test via `bot.run()` only.
- **Multi-asset strategy?** See [Portfolio Management](./portfolio-management.md) for position sizing and rebalancing patterns.
