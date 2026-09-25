# InstitutionalFlowBot — walk-forward backtest, 2026-09-25

**Status: deployed as paper** (`15 21 * * 5`, Fridays). No copier holds it.

It has positive alpha against both QQQ and an equal-weight hold of its own
universe, in both halves of the sample, at a beta of about 0.4. It is
**unproven**: out of sample, t = 1.52. The market gate is the only component
whose effect is unambiguous.

## What it does

Every Friday it takes the S&P 100 (99 names) and runs four steps:

1. **Market gate.** If QQQ is below its 200-day MA, it holds cash.
2. **Mandate filter.** It keeps the stocks an institution could buy: 20-day
   average dollar volume of at least $50M, price of at least $10, and
   close > SMA50 > SMA200 with SMA200 rising.
3. **Ranking.** It ranks the survivors on a z-scored composite of seven
   components:
   - IBD relative strength
   - up/down volume ratio
   - Chaikin Money Flow
   - OBV slope
   - IBD accumulation minus distribution days
   - distance above the quarter-anchored VWAP
   - OBV-vs-price divergence
4. **Book.** It holds the top N at 1/N each. When fewer than N names pass, the
   rest stays in cash.

Code: `tradingbot/institutionalflowbot.py` and `tradingbot/utils/institutional_ta.py`.

Out of scope, because free daily data does not contain them: MOC imbalances,
dark-pool prints, ETF creations, 13F changes and options flow.

Fundamentals are captured daily from 2026-09-25 into `stock_fundamentals`. The
bot does not read them, because there is no history before capture to backtest
against.

## Method

`scripts/onetime_institutional_flow_backtest.py` runs offline: yfinance data, a
scratch SQLite DB, and the framework's own `backtest_bot` with 0.05% slippage
per side and the stage-2 no-trade band.

- **Universe:** the 95 S&P 100 names with daily history from before 2017.
  GEV, PLTR, SNDK and UBER are excluded, because the backtest inner-joins
  timestamps and one recent listing would truncate the whole window.
- **Windows:**
  - H1 = 2019–2022, used for the grid search.
  - H2 = 2023 → 2026-09, never seen by the search.
  - Features are computed on history from 2017, so both windows start warm.
- **Two benchmarks:**
  - **QQQ**, the fleet's target.
  - **An equal-weight buy-and-hold of the same 95 stocks** from the same start.
    Today's constituents are survivors, so the EW hold earns alpha with no
    signal at all. Beating it is what isolates stock selection.

## Results

Each alpha is annualised, with its t-stat in brackets. MaxDD is max drawdown.

| Config | Window | α vs QQQ | Beta | Corr | MaxDD | α vs EW universe | Trades |
|---|---|---|---|---|---|---|---|
| **top 20, weekly, gate** (H1 winner, new default) | H1 | +7.1% (1.20) | 0.30 | 0.56 | −21.9% | +6.1% (1.06) | 2,044 |
| | **H2** | **+9.3% (1.52)** | 0.46 | 0.62 | −11.9% | +6.4% (1.11) | 2,461 |
| | FULL | +9.2% (2.14) | 0.35 | 0.58 | −21.9% | +7.6% (1.83) | 4,482 |
| top 10, weekly, gate (old default) | H1 | +5.0% (0.71) | 0.35 | 0.56 | −26.7% | +3.8% (0.56) | 1,294 |
| | H2 | +10.2% (1.29) | 0.53 | 0.57 | −14.9% | +7.1% (0.94) | 1,545 |
| | FULL | +8.7% (1.63) | 0.41 | 0.55 | −26.7% | +6.8% (1.32) | 2,824 |
| top 10, **daily**, gate | H1 | +5.5% (0.80) | 0.29 | 0.50 | −20.5% | +4.6% (0.69) | 3,254 |
| | H2 | +5.1% (0.63) | 0.53 | 0.56 | −18.0% | +1.8% (0.23) | 3,634 |
| | FULL | +6.8% (1.28) | 0.37 | 0.51 | −20.5% | +5.2% (1.00) | 6,836 |
| *EW hold of the 95, for reference* | H1 | +3.1% (0.75) | 0.85 | | −33.8% | | |
| | H2 | +4.2% (1.39) | 0.78 | | −19.8% | | |
| | FULL | +3.8% (1.46) | 0.87 | | −33.8% | | |

Total return over the full 7.7 years was +258% for the new default and +266% for
the old one. `backtest_bot`'s `yearly_return` key is the total return over the
window despite its name.

### H1 grid (12 combinations, ranked by alpha_t)

| top_n | Rebalance | Gate | α vs QQQ | Beta | α vs EW |
|---|---|---|---|---|---|
| 20 | weekly | on | +7.1% (1.20) | 0.30 | +6.1% (1.06) |
| 20 | daily | on | +5.6% (0.93) | 0.27 | +4.8% (0.81) |
| 20 | weekly | off | +4.7% (0.82) | 0.39 | +3.5% (0.62) |
| 10 | daily | on | +5.5% (0.80) | 0.29 | +4.6% (0.69) |
| 10 | weekly | on | +5.0% (0.71) | 0.35 | +3.8% (0.56) |
| 5 | daily | on | +4.9% (0.61) | 0.31 | +4.1% (0.51) |
| 20 | daily | off | +2.9% (0.49) | 0.37 | +1.8% (0.32) |
| 10 | weekly | off | +3.0% (0.43) | 0.46 | +1.5% (0.22) |
| 5 | weekly | on | +3.2% (0.40) | 0.37 | +2.1% (0.27) |
| 10 | daily | off | +1.6% (0.23) | 0.41 | +0.3% (0.04) |
| 5 | weekly | off | +1.4% (0.16) | 0.51 | −0.3% (−0.03) |
| 5 | daily | off | −0.4% (−0.05) | 0.45 | −1.9% (−0.22) |

## Reading it honestly

- **Not proven.** The out-of-sample t is 1.52. The full-sample t of 2.14
  includes the half the parameters were chosen on, so it is not evidence of an
  edge. The bar is t ≥ 2 out of sample or in live trading.
- **The market gate is the robust part.** It wins in every one of the six
  pairings in the grid. It is also the main beta reducer: in H1, beta is
  0.27–0.37 with the gate and 0.37–0.51 without, against 0.85 for the EW hold.
- **Weekly beats daily.** Daily rebalancing at top 10 halves the H2
  alpha: +5.1% vs +10.2% against QQQ, and +1.8% vs +7.1% against EW. It does so
  on 2.3× the trades, so the extra rebalances mostly pay slippage on
  cut-off churn.
- **Top 20 beats top 10 and top 5.** A wider basket is less exposed to any
  single name. The new default gives up about 1 point of H2 alpha against top
  10, but has a higher t, lower beta, and 3 points less drawdown.
- **Correlation is about 0.6, not the < 0.5 preferred.** It is a long-only
  large-cap basket and cannot get far from the market. Its value to the book
  would come from the gate taking it to cash in drawdowns.
- **Survivorship is only partly handled.** The EW comparison cancels the bias of
  holding winners, but not every way today's list shapes which stocks pass the
  trend filter in 2019.

## Reproducing

```bash
RETUNE_SCRATCH=/tmp PYTHONPATH=. uv run python scripts/onetime_institutional_flow_backtest.py > instflow.json
```

It takes about 20 minutes, about 100 s of which is yfinance downloads.
