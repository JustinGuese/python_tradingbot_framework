# Long/flat trend on Darwinex-tradable instruments (2026-11-14)

Script: `scripts/onetime_trend_darwinex_study.py`. 13 instruments (4 FX majors, 4 indices, 5 commodity
futures via yfinance), 12-1 momentum > 0 => long else flat, inverse-vol weights, monthly rebalance,
signal lagged one day, gross <= 1, 5 bp turnover cost, idle cash 0%. No shorts. Nothing fitted.
Window 2009-01 to 2026-11, split at 2017-12-11. Alpha vs QQQ per the CLAUDE.md method.

| Sample | Alpha/yr | t | Beta | Corr |
|---|---|---|---|---|
| H1 | -0.52% | -0.32 | 0.15 | 0.48 |
| H2 | +1.51% | 0.92 | 0.11 | 0.47 |
| Full | +0.62% | 0.54 | 0.12 | 0.47 |

CAGR 3.06%, max drawdown -18.5%.

**Verdict: unproven, no edge.** Long-only trend on this universe is mostly flat-or-long exposure with
alpha near zero and t 0.54. Trend's usual payoff comes from the short side, which is out of scope.
Darwinex is therefore not a fit for this book without shorts. Do not build a bot.
