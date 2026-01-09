# Example Bots

Real-world bot implementations demonstrating different patterns and strategies.

## eurusdtreebot.py

Decision tree-based strategy for EUR/USD.

**Pattern**: Simple `decisionFunction()` with multiple thresholds

```python
class EURUSDTreeBot(Bot):
    def decisionFunction(self, row):
        if row["trend_sma_slow"] <= self.sma_slow_threshold:
            if row["trend_macd_signal"] <= self.macd_signal_threshold:
                return -1
            # ... more conditions
        return 0
```

## feargreedbot.py

Uses external Fear & Greed Index API.

**Pattern**: Override `makeOneIteration()` for external data

```python
class FearGreedBot(Bot):
    def makeOneIteration(self):
        index = get_fear_greed_index()
        if index < 20:  # Extreme fear
            self.buy("QQQ")
        elif index > 80:  # Extreme greed
            self.sell("QQQ")
        return 0
```

## sharpeportfoliooptweekly.py

Portfolio optimization with Sharpe ratio.

**Pattern**: Complex `makeOneIteration()` for multi-asset optimization

```python
class SharpePortfolioOptWeekly(Bot):
    def makeOneIteration(self):
        # Fetch multiple symbols
        data = self.getYFDataMultiple(["QQQ", "GLD", "TLT"])
        
        # Optimize portfolio
        weights = optimize_sharpe_ratio(data)
        
        # Rebalance
        self.rebalancePortfolio(weights)
        return 0
```

## xauzenbot.py

Gold (XAU) trading bot.

**Pattern**: Simple `decisionFunction()` with RSI and MACD

## gptbasedstrategytabased.py

GPT-based strategy with technical analysis.

**Pattern**: Uses LLM for decision making with TA indicators

## aihedgefundbot.py

AI-driven portfolio rebalancing.

**Pattern**: Reads decisions from external database and rebalances

## Learning from Examples

Each example demonstrates:
- Different implementation approaches
- Common patterns and best practices
- Real-world trading strategies
- Error handling and edge cases

## Next Steps

- [Creating a Bot](../getting-started/creating-a-bot.md) - Build your own
- [Bot Class System](../architecture/bot-class-system.md) - Understand patterns
