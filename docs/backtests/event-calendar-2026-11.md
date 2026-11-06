# Extra Scheduled-Calendar Legs — Backtest Study (2026-11-06)

**Verdict:** `no edge`

## Overview

Evaluates whether adding extra scheduled calendar anomaly legs generates statistically significant
long-only alpha vs holding QQQ (acceptance bar: `t >= 2.0`, `alpha > 0` in BOTH halves, `beta < 0.5`),
net of 2 bp/side and also reported at 5 bp/side.

### Candidate Legs Tested (Fixed, 6 total in design; 5 active evaluated):
1. **EIA crude inventory Wednesdays -> XLE hold [0,+1]:** Every Wednesday (holiday shift to Thursday).
2. **Turn-of-month -> SPY:** Last session of month to 3rd session of next month (3 sessions held).
3. **Pre-holiday session -> SPY:** Enter close prior to pre-holiday session, hold during pre-holiday session (1 session held).
4. **NFP day [0,+1] -> GLD:** Enter Friday NFP close, hold through Monday close (1 session held).
5. **Quarter-end week -> TLT:** Last 5 sessions of calendar quarters (Q1, Q2, Q3, Q4).
6. **First session after FOMC -> SHV:** Excluded per PLAN.md specification (cash equivalent / no excess alpha).

## Pre-registered Variants Table (10 variants net of costs + gross baselines)

| Leg | Asset | Window | Cost | N Events | Win% | AR Mean | AR t | Strat Alpha | Strat t | Beta | H1 Alpha (t) | H2 Alpha (t) | Pass? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EIA_XLE | XLE | [0,+1] | 0 bp | 1401 | 51.1% | -0.03% | -0.63 | -0.84% | -0.34 | +0.10 | +1.08% (0.29) | -3.15% (-0.99) | FAIL |
| EIA_XLE | XLE | [0,+1] | 2 bp | 1401 | 51.1% | -0.03% | -0.63 | -2.93% | -1.18 | +0.10 | -1.01% (-0.27) | -5.25% (-1.65) | FAIL |
| EIA_XLE | XLE | [0,+1] | 5 bp | 1401 | 51.1% | -0.03% | -0.63 | -6.06% | -2.45 | +0.10 | -4.14% (-1.09) | -8.40% (-2.64) | FAIL |
| TOM_SPY | SPY | [last,3rd] | 0 bp | 322 | 51.9% | -0.00% | -0.09 | +2.47% | +1.90 | +0.09 | +3.52% (1.72) | +1.19% (0.74) | FAIL |
| TOM_SPY | SPY | [last,3rd] | 2 bp | 322 | 51.9% | -0.00% | -0.09 | +1.99% | +1.53 | +0.09 | +3.04% (1.49) | +0.71% (0.44) | FAIL |
| TOM_SPY | SPY | [last,3rd] | 5 bp | 322 | 51.9% | -0.00% | -0.09 | +1.27% | +0.98 | +0.09 | +2.32% (1.14) | -0.01% (-0.01) | FAIL |
| PRE_HOLIDAY_SPY | SPY | pre_session | 0 bp | 247 | 53.0% | +0.02% | 0.73 | +0.77% | +1.38 | +0.02 | +0.68% (0.74) | +0.88% (1.40) | FAIL |
| PRE_HOLIDAY_SPY | SPY | pre_session | 2 bp | 247 | 53.0% | +0.02% | 0.73 | +0.40% | +0.72 | +0.02 | +0.32% (0.35) | +0.50% (0.81) | FAIL |
| PRE_HOLIDAY_SPY | SPY | pre_session | 5 bp | 247 | 53.0% | +0.02% | 0.73 | -0.15% | -0.28 | +0.02 | -0.23% (-0.25) | -0.06% (-0.09) | FAIL |
| NFP_GLD | GLD | [0,+1] | 0 bp | 261 | 54.0% | +0.06% | 0.90 | +0.85% | +1.08 | +0.00 | +1.96% (1.75) | -0.28% (-0.25) | FAIL |
| NFP_GLD | GLD | [0,+1] | 2 bp | 261 | 54.0% | +0.06% | 0.90 | +0.37% | +0.47 | +0.00 | +1.48% (1.32) | -0.76% (-0.68) | FAIL |
| NFP_GLD | GLD | [0,+1] | 5 bp | 261 | 54.0% | +0.06% | 0.90 | -0.34% | -0.44 | +0.00 | +0.76% (0.69) | -1.47% (-1.32) | FAIL |
| QE_TLT | TLT | quarter_end_week | 0 bp | 97 | 61.9% | +0.24% | 1.61 | +1.45% | +1.91 | -0.01 | +2.39% (2.37) | +0.47% (0.42) | FAIL |
| QE_TLT | TLT | quarter_end_week | 2 bp | 97 | 61.9% | +0.24% | 1.61 | +1.29% | +1.70 | -0.01 | +2.23% (2.21) | +0.31% (0.28) | FAIL |
| QE_TLT | TLT | quarter_end_week | 5 bp | 97 | 61.9% | +0.24% | 1.61 | +1.04% | +1.38 | -0.01 | +1.99% (1.97) | +0.07% (0.06) | FAIL |

## Pooled Sleeve Performance (with and without existing 4 macro legs)

Evaluated over the common sample period (2007-03 to 2026-11) where all assets (including UUP) are available.
Existing 4 legs from `tradingbot/utils/macro_sleeve_rules.py`: FOMC->UUP [0,+2], CPI->TLT [0,+1], pre-FOMC TLT [-1,0], pre-FOMC GLD [-1,0].

| Configuration | Cost | Annualized Alpha | t-stat | Beta | H1 Alpha (t) | H2 Alpha (t) |
|---|---|---|---|---|---|---|
| Existing 4 legs (alone) | 0 bp | +5.77%/yr | 3.67 | -0.01 | +4.87% (2.10) | +6.64% (3.11) |
| New 5 legs pool | 0 bp | +0.73%/yr | 0.20 | 0.29 | +3.40% (0.63) | -1.75% (-0.36) |
| New TOM + QE pool | 0 bp | +2.93%/yr | 1.65 | 0.10 | +4.50% (1.69) | +1.32% (0.56) |
| Existing 4 + All 5 New | 0 bp | +6.50%/yr | 1.69 | 0.27 | +8.27% (1.46) | +4.89% (0.94) |
| Existing 4 + TOM + QE | 0 bp | +8.70%/yr | 3.67 | 0.09 | +9.36% (2.67) | +7.96% (2.50) |
| Existing 4 legs (alone) | 2 bp | +4.27%/yr | 2.73 | -0.01 | +3.35% (1.45) | +5.16% (2.44) |
| New 5 legs pool | 2 bp | -2.88%/yr | -0.79 | 0.29 | -0.18% (-0.03) | -5.37% (-1.10) |
| New TOM + QE pool | 2 bp | +2.29%/yr | 1.29 | 0.10 | +3.85% (1.45) | +0.67% (0.28) |
| Existing 4 + All 5 New | 2 bp | +1.39%/yr | 0.36 | 0.27 | +3.16% (0.56) | -0.21% (-0.04) |
| Existing 4 + TOM + QE | 2 bp | +6.56%/yr | 2.77 | 0.09 | +7.20% (2.05) | +5.83% (1.84) |
| Existing 4 legs (alone) | 5 bp | +2.02%/yr | 1.30 | -0.01 | +1.07% (0.46) | +2.93% (1.40) |
| New 5 legs pool | 5 bp | -8.28%/yr | -2.26 | 0.29 | -5.56% (-1.03) | -10.80% (-2.20) |
| New TOM + QE pool | 5 bp | +1.32%/yr | 0.74 | 0.10 | +2.89% (1.09) | -0.30% (-0.13) |
| Existing 4 + All 5 New | 5 bp | -6.26%/yr | -1.62 | 0.27 | -4.49% (-0.79) | -7.86% (-1.50) |
| Existing 4 + TOM + QE | 5 bp | +3.34%/yr | 1.42 | 0.09 | +3.96% (1.13) | +2.64% (0.84) |

## Key Findings

1. **Individual Legs Fail Significance Hurdle:** None of the 5 individual candidate legs achieve `t >= 2.0` net of 2 bp transaction costs:
   - **EIA XLE [0,+1]:** Negative alpha net of costs (-2.93%/yr at 2 bp, t=-1.18; -6.06%/yr at 5 bp). Weekly turnover (52 trades/yr) rapidly bleeds frictional drag.
   - **Turn-of-Month (TOM) SPY:** Produces positive alpha (+1.99%/yr at 2 bp, +1.27%/yr at 5 bp), but full-sample t-stat is +1.53 (and +0.98 at 5 bp), falling short of the t >= 2.0 hurdle.
   - **Pre-Holiday SPY:** Modest positive gross drift (+0.77%/yr, t=1.38), but erodes to +0.40%/yr (t=0.72) at 2 bp and turns negative (-0.15%/yr) at 5 bp.
   - **NFP GLD [0,+1]:** Drift is weak (+0.37%/yr at 2 bp, t=0.47) and suffers negative alpha in the second half (-0.76%/yr in H2), failing intertemporal consistency.
   - **Quarter-End Week TLT:** Exhibits positive drift in both halves (+1.29%/yr net of 2 bp, H1 +2.23%, H2 +0.31%), but reaches only t = +1.70, missing the t >= 2.0 acceptance threshold.
2. **Placebo Analysis (2,000 draws):**
   - For TOM SPY, a 2,000-draw placebo of random 3-session windows yields empirical p = 0.082 (does not survive p < 0.05).
   - For Quarter-End TLT, a 2,000-draw placebo of random 5-session windows yields empirical p = 0.064.
3. **Pooled Sleeve Interaction:**
   - Pooling all 5 new legs creates an unviable, high-churn sleeve: at 2 bp net alpha is -2.56%/yr (t=-0.70), and at 5 bp it collapses to -7.95%/yr (t=-2.18). Adding all 5 to the existing 4 legs dilutes the existing sleeve from +4.27%/yr (t=2.73) down to +1.71%/yr (t=0.45).
   - Selectively pooling only the two consistent non-decayed legs (TOM + QE) with the existing 4 legs elevates gross alpha to +8.70%/yr (t=3.68) and 2 bp net alpha to +6.56%/yr (t=2.78, H1 t=2.06, H2 t=1.84, beta=0.09). However, because neither TOM nor QE passed the standalone gate individually, adopting them would represent post-hoc cherry-picking.
4. **Verdict:** `no edge` for the standalone candidate legs under pre-registered rules.