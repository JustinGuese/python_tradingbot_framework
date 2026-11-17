# Broad Universe Buyback Sleeve Study (2026-11)

Study evaluating Form 8-K buyback authorization drift (10-session hold, equal weight capped at 0.5 per name) across S&P 100, S&P 400, S&P 600, mid/small caps, and the broad universe. Strictly LONG-ONLY (no shorts, no hedge). Idle cash earns T-bill yield (SHV cash proxy).

- **Window**: 2010-02-01 to latest available (EDGAR 8-K sample).
- **Benchmark**: QQQ.
- **Costs**: 10 bp per side (20 bp round-trip).
- **Total deduped events**: 1213 (SP100: 396, SP400: 338, SP600: 479).

## Results: Baseline (Cash 0% — Paper Metric)

| Universe | Events | Avg Cash | Alpha/yr | t | Beta | Corr | H1 Alpha (t) | H2 Alpha (t) |
|---|---|---|---|---|---|---|---|---|
| buyback_sp100 | 396 | 62.5% | +4.32% | 1.37 | 0.29 | 0.42 | +0.91% (0.29) | +8.14% (1.49) |
| buyback_sp400 | 338 | 65.3% | -5.22% | -1.47 | 0.32 | 0.42 | -1.21% (-0.32) | -8.89% (-1.48) |
| buyback_sp600 | 479 | 54.9% | +5.38% | 1.21 | 0.42 | 0.43 | +3.48% (0.77) | +8.13% (1.06) |
| buyback_midsmall | 817 | 35.9% | +1.57% | 0.33 | 0.56 | 0.51 | +2.47% (0.48) | +0.91% (0.11) |
| buyback_broad | 1213 | 21.1% | +5.12% | 1.14 | 0.62 | 0.57 | +3.43% (0.69) | +6.49% (0.87) |

## Results: Leveraged Cash (Idle Cash in SHV / T-Bill Yield — Copier Metric)

| Universe | Events | Avg Cash | Alpha/yr | t | Beta | Corr | H1 Alpha (t) | H2 Alpha (t) | Bar Passed? |
|---|---|---|---|---|---|---|---|---|---|
| buyback_sp100 | 396 | 62.5% | +5.20% | 1.65 | 0.29 | 0.42 | +1.06% (0.34) | +9.76% (1.79) | FAIL |
| buyback_sp400 | 338 | 65.3% | -4.36% | -1.23 | 0.32 | 0.42 | -1.03% (-0.27) | -7.35% (-1.23) | FAIL |
| buyback_sp600 | 479 | 54.9% | +6.05% | 1.36 | 0.42 | 0.43 | +3.68% (0.81) | +9.29% (1.22) | FAIL |
| buyback_midsmall | 817 | 35.9% | +1.96% | 0.41 | 0.56 | 0.51 | +2.60% (0.50) | +1.56% (0.20) | FAIL |
| buyback_broad | 1213 | 21.1% | +5.35% | 1.19 | 0.62 | 0.57 | +3.49% (0.70) | +6.89% (0.92) | FAIL |

## Analysis & Findings

1. **S&P 100 Lead Holds Up**: `buyback_sp100` achieves +5.20%/yr alpha vs QQQ at t 1.65 with beta 0.29 when idle cash is parked in SHV. Both halves are positive (+1.06% in H1, +9.76% in H2), and beta is comfortably below 0.5.
2. **Mid-Caps Drag Severely**: `buyback_sp400` exhibits negative alpha in both halves (-1.03% in H1, -7.35% in H2), netting -4.36%/yr at t -1.23. Buybacks in mid-cap companies fail to produce positive abnormal drift.
3. **Beta Inflates in Broader Universes**: `buyback_midsmall` inflates beta to 0.56, while `buyback_broad` reaches beta 0.62. Both violate the project beta threshold (beta < 0.5) and dilute t-stats (0.41 and 1.19 respectively).
4. **Decision per PLAN.md S3a**: The broader universe does NOT meet the promotion bar (beta >= 0.5, t < 2, mid-cap drag). Therefore, `BuybackBroadBot` is NOT promoted to live or paper execution (`no edge` / `unproven`). `BuybackDriftBot` (S&P 100 only, paper) remains the sole active buyback strategy.
