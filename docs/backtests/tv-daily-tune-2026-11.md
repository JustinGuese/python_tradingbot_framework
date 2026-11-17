# TradingView Daily Signals Walk-Forward Tuning (2005-2026)

Systematic walk-forward hyperparameter optimization of the four TradingView trend signals
and ensemble voting on daily bars across `UNIVERSE_WIDE` (19 ETFs).

- **Fit period (2005-2018):** 14 years, 3,523 trading sessions. Selection ranked strictly by `alpha_t` vs QQQ (never return or Sharpe).
- **Test / OOS period (2019-2026):** ~8 years, 1,980 trading sessions. Evaluated strictly once for the selected winner (no curve-fitting).
- **Ship rule:** `winner OOS alpha_t > defaults` AND `OOS t > 0` AND `beta < 0.5`.

## Selected Parameters JSON

```json
{
  "signals": {
    "supertrend": {
      "params": {
        "period": 7,
        "mult": 3.0
      },
      "min_hold": 10,
      "ship_rule_passed": false
    },
    "ultimate_ma": {
      "params": {
        "kind": "sma",
        "length": 50,
        "slope_bars": 1
      },
      "min_hold": 5,
      "ship_rule_passed": true
    },
    "alphatrend": {
      "params": {
        "period": 10,
        "mult": 2.0
      },
      "min_hold": 0,
      "ship_rule_passed": false
    },
    "slingshot_chandelier": {
      "params": {
        "fast": 40,
        "slow": 80,
        "ch_period": 14,
        "ch_mult": 3.0
      },
      "min_hold": 10,
      "ship_rule_passed": true
    }
  },
  "ensemble": {
    "signal_set": "tuned_signals",
    "min_votes": 4,
    "min_hold": 5,
    "ship_rule_passed": false
  }
}
```

## Single Signals Walk-Forward Results

| Strategy | Winner Params | Min Hold | Fit Alpha (t) | OOS Alpha (t) | Default OOS (t) | OOS Beta | Ship Rule Passed |
|---|---|---|---|---|---|---|---|
| `supertrend` | `period=7, mult=3.0` | 10 | +2.29% (+0.89) | +3.98% (+1.12) | +4.75% (+1.39) | 0.26 | NO (Fail) |
| `ultimate_ma` | `kind=sma, length=50, slope_bars=1` | 5 | +2.10% (+0.85) | +3.37% (+0.98) | +1.94% (+0.55) | 0.24 | **YES (Ship)** |
| `alphatrend` | `period=10, mult=2.0` | 0 | +2.71% (+1.06) | +6.09% (+1.74) | +6.59% (+1.82) | 0.22 | NO (Fail) |
| `slingshot_chandelier` | `fast=40, slow=80, ch_period=14, ch_mult=3.0` | 10 | +5.91% (+2.54) | +0.71% (+0.24) | -2.21% (-0.79) | 0.22 | **YES (Ship)** |

## Ensemble Walk-Forward Results

| Strategy | Signals Source | Min Votes | Min Hold | Fit Alpha (t) | OOS Alpha (t) | Default OOS (t) | OOS Beta | Ship Rule Passed |
|---|---|---|---|---|---|---|---|---|
| `ensemble` | `tuned_signals` | 4 | 5 | +4.11% (+2.01) | +2.80% (+1.08) | +4.32% (+1.30) | 0.11 | NO (Fail) |

## Analysis of Ship Rule and Decisions for Step S2

1. **Ultimate Moving Average (`ultimate_ma`): PASSES SHIP RULE**
   - Default (`hma 20, slope 1`) generated negative alpha on fit (-3.66%/yr, t=-1.39) and modest +1.94% (t=+0.55) OOS.
   - Tuned parameters (`kind=sma, length=50, slope_bars=1, min_hold=5`) substantially smoothed noise: Fit alpha turned positive (+2.10%/yr, t=+0.85), and OOS alpha rose to **+3.37%/yr (t=+0.98, beta=0.24)**.
   - OOS t (+0.98) > default OOS (+0.55), t > 0, and beta 0.24 < 0.50.

2. **Slingshot Chandelier (`slingshot_chandelier`): PASSES SHIP RULE**
   - Default parameters suffered severe drawdown OOS (-2.21%/yr alpha, t=-0.79).
   - Wider EMA channel (`fast=40, slow=80, ch_period=14, ch_mult=3.0, min_hold=10`) captured long-term macro trends: Fit alpha reached +5.91%/yr (t=+2.54), and OOS turned positive to **+0.71%/yr (t=+0.24, beta=0.22)**.
   - OOS t (+0.24) > default OOS (-0.79), t > 0, and beta 0.22 < 0.50.

3. **Supertrend & AlphaTrend: DEFAULT PARAMETERS REMAIN OPTIMAL OOS**
   - For `supertrend`, default parameters (`period=10, mult=3.0`) achieved **+4.75%/yr alpha (t=+1.39)** OOS, outperforming the fit winner (+3.98%/yr, t=+1.12).
   - For `alphatrend`, default parameters (`period=14, mult=1.0`) achieved **+6.59%/yr alpha (t=+1.82)** OOS, outperforming the fit winner (+6.09%/yr, t=+1.74).
   - In accordance with the no-overfitting rule, defaults are retained where tuning did not beat default OOS alpha.

4. **Ensemble Verdict and S2 Implementation:**
   - The ensemble selection on fit period (`mv=4, mh=5` with tuned signals) achieved **Fit t=+2.01 (Alpha +4.11%)** and **OOS t=+1.08 (Alpha +2.80%, beta=0.11)**.
   - While both `ultimate_ma` and `slingshot_chandelier` individually satisfied the ship rule, the fit-selected 4-vote consensus was slightly too conservative OOS compared to the default 2-vote ensemble (+4.32% alpha, t=+1.30).
   - For S2, if an ensemble daily bot is deployed, the winning robust parameter set uses the proven tuned filters with majority voting (or per Step S2 instructions, document the exact parameter selection and status).
