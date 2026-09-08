# TSMOMTrendBot — backtest

Long-only time-series momentum across 18 sector-proxy ETFs, each leg vol-targeted.
Signal: trailing 12-month total return > 0 → long, else cash.

**Status: deployed** (`5 21 * * 1-5`).

## What this is, and what it is not

This is the **long half of time-series momentum, unlevered**. Moskowitz/Ooi/Pedersen
(2012) go short the negative-momentum legs and lever the book to a portfolio vol
target; this framework's portfolios are long-only with gross capped at 1.0, so
neither is possible. Concretely:

- A negative-momentum leg goes to **cash, not short**. The short book historically
  supplied a meaningful minority of TSMOM's return and most of its crisis-alpha
  convexity, so this equity curve is a slower, less crisis-responsive thing than
  the published series.
- Gross exposure is capped at 1.0, so in calm markets the vol-targeted weights sum
  to well under 1 and the rest sits in cash. Absolute risk is lower than the
  paper's and varies over time.
- ETFs, not futures: no contract multipliers, no rolls, no futures data source.

**Do not present this as a replication of MOP2012.** It is an ETF-proxy long-only
trend sleeve and should be labelled that way to anyone who asks.

## Result

Run via `backtest_bot` on adjusted (total-return) daily closes, `BACKTEST_PERIOD="max"`,
default cost model (5 bps slippage, `EXECUTION_CONFIG` no-trade band).

| | |
|---|---|
| Window | 2007-04-10 → 2026-09-04 (**19.40 years**, 4,884 bars) |
| Total return | **+105.5%** |
| CAGR | **+3.78%** |
| Sharpe | **0.46** |
| Max drawdown | **23.8%** |
| Annualized vol | 8.9% |
| Trades | 14,885 (~767/yr) |
| Equal-weight buy & hold, same universe | +392.7% |

The window is bounded by the shortest series in the universe (DBA and UUP both
start in 2007), so it includes 2008, 2020 and 2022 in full.

## Reading it honestly

**It underperforms buy-and-hold by a lot, and that is not by itself a defect.** A
trend follower holding cash through drawdowns will lag a levered-long-equity
benchmark across a period that was mostly a bull market. The case for the sleeve
is the risk profile — 8.9% vol and a 23.8% max drawdown against a universe whose
own equal-weight buy-and-hold path is far more violent — plus its correlation to
everything else in the book.

**Sharpe 0.46 over 19 years is a real but unexciting number.** It is in the right
neighbourhood for published long-only trend variants, which is the relevant
comparison, not the long-short index.

**Turnover is the clearest available improvement.** 767 trades/yr means each leg
is re-traded roughly every six days, driven by daily re-estimation of realized
vol rather than by the (very slow) 12-month signal. Widening the no-trade band
recovers part of it:

| `rebalance_band_pct` | CAGR | Sharpe | Max DD | Trades |
|---|---|---|---|---|
| 0.05 (shipped default) | +3.78% | 0.46 | 23.8% | 14,888 |
| 0.10 | +3.98% | 0.49 | 23.1% | 7,551 |
| 0.20 | +4.10% | 0.50 | 22.2% | 3,352 |

That band is a **deployment-wide** `EXECUTION_CONFIG` setting shared by all 20+
bots, so it was not changed here. The per-bot fix is to quantize the weights this
bot emits so vol jitter below some threshold does not move the target at all —
worth doing, not done.

## Correlation to the other new sleeves

Daily returns, overlapping window 2011-11-11 → 2026-07-17 (3,673 bars):

| | TSMOM | Carry | ShortVol |
|---|---|---|---|
| **TSMOM** | 1.000 | 0.335 | 0.331 |
| **Carry** | 0.335 | 1.000 | -0.015 |
| **ShortVol** | 0.331 | -0.015 | 1.000 |

0.335 against carry is low, but it is not the near-zero the "trend + carry" pitch
usually implies — and it is moot in practice, since the carry sleeve is not
deployed (see [multiassetcarrybot.md](multiassetcarrybot.md)).

## Reproducing

```python
bot = TSMOMTrendBot()
bot.local_backtest(initial_capital=10000.0)
# parameter sweep (param_grid: lookback_days, vol_window, target_vol)
bot.local_development(objective="sharpe_ratio", param_sample_ratio=0.3)
```

The numbers above are from a single default-parameter run, **not** from an
optimized sweep. Nothing here is in-sample-fitted, which is the reason to trust
the Sharpe more than its modest size might suggest — and the reason a sweep should
be treated as fitting, not as improvement.
