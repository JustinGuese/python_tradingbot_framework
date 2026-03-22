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

## deepseektoolbot.py

AI-driven portfolio research and rebalancing with tools. The main LLM uses tools (market data, news, earnings, insider trades, portfolio, recent trades) to research symbols and submits target weights via a custom `submit_portfolio_weights` tool. The cheap LLM then sanity-checks the submitted weights; if it rejects them, the bot retries once with the main LLM. Requires `OPENROUTER_API_KEY`.

**Pattern**: Override `get_ai_tools()` for custom tools; use main LLM for tool flow, cheap LLM for output validation and fallback

## ta_regime_bot.py (TARegimeAdaptiveBot)

Single-asset bot that uses **only historic OHLCV and TA** (no Fear & Greed). It classifies regime as **trend** vs **mean reversion** via a Hurst-style proxy (lag-1 autocorrelation of returns), then applies ADX/MACD/EMA in trend regime and RSI/Bollinger BBP (and optional z-score) in mean-reversion regime. All decision logic lives in `utils.ta_regime`; the bot only fetches data and delegates.

**Pattern**: Minimal bot; reusable logic in `utils.ta_regime`; `decisionFunction(row)` calls `ta_regime_decision(row, self.data, **self._ta_params)`.

For the mathematical and quant concepts (Hurst, R/S, variance ratio, z-score, Hilbert/Ehlers, entropy) and source links, see **[TA Regime Bot: Mathematical and Quant Concepts](ta-regime-bot.md)**.

## adaptivemeanreversionbot.py

Volatility-gated mean-reversion on QQQ.

**Pattern**: `decisionFunction()` with overridden `getYFDataWithTA()` to inject custom columns (200-day SMA, ATR rolling mean, prev-session high, BBW squeeze minimum). Fully backtestable.

```python
class AdaptiveMeanReversionBot(Bot):
    param_grid = {
        "wr_threshold": [-80, -85, -90, -95],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
    }

    def decisionFunction(self, row):
        # SELL: snap-back above previous session's high
        if prev_high > 0 and close > prev_high:
            return -1
        # BUY: oversold (WR < -90), above 200 SMA, calm ATR, no squeeze
        if close > sma_200 and wr < self.wr_threshold and atr < multiplier * atr_ma:
            return 1
        return 0
```

**Hyperparameters** (`param_grid`):
| Parameter | Grid | Effect |
|---|---|---|
| `wr_threshold` | -80, -85, -90, -95 | Depth of oversold required for entry |
| `atr_multiplier` | 1.5, 2.0, 2.5, 3.0 | How aggressive the volatility gate is |

Run `AdaptiveMeanReversionBot().local_optimize()` to find the best combination on recent data.

## goldenbutterflymombot.py

Golden Butterfly five-asset portfolio with Relative Rotation Graph (RRG) momentum overlay for weekly rebalancing.

**Pattern**: `makeOneIteration()` + `rebalancePortfolio()`. Multi-asset (Pattern B). Not backtestable.

```python
class GoldenButterflyMomBot(Bot):
    # Universe: VTI, IJS, TLT, SHY, IAU — benchmark: SPY
    def makeOneIteration(self):
        # Compute RS-Ratio and RS-Momentum vs SPY (z-scored)
        # Classify: Leading / Weakening / Improving / Lagging
        # OBV rising → upgrade Improving to full weight
        # CMF < 0   → downgrade Leading to half weight
        # Lagging   → redirect to SHY (or USD if SHY is also Lagging)
        self.rebalancePortfolio(target, onlyOver50USD=True)
```

**RRG Quadrant → Weight:**
| Quadrant | Condition | Weight |
|---|---|---|
| Leading + CMF ≥ 0 | rs_ratio_z > 0 AND rs_mom_z > 0 | 20% |
| Leading + CMF < 0 | distribution warning | 10% → 10% to SHY |
| Improving + OBV↑ | rs_ratio_z < 0 AND rs_mom_z > 0, volume confirm | 20% |
| Improving, no OBV | accumulation unconfirmed | 10% → 10% to SHY |
| Weakening | rs_ratio_z > 0 AND rs_mom_z < 0 | 10% → 10% to SHY |
| Lagging | rs_ratio_z < 0 AND rs_mom_z < 0 | 0% → 20% to SHY |

**Research basis**: [Strategic Synthesis of Adaptive Mean Reversion and Multi-Asset Rotation](Strategic-Synthesis-of-Adaptive-Mean-Reversion-and-Multi-Asset-Rotation.md) — covers RRG mathematics, UIS scaling, volatility clustering theory, and the 2026 ETF landscape.

## stocknewssentimentbot.py

AI-driven news sentiment trading across multiple symbols.

**Pattern**: `makeOneIteration()` — reads the `stock_news` DB table (populated nightly by `calculate_portfolio_worth`), classifies aggregate headline sentiment per symbol via AI, and executes trades on medium/high-confidence signals.

```python
class StockNewsSentimentBot(Bot):
    def makeOneIteration(self):
        # Fetch unacted news rows from last 2 days
        # Mark acted_on=True BEFORE AI call (crash-safe deduplication)
        # AI returns {"direction": "BUY"|"SELL"|"HOLD", "confidence": "low"|"medium"|"high"}
        # Buy 20% of cash per BUY signal; sell all on SELL signal
```

**Key details:**
| Setting | Value |
|---|---|
| Lookback window | 2 days of headlines |
| Max headlines per AI call | 5 per symbol |
| Position size | 20% of cash per BUY |
| Confidence filter | medium or high only |
| Crash safety | `acted_on` flag set before AI call |

Schedule: `30 22 * * 1-5` — runs 30 min after `calculate_portfolio_worth` (which refreshes the news feed).

## synthesizedhyperconvexitybot.py

Five-layer leveraged ETF strategy trading TQQQ / SQQQ / IEF.

**Pattern**: `makeOneIteration()` — multi-instrument, multi-data-source. Not backtestable with the built-in engine.

```python
class SynthesizedHyperConvexityBot(Bot):
    def makeOneIteration(self):
        # Stage 2 (QQQ > rising 30-wk SMA) + squeeze + VIX declining → buy TQQQ
        # Stage 4 (QQQ < falling 30-wk SMA) + F&G not panicking → buy SQQQ
        # Stage 1 / 3 / unknown → rotate to IEF
        # Black Swan CB: TQQQ ≥-20% or QQQ ≥-7% → liquidate all → IEF
```

**Signal stack:**
| Layer | Indicator | Role |
|---|---|---|
| Stage Analysis | 30-week SMA slope on QQQ | Macro regime (direction) |
| BB/KC Squeeze | Bollinger Bands inside Keltner Channels | Entry timing |
| VIX Trend | VIX vs 20-day MA | Gamma unwind confirmation |
| Sentiment Gate | Fear & Greed (>75 / <25) | Contrarian filter at extremes |
| Black Swan CB | Daily TQQQ/QQQ drop thresholds | Hard crash exit |

**Position sizing**: Vol-of-Vol Kelly proxy — ATR/price ratio scales stake between 25% (high vol) and 80% (low vol) of available cash.

**Research basis**: [Synthesized Hyper-Convexity Engine](synthesized-hyper-convexity-engine.md.md) — covers leveraged ETF volatility drag, Weinstein Stage Analysis, BB/KC squeeze mechanics, gamma squeeze dynamics, and Vol-of-Vol Kelly sizing.

## recursivedecayharvestbot.py (RecursiveDecayHarvestBot)

TQQQ regime-switching strategy with dual crash filter. Holds 3x Nasdaq-100 (TQQQ) during
QQQ uptrends with calm volatility; exits to cash on UVXY RSI spike or QQQ SMA200 breakdown.
Captures leveraged (3×) upside in bull markets while avoiding the beta-slippage decay spiral
that destroys leveraged ETF holders in bear/volatile regimes.

**Pattern**: `decisionFunction()` with overridden `getYFDataWithTA()` to inject multi-symbol
derived columns (QQQ SMA200 + close, UVXY RSI). Backtestable. `getYFDataMultiple` fetches
raw OHLCV for QQQ and UVXY; RSI and SMA are computed in `_enrich()` and date-merged onto
TQQQ rows.

```python
class RecursiveDecayHarvestBot(Bot):
    param_grid = {
        "uvxy_rsi_exit": [55, 60, 65, 70],
        "sell_buffer": [0.02, 0.05, 0.08, 0.12],
    }

    def decisionFunction(self, row):
        # SELL: UVXY RSI spike — volatility panic, decay accelerates
        if uvxy_rsi > self.uvxy_rsi_exit:
            return -1
        # SELL: QQQ trend broken with sell_buffer gap
        if qqq_close < qqq_sma200 * (1 - self.sell_buffer):
            return -1
        # BUY: QQQ uptrend + calm UVXY → hold TQQQ
        if qqq_close > qqq_sma200 and uvxy_rsi <= self.uvxy_rsi_exit:
            return 1
        return 0
```

**Instruments:** TQQQ (primary), QQQ (trend filter), UVXY (crash filter)

**Hyperparameters** (`param_grid`):
| Parameter | Grid | Effect |
|---|---|---|
| `uvxy_rsi_exit` | 55, 60, 65, 70 | UVXY RSI threshold: lower = exit sooner on fear spikes |
| `sell_buffer` | 0.02, 0.05, 0.08, 0.12 | QQQ SMA breakdown buffer: wider = fewer SMA exits |

Run `RecursiveDecayHarvestBot().local_optimize()` to find the best combination on recent data.

**Research basis**: [Recursive Adversarial Arbitrage](Recursive-Adversarial-Arbitrage.md) — covers
volatility decay theory, beta slippage mathematics, dark pool footprints, and multi-agent AI
consensus frameworks for leveraged ETF strategies.

## Learning from Examples

Each example demonstrates:
- Different implementation approaches
- Common patterns and best practices
- Real-world trading strategies
- Error handling and edge cases

## Next Steps

- [Creating a Bot](../getting-started/creating-a-bot.md) - Build your own
- [Bot Class System](../architecture/bot-class-system.md) - Understand patterns
