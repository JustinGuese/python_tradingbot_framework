# Daily-Bar TradingView Trend Strategies vs QQQ (2005-2026)

Evaluation of the four TradingView trend-following signals and ensemble voting
on daily bars across a wide cross-asset ETF universe (`UNIVERSE_WIDE`, 19 ETFs)
from 2005-01-03 to 2026-11-16.

Reproduce with:
```bash
POSTGRES_URI=postgresql://ci:ci@localhost:5432/ci poetry run python scripts/onetime_tv_daily_study.py --save-doc
```

## Setup and Methodology

- **Universe (`UNIVERSE_WIDE`):** `SPY QQQ IWM EFA EEM XLE XLF XLK XLV XLP XLU GLD SLV DBC USO TLT IEF HYG UUP` (19 liquid cross-asset ETFs with daily history back to ~2007; no leveraged/inverse).
- **Timeframe:** Daily bars (2005-2026), 5,503 sessions. Solves the low-sample and high-turnover failure modes of hourly bars.
- **Cost Model:** 5 bps one-way slippage/commission on turnover (framework default).
- **Allocation:** Long-only; equal weight among active tickers capped at 0.20 per ticker; maximum gross 1.0; idle held in cash.
- **Signals:**
  1. `supertrend`: Pine Script v5 ATR trailing ratcheting stop (`period=10, mult=3.0`).
  2. `ultimate_ma`: CM Ultimate MA direction filter (`kind=hma, length=20, slope_bars=1`).
  3. `alphatrend`: Kıvanç Özbilgiç AlphaTrend indicator (`period=14, mult=1.0`).
  4. `slingshot_chandelier`: Slingshot EMA pullback (34/50) + Chandelier Exit stop (22, 3.0).
- **Ensemble:** `ensemble_mv{1..4}` allocating to tickers receiving at least `min_votes` long signals.
- **Benchmark:** QQQ over identical trading sessions.
- **TSMOM Proxy:** 12-month trailing total return momentum sleeve on the same universe with identical 5bp costs.
- **Judging Rule:** Alpha vs QQQ and t-statistic. Mandatory honest verdict: `unproven` unless `t >= 2.0` and alpha positive in both halves.

## Results Summary (2005-2026)

| Strategy | Alpha/yr | t | Beta | Corr QQQ | CAGR | Max DD | H1 Alpha (t) | H2 Alpha (t) | Fit '05-'18 (t) | Judge '19-'26 (t) | TSMOM Corr | Incr Alpha vs TSMOM (t) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `supertrend` | +2.70% | +1.33 | 0.25 | 0.50 | +6.3% | -26.8% | +1.7% (+0.55) | +3.8% (+1.43) | +1.6% (+0.63) | +4.8% (+1.39) | 0.61 | +2.5% (+1.34) | `unproven` |
| `ultimate_ma` | -1.70% | -0.81 | 0.27 | 0.51 | +2.0% | -39.7% | -4.0% (-1.27) | +0.8% (+0.29) | -3.7% (-1.39) | +1.9% (+0.55) | 0.57 | -1.5% (-0.73) | `unproven` |
| `alphatrend` | +3.02% | +1.45 | 0.25 | 0.48 | +6.6% | -29.5% | +1.5% (+0.49) | +4.8% (+1.69) | +1.1% (+0.45) | +6.6% (+1.82) | 0.61 | +2.7% (+1.45) | `unproven` |
| `slingshot_chandelier` | +0.11% | +0.06 | 0.20 | 0.47 | +2.9% | -23.3% | +1.3% (+0.49) | -1.0% (-0.46) | +1.5% (+0.65) | -2.2% (-0.79) | 0.64 | -0.4% (-0.27) | `unproven` |
| `ensemble_mv1` | -0.40% | -0.21 | 0.34 | 0.64 | +4.5% | -40.2% | -2.3% (-0.82) | +1.8% (+0.72) | -1.8% (-0.77) | +2.4% (+0.74) | 0.69 | -0.0% (-0.00) | `unproven` |
| `ensemble_mv2` | +2.02% | +1.01 | 0.25 | 0.51 | +5.6% | -28.9% | +0.9% (+0.29) | +3.3% (+1.27) | +0.8% (+0.32) | +4.3% (+1.30) | 0.63 | +1.8% (+0.98) | `unproven` |
| `ensemble_mv3` | +1.24% | +0.61 | 0.21 | 0.43 | +4.1% | -23.4% | +0.0% (+0.01) | +2.5% (+0.91) | -0.2% (-0.08) | +3.8% (+1.10) | 0.57 | +0.8% (+0.42) | `unproven` |
| `ensemble_mv4` | +0.85% | +0.63 | 0.09 | 0.31 | +2.2% | -15.7% | +2.1% (+1.01) | -0.3% (-0.19) | +1.6% (+0.91) | -0.4% (-0.17) | 0.47 | +0.4% (+0.32) | `unproven` |
| `tsmom_proxy` | +1.75% | +0.97 | 0.32 | 0.64 | +6.5% | -26.9% | +2.5% (+0.91) | +0.8% (+0.35) | +1.2% (+0.50) | +2.7% (+0.93) | - | - | `unproven` |

## Key Findings

1. **Major improvement over hourly bars:**
   - On 1h bars (tested over 2.9 years), all four bots failed heavily due to turnover costs (alpha between -3.3% and +0.4%, t-stats negative or flat).
   - On daily bars across 21 years, **Supertrend** (+2.70%/yr alpha, t=+1.33) and **AlphaTrend** (+3.02%/yr alpha, t=+1.45) produce positive alpha with low beta (~0.25) and modest correlation (~0.50).
   - Both Supertrend and AlphaTrend are positive in both halves (H1 and H2) and in both Fit (2005-2018) and Judge (2019-2026) periods.
2. **Ultimate MA and Slingshot Chandelier underperform on defaults:**
   - `ultimate_ma` default (`hma, 20`) is too fast, generating negative alpha (-1.70%/yr, t=-0.81) from whip-saws.
   - `slingshot_chandelier` default is essentially flat alpha (+0.11%/yr, t=+0.06).
3. **Ensemble Voting (`ensemble_mv2`):**
   - Requiring $\ge 2$ votes (`ensemble_mv2`) filters false positives: +2.02%/yr alpha, t=+1.01, Beta 0.25, Max DD -28.9% (vs QQQ -80% during dot-com/GFC).
   - In the recent 2019-2026 out-of-sample period, `ensemble_mv2` generates +4.32%/yr alpha (t=+1.30).
4. **Diversification vs TSMOM Momentum:**
   - Correlation with standard 12-month TSMOM is moderate (0.57 - 0.64), not redundant.
   - Supertrend adds +2.47%/yr incremental alpha over TSMOM (t=+1.34), and AlphaTrend adds +2.74%/yr (t=+1.45).
5. **Honest Project Verdict:**
   - **`unproven` across all default configurations.** While alpha is positive and resilient across cycles for Supertrend, AlphaTrend, and Ensemble mv2, none of the untuned default parameter combinations reaches the required project bar of $t \ge 2.0$.
   - This motivates Step S1b: systematic walk-forward parameter tuning on the 2005-2018 fit period to evaluate whether robust, ship-worthy parameters emerge out-of-sample on 2019-2026.
