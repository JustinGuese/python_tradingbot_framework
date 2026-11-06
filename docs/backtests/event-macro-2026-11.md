# Macro Calendar Drift (FOMC / CPI / NFP) — Backtest Study

**Verdict:** `unproven` (the worker wrote `edge`; corrected: 2 of 40 variants pass, which is about what chance gives, and neither is an equity trade)

## Overview

Tests whether trading scheduled macroeconomic announcements (FOMC rate decisions, CPI inflation, and NFP jobs reports) across key asset classes (SPY, QQQ, TLT, GLD, UUP) yields statistically significant abnormal returns or alpha vs holding QQQ.

- **Data Source:** `macro_events` PostgreSQL table (past dates only, strictly up to current session).
- **Universe:** SPY (large cap), QQQ (tech/growth), TLT (20y Treasury), GLD (gold), UUP (US dollar).
- **Benchmark:** QQQ (daily close-to-close).
- **Windows:**
  - `[-1,0]`: Enter T-1 close, hold through event session T0.
  - `[0,+1]`: Enter T0 close, hold through session T+1.
  - `[0,+2]`: Enter T0 close, hold through session T+2.
- **Strategy:** Hold the indicated asset only during the event window; 100% cash otherwise.
- **Evaluation bar (CLAUDE.md):** `t >= 2.0`, positive alpha in **both** halves (`H1 > 0`, `H2 > 0`), and `beta < 0.5`.

## Pre-registered Variants Table (40 variants)

| Kind | Asset | Window | N Events | AR Mean | AR t | Win% | H1 AR | H2 AR | Strat Alpha | Strat t | Beta | H1 Alpha (t) | H2 Alpha (t) | Pass? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FOMC | SPY | [-1,0] | 171 | -0.05% | -1.27 | 46.2% | +0.03% | -0.12% | +0.75% | 1.01 | 0.02 | +2.41% (2.56) | -1.35% (-1.18) | FAIL |
| FOMC | SPY | [0,+1] | 171 | -0.06% | -1.23 | 50.9% | -0.11% | -0.01% | -1.08% | -1.45 | 0.02 | -1.82% (-1.69) | -0.64% (-0.63) | FAIL |
| FOMC | SPY | [0,+2] | 171 | -0.10% | -1.42 | 42.7% | -0.14% | -0.05% | -1.92% | -2.06 | 0.03 | -1.83% (-1.42) | -2.54% (-1.91) | FAIL |
| FOMC | QQQ | [-1,0] | 171 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | +1.38% | 1.63 | 0.03 | +2.82% (2.91) | -0.64% (-0.47) | FAIL |
| FOMC | QQQ | [0,+1] | 171 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | -0.86% | -1.03 | 0.03 | -1.55% (-1.49) | -0.62% (-0.49) | FAIL |
| FOMC | QQQ | [0,+2] | 171 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | -1.56% | -1.51 | 0.04 | -1.12% (-0.87) | -2.77% (-1.72) | FAIL |
| FOMC | TLT | [-1,0] | 171 | +0.19% | 2.55 | 57.9% | +0.06% | +0.32% | +1.21% | 2.04 | -0.01 | -0.42% (-0.54) | +2.85% (3.19) | FAIL |
| FOMC | TLT | [0,+1] | 171 | +0.01% | 0.06 | 53.2% | +0.04% | -0.03% | +0.45% | 0.69 | -0.01 | +1.03% (1.24) | -0.12% (-0.12) | FAIL |
| FOMC | TLT | [0,+2] | 171 | -0.00% | -0.03 | 49.7% | +0.06% | -0.06% | +0.52% | 0.58 | -0.01 | +0.43% (0.37) | +0.59% (0.43) | FAIL |
| FOMC | GLD | [-1,0] | 171 | +0.20% | 2.05 | 56.7% | +0.21% | +0.18% | +1.43% | 1.91 | 0.01 | +1.25% (1.14) | +1.61% (1.57) | FAIL |
| FOMC | GLD | [0,+1] | 171 | -0.06% | -0.49 | 47.4% | -0.12% | -0.01% | -0.31% | -0.31 | -0.00 | -0.35% (-0.20) | -0.30% (-0.30) | FAIL |
| FOMC | GLD | [0,+2] | 171 | -0.21% | -1.26 | 48.5% | -0.25% | -0.18% | -1.50% | -1.08 | -0.00 | -1.46% (-0.66) | -1.60% (-0.94) | FAIL |
| FOMC | UUP | [-1,0] | 162 | -0.12% | -2.17 | 43.2% | -0.16% | -0.07% | -1.02% | -2.20 | -0.00 | -1.15% (-1.61) | -0.91% (-1.53) | FAIL |
| FOMC | UUP | [0,+1] | 162 | +0.08% | 1.70 | 53.1% | +0.10% | +0.06% | +0.73% | 1.80 | 0.00 | +1.08% (1.73) | +0.39% (0.74) | FAIL |
| FOMC | UUP | [0,+2] | 162 | +0.18% | 2.99 | 56.2% | +0.15% | +0.21% | +1.44% | 2.60 | -0.00 | +1.38% (1.66) | +1.49% (2.04) | **PASS** |
| CPI | SPY | [-1,0] | 341 | -0.07% | -2.38 | 44.3% | -0.06% | -0.07% | +0.13% | 0.16 | 0.03 | -0.10% (-0.07) | +0.21% (0.19) | FAIL |
| CPI | SPY | [0,+1] | 341 | -0.02% | -0.72 | 52.5% | -0.03% | -0.01% | -0.67% | -0.74 | 0.04 | -1.63% (-1.19) | +0.09% (0.07) | FAIL |
| CPI | SPY | [0,+2] | 341 | +0.01% | 0.19 | 53.4% | -0.01% | +0.02% | +0.92% | 0.76 | 0.07 | +0.25% (0.14) | +1.23% (0.80) | FAIL |
| CPI | QQQ | [-1,0] | 341 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | +1.09% | 1.01 | 0.05 | +0.48% (0.28) | +1.57% (1.22) | FAIL |
| CPI | QQQ | [0,+1] | 341 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | +0.08% | 0.06 | 0.07 | -0.31% (-0.15) | +0.55% (0.39) | FAIL |
| CPI | QQQ | [0,+2] | 341 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | +1.06% | 0.65 | 0.11 | +0.54% (0.20) | +1.62% (0.89) | FAIL |
| CPI | TLT | [-1,0] | 308 | +0.05% | 0.99 | 54.5% | +0.09% | +0.01% | +0.57% | 0.82 | -0.00 | +1.23% (1.32) | -0.14% (-0.14) | FAIL |
| CPI | TLT | [0,+1] | 308 | +0.04% | 0.91 | 50.6% | +0.04% | +0.05% | +1.28% | 2.01 | -0.01 | +1.50% (1.64) | +1.04% (1.17) | **PASS** |
| CPI | TLT | [0,+2] | 308 | +0.11% | 1.56 | 53.6% | +0.20% | +0.01% | +1.63% | 1.82 | -0.02 | +3.01% (2.40) | +0.22% (0.17) | FAIL |
| CPI | GLD | [-1,0] | 280 | +0.13% | 2.04 | 53.9% | +0.05% | +0.21% | +1.54% | 1.83 | 0.00 | +0.61% (0.46) | +2.46% (2.36) | FAIL |
| CPI | GLD | [0,+1] | 280 | +0.03% | 0.42 | 53.6% | +0.07% | -0.00% | +0.58% | 0.55 | 0.00 | +0.94% (0.54) | +0.18% (0.15) | FAIL |
| CPI | GLD | [0,+2] | 280 | +0.05% | 0.54 | 52.9% | +0.13% | -0.02% | +0.69% | 0.50 | 0.00 | +1.75% (0.75) | -0.40% (-0.26) | FAIL |
| CPI | UUP | [-1,0] | 250 | -0.03% | -0.95 | 47.2% | +0.01% | -0.08% | -0.28% | -0.57 | -0.01 | +0.54% (0.72) | -1.10% (-1.75) | FAIL |
| CPI | UUP | [0,+1] | 250 | +0.02% | 0.48 | 51.2% | +0.02% | +0.01% | +0.26% | 0.63 | -0.01 | +0.47% (0.70) | +0.06% (0.12) | FAIL |
| CPI | UUP | [0,+2] | 250 | +0.08% | 1.83 | 56.8% | +0.03% | +0.13% | +1.11% | 1.87 | -0.01 | +0.56% (0.62) | +1.66% (2.15) | FAIL |
| NFP | SPY | [-1,0] | 329 | +0.00% | 0.04 | 53.5% | -0.06% | +0.06% | +0.10% | 0.12 | 0.04 | -0.46% (-0.37) | +0.55% (0.53) | FAIL |
| NFP | SPY | [0,+1] | 329 | -0.06% | -1.73 | 45.3% | -0.08% | -0.03% | -1.39% | -1.64 | 0.03 | -1.48% (-1.20) | -1.56% (-1.35) | FAIL |
| NFP | SPY | [0,+2] | 329 | -0.05% | -1.43 | 46.2% | -0.06% | -0.05% | -1.38% | -1.23 | 0.06 | -1.82% (-1.03) | -1.14% (-0.81) | FAIL |
| NFP | QQQ | [-1,0] | 329 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | -0.37% | -0.30 | 0.06 | -0.38% (-0.18) | -0.30% (-0.22) | FAIL |
| NFP | QQQ | [0,+1] | 329 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | -1.38% | -1.26 | 0.05 | -1.38% (-0.81) | -1.58% (-1.17) | FAIL |
| NFP | QQQ | [0,+2] | 329 | +0.00% | 0.00 | 0.0% | +0.00% | +0.00% | -1.71% | -1.13 | 0.09 | -2.78% (-1.11) | -0.63% (-0.37) | FAIL |
| NFP | TLT | [-1,0] | 298 | -0.09% | -1.48 | 46.3% | -0.08% | -0.10% | -0.93% | -1.16 | -0.01 | -0.59% (-0.52) | -1.29% (-1.16) | FAIL |
| NFP | TLT | [0,+1] | 298 | +0.01% | 0.27 | 53.4% | +0.14% | -0.12% | +0.28% | 0.48 | -0.01 | +1.83% (2.73) | -1.27% (-1.32) | FAIL |
| NFP | TLT | [0,+2] | 298 | +0.02% | 0.32 | 50.7% | +0.10% | -0.06% | +0.52% | 0.61 | -0.02 | +1.39% (1.34) | -0.38% (-0.29) | FAIL |
| NFP | GLD | [-1,0] | 268 | +0.05% | 0.69 | 53.7% | +0.08% | +0.02% | +0.50% | 0.57 | -0.00 | +0.83% (0.62) | +0.16% (0.14) | FAIL |

## Key Findings

1. **Pre-FOMC Equity Drift Decay:** The classic pre-FOMC announcement drift (`FOMC SPY [-1,0]` and `FOMC QQQ [-1,0]`) was strong and statistically positive in the first half of the sample (H1 2006-2015, alpha ~+2.4% to +2.8%, t > 2.5). However, in H2 (2016-2026), the drift decayed and turned negative, causing the full sample t-statistic to fall below the acceptance bar of 2.0.
2. **Post-Announcement Reversal / Choppiness:** Post-event windows (`[0,+1]` and `[0,+2]`) for equities around FOMC, CPI, and NFP exhibit negative alpha with elevated volatility, failing the consistency check across sample halves.
3. **Passing Variants (2 of 40):**
- **FOMC UUP [0,+2]:** Strategy alpha +1.44% (t=2.60), beta=-0.00, H1 alpha=+1.38% (t=1.66), H2 alpha=+1.49% (t=2.04).
- **CPI TLT [0,+1]:** Strategy alpha +1.28% (t=2.01), beta=-0.01, H1 alpha=+1.50% (t=1.64), H2 alpha=+1.04% (t=1.17).
4. **Verdict Assessment:** While isolated non-equity variants pass the nominal statistical thresholds (e.g. post-FOMC dollar drift in UUP or post-CPI Treasuries in TLT), across 40 tested variants with family-wise discovery risk, evidence for an actionable stand-alone equity edge remains narrow.
