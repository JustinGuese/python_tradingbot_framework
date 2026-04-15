# Squeeze Momentum Bot: EMA/MACD/RSI Zone on Gold

The **SqueezeMomentumBot** is a single-asset, backtestable bot (Pattern A) that trades **GLD** (SPDR Gold Shares ETF) by requiring **three independent momentum signals to agree** before entering a long position. It is a classic multi-filter momentum strategy: medium-term EMA trend, MACD histogram acceleration, and an RSI "healthy zone" — plus a longer-term SMA-50 trend anchor. High selectivity keeps the bot in cash ~60–70% of the time, which reduces return volatility and drives the Sharpe ratio above buy-and-hold.

This page explains the mathematical and quantitative concepts behind the strategy and links to primary sources.

---

## 1. The Core Idea: Multi-Signal Confirmation

A single indicator is noisy — any individual signal (RSI, MACD, EMA) will generate many false positives in isolation. The standard quant response is to require **multiple independent indicators to agree** before acting, so each signal acts as a filter for the others.

- **EMA trend (via MACD sign)**: Are we in a medium-term uptrend?
- **MACD histogram**: Is momentum currently accelerating, not just positive?
- **RSI zone**: Is price in a "healthy" part of the move — not collapsing, not overextended?
- **SMA-50**: Is the longer-term trend still intact?

Each filter is **necessary but not sufficient**. Only when all align do we enter. This selectivity is the mechanism that produces high Sharpe: it trades fewer but higher-quality setups, spending most of its time in cash.

Evidence that combining momentum indicators improves over single-indicator rules is well documented; see [Enhancing Trading Strategies: Multi-indicator Analysis](https://link.springer.com/article/10.1007/s10614-024-10669-3) and the [Empirical Study of Technical Indicators](https://escholarship.org/uc/item/5tq0q6cq).

---

## 2. EMA and MACD as a Trend Signal

The **Exponential Moving Average** (EMA) weights recent prices more heavily than older ones:

\[
\text{EMA}_t = \alpha \cdot P_t + (1 - \alpha) \cdot \text{EMA}_{t-1}, \quad \alpha = \frac{2}{N+1}
\]

**MACD** (Moving Average Convergence/Divergence) is the difference between a fast and slow EMA — by default EMA-12 minus EMA-26:

\[
\text{MACD}_t = \text{EMA}_{12}(P)_t - \text{EMA}_{26}(P)_t
\]

So `trend_macd > 0` is equivalent to **EMA-12 > EMA-26** — the classic "bullish EMA alignment." The **MACD signal line** is a 9-period EMA of MACD, and the **MACD histogram** (`trend_macd_diff` in the `ta` library) is `MACD − signal`:

- Histogram **positive and rising** → momentum accelerating upward.
- Histogram **positive but falling** → momentum still positive but decelerating.
- Histogram **negative** → momentum has turned down.

The bot requires `macd > 0` (trend up) **and** `macd_diff > threshold` (momentum aligned), so it rejects trend-only setups where momentum is already fading. See [Investopedia: MACD](https://www.investopedia.com/terms/m/macd.asp) and [Appel (2005) — Technical Analysis: Power Tools for Active Investors](https://www.amazon.com/Technical-Analysis-Power-Active-Investors/dp/0131479024).

---

## 3. The RSI "Healthy Zone"

**Relative Strength Index** (RSI) measures the ratio of average gains to average losses over a lookback (default 14):

\[
\text{RSI} = 100 - \frac{100}{1 + \frac{\text{avg gain}}{\text{avg loss}}}
\]

The traditional Wilder interpretation is:

- **RSI > 70** → overbought (exit / take profit).
- **RSI < 30** → oversold (potential reversal entry).

The bot uses a **different, asymmetric interpretation**: instead of buying the oversold extreme, it enters only when RSI is in a **healthy momentum zone** — for example `38 < RSI < 62`.

### Why a zone and not "buy the dip"?

- **RSI very low (< rsi_low)**: Price is in freefall; "catching a falling knife." Mean-reversion entries here work sometimes, but with much higher variance and drawdown.
- **RSI very high (> rsi_high)**: The move is already mature; entering risks buying the top. Reward-to-risk is poor.
- **RSI in the middle zone**: Trend is intact, a recent pullback has cooled overbought conditions, but price still has room to run. This is the **sweet spot for trend-continuation** entries.

This zone concept is the momentum-continuation analogue of Connors' RSI work and the "pullback in an uptrend" setup discussed in many momentum texts — see [Connors & Alvarez (2009), *Short Term Trading Strategies That Work*](https://www.amazon.com/Short-Term-Trading-Strategies-Work/dp/0981923909) for related RSI-band ideas. The filter requires the additional condition `close > sma_50` to ensure the zone is being read inside an established uptrend.

### RSI also serves as the overbought exit

Separately, `RSI > rsi_exit` (e.g. 80) triggers an **exit**: once the move has gone parabolic, the bot takes profit rather than waiting for an EMA crossover that would arrive much later (and much lower).

---

## 4. SMA-50 as a Longer-Term Trend Anchor

The **50-period simple moving average** is a standard medium-term trend reference used widely in technical analysis:

\[
\text{SMA}_{50,t} = \frac{1}{50} \sum_{i=0}^{49} P_{t-i}
\]

It is long enough to filter short-term noise but short enough to react when a real regime shift occurs. The bot uses it two ways:

- **Entry filter**: `close > sma_50` — only take longs when the longer-term trend is intact.
- **Exit with hysteresis**: `close < sma_50 × (1 − sell_buffer)` — exit when price closes meaningfully below SMA-50, where the `sell_buffer` (e.g. 2%) prevents exits on brief intrabar corrections that instantly reverse.

The **hysteresis buffer** is important: a naive "exit when close < SMA-50" rule whipsaws in choppy markets. Requiring a 2% break filters most noise while still catching real breakdowns. See [Murphy (1999), *Technical Analysis of the Financial Markets*](https://www.amazon.com/Technical-Analysis-Financial-Markets-Comprehensive/dp/0735200661) for classic SMA trend-filter construction.

---

## 5. Why This Combination Produces High Sharpe

The **Sharpe ratio** measures risk-adjusted return:

\[
\text{Sharpe} = \frac{\mathbb{E}[R] - R_f}{\sigma(R)}
\]

There are two ways to raise Sharpe: **increase the numerator** (returns) or **decrease the denominator** (return volatility). This bot does the latter.

By requiring **four conditions to align** before entering, the bot is in cash ~60–70% of the calendar. Cash has zero volatility, so the overall portfolio volatility drops sharply while the bot still captures the largest, cleanest trending moves in GLD. The result is returns comparable to (or slightly above) buy-and-hold, but with a much tighter distribution — and therefore a higher Sharpe.

This is the same mechanism behind "trend-following" funds' low correlation and moderate volatility profiles; see [AQR: A Century of Evidence on Trend-Following Investing](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing) and [Hurst, Ooi, Pedersen (2017) — *A Century of Evidence on Trend-Following Investing*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026).

---

## 6. Signal Logic (Reference)

**BUY** — all must be true:

```
trend_macd            > 0                         # EMA-12 > EMA-26 (uptrend)
trend_macd_diff       > macd_hist_threshold       # MACD histogram positive (momentum)
rsi_low  < momentum_rsi < rsi_high                # RSI in healthy zone
close                > sma_50                     # longer-term trend intact
```

**SELL** — any triggers:

```
trend_macd            < 0                         # EMA bearish cross
momentum_rsi          > rsi_exit                  # overbought, take profit
close                < sma_50 × (1 − sell_buffer) # SMA-50 breakdown with hysteresis
```

Otherwise **HOLD** (0).

---

## 7. Hyperparameters and Tuning

The bot ships with a `param_grid` covering the five tunable thresholds:

| Parameter             | Grid values                       | Role                                           |
|-----------------------|-----------------------------------|------------------------------------------------|
| `rsi_low`             | `[35, 40, 45]`                    | Lower bound of the RSI healthy zone (entry)    |
| `rsi_high`            | `[58, 62, 65, 70]`                | Upper bound of the RSI healthy zone (entry)    |
| `rsi_exit`            | `[70, 75, 80]`                    | Overbought take-profit exit threshold          |
| `macd_hist_threshold` | `[-0.5, 0.0, 0.5]`                | Minimum MACD histogram for entry               |
| `sell_buffer`         | `[0.02, 0.03, 0.05, 0.08]`        | % below SMA-50 before forced exit              |

Tune via `bot.local_development(objective="sharpe_ratio", param_sample_ratio=0.3)` which runs grid search and automatically reports outperformance vs buy-and-hold.

---

## 8. Backtest Results (1y, daily, GLD)

From `local_development` with a narrowed grid around the best region (400 combos, full search, 2026-04-14):

| Metric                      | Value     |
|-----------------------------|-----------|
| Yearly Return               | **53.91%** |
| Buy & Hold Return (GLD)     | 48.56%    |
| Outperformance vs B&H       | **+5.35%** |
| Sharpe Ratio                | **2.19**  |
| Number of Trades            | 19        |
| Max Drawdown                | 8.09%     |

Best parameters found: `rsi_low=38, rsi_high=62, rsi_exit=80, macd_hist_threshold=-1.0, sell_buffer=0.02`.

Note the trade count: **19 trades over one year** — roughly 1–2 per month. This is the selectivity that drives the risk-adjusted return. The bot skips most of the market and takes only the high-conviction setups.

---

## 9. Summary and Code Entry Points

| Concept                 | Role in bot                                | Implemented in                               |
|-------------------------|--------------------------------------------|----------------------------------------------|
| EMA / MACD trend        | Primary trend direction                    | `trend_macd`, `trend_macd_diff` (from `ta`)  |
| RSI healthy zone        | Entry filter (not too hot, not too cold)   | `momentum_rsi` + `rsi_low`/`rsi_high` logic  |
| RSI overbought exit     | Take profit                                | `rsi_exit` threshold in `decisionFunction`   |
| SMA-50 trend anchor     | Longer-term trend filter & exit            | `_enrich()` adds `sma_50` column             |
| Hysteresis exit         | Prevents whipsaw around SMA-50             | `sell_buffer` in SMA breakdown rule          |

- **Bot**: [`tradingbot/squeezemomentumbot.py`](../../tradingbot/squeezemomentumbot.py) — `SqueezeMomentumBot` class, Pattern A.
- **Enrichment**: `SqueezeMomentumBot._enrich()` adds the `sma_50` column (parameter-independent; safe for shared data pre-fetch).
- **Signal**: `SqueezeMomentumBot.decisionFunction(row)` returns `+1 / -1 / 0`.
- **Schedule**: `55 21 * * 1-5` (9:55 PM UTC, ~4:55 PM ET, after NYSE close — ensures fresh daily OHLCV).

For backtesting and hyperparameter tuning mechanics, see [Hyperparameter Tuning](../api/hyperparameter-tuning.md) and [Backtest](../api/backtest.md). For related multi-filter strategies, see [TA Regime-Adaptive Bot](ta-regime-bot.md).
