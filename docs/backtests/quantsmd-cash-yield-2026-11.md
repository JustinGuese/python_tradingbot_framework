# quants.md cash-yield lever study (2026-11)

Measurement of the cash-yield lever across the six surviving quants.md strategy books.
Paper bots hold 0%-yield USD, whereas a live copier or broker parks uninvested balance
in cash equivalents (e.g. SHV or 13-week Treasury bills). A paper bot's measured alpha
understates what the copied book earns by roughly `rf x cash_share` (CLAUDE.md).

## Methodology

- Daily equity curves simulated in plain pandas directly calling each bot's pure `compute_weights()` function.
- Yahoo Finance adjusted closes (`auto_adjust=True`, start `2000-01-01`). Common history window per universe (2006 for ETF8, 2000 for sector ETFs).
- Default bot parameters used throughout (untuned). `None` output maintains previous weights.
- Weights decided at close of day t earn return on day t+1.
- Turnover friction of 5 bp (0.0005) charged per unit of rebalance turnover (`sum(|w_t - w_{t-1}|)`).
- Baseline: cash earns 0.0%.
- With yield: cash share (`1.0 - sum(weights)`) earns the 13-week T-bill yield (`^IRX / 100 / 252` per day).
- Benchmark: QQQ. Alpha, t-stat, beta, corr computed per CLAUDE.md / `tradingbot.utils.alpha_report.alpha_stats`.
- H1 / H2: two halves of the common return curve.

## Results: Baseline (cash 0%) vs With Cash Yield

| Bot | Start | Avg cash | Alpha (0%) | t (0%) | Alpha (yield) | t (yield) | Beta | Corr | H1 alpha (t) [0% / yield] | H2 alpha (t) [0% / yield] | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MonthlyRotation | 2006 | 2.8% | +4.9% | 1.85 | +5.0% | 1.90 | 0.26 | 0.42 | +8.2% (1.97) / +8.5% (2.03) | +1.1% (0.35) / +1.1% (0.35) | unproven |
| VolTargetMomentum | 2006 | 11.0% | +4.7% | 1.88 | +4.9% | 2.00 | 0.22 | 0.39 | +6.6% (1.76) / +6.9% (1.84) | +2.3% (0.72) / +2.5% (0.80) | unproven |
| QuarterlyTeCostGate | 2006 | 2.4% | +6.6% | 2.56 | +6.8% | 2.60 | 0.23 | 0.40 | +8.4% (2.15) / +8.6% (2.21) | +4.5% (1.33) / +4.5% (1.33) | unproven |
| RiskParityCapped | 2000 | 1.9% | +4.8% | 2.15 | +4.9% | 2.19 | 0.45 | 0.72 | +5.9% (1.65) / +6.1% (1.71) | +1.5% (0.59) / +1.5% (0.59) | unproven |
| SmaDaily | 2000 | 13.7% | +4.7% | 2.20 | +5.0% | 2.33 | 0.31 | 0.60 | +5.2% (1.69) / +5.7% (1.85) | -0.0% (-0.01) / +0.1% (0.03) | unproven |
| SmaDailyGated | 2000 | 29.4% | +5.4% | 3.03 | +6.1% | 3.40 | 0.16 | 0.42 | +5.0% (2.00) / +6.0% (2.40) | +3.6% (1.50) / +3.9% (1.62) | unproven |

## Detailed Breakdown

### Baseline: Cash earns 0%

| Bot | Start | CAGR | Max DD | Alpha/yr | t | Beta | Corr | H1 alpha (t) | H2 alpha (t) |
|---|---|---|---|---|---|---|---|---|---|
| MonthlyRotation | 2006 | 8.6% | -29.8% | +4.9% | 1.85 | 0.26 | 0.42 | +8.2% (1.97) | +1.1% (0.35) |
| VolTargetMomentum | 2006 | 7.8% | -30.3% | +4.7% | 1.88 | 0.22 | 0.39 | +6.6% (1.76) | +2.3% (0.72) |
| QuarterlyTeCostGate | 2006 | 10.2% | -21.4% | +6.6% | 2.56 | 0.23 | 0.40 | +8.4% (2.15) | +4.5% (1.33) |
| RiskParityCapped | 2000 | 8.9% | -49.2% | +4.8% | 2.15 | 0.45 | 0.72 | +5.9% (1.65) | +1.5% (0.59) |
| SmaDaily | 2000 | 7.6% | -35.8% | +4.7% | 2.20 | 0.31 | 0.60 | +5.2% (1.69) | -0.0% (-0.01) |
| SmaDailyGated | 2000 | 6.9% | -19.7% | +5.4% | 3.03 | 0.16 | 0.42 | +5.0% (2.00) | +3.6% (1.50) |

### With Cash Yield: Cash earns 13-week T-bill (^IRX / 252)

| Bot | Start | CAGR | Max DD | Alpha/yr | t | Beta | Corr | H1 alpha (t) | H2 alpha (t) |
|---|---|---|---|---|---|---|---|---|---|
| MonthlyRotation | 2006 | 8.8% | -29.8% | +5.0% | 1.90 | 0.26 | 0.42 | +8.5% (2.03) | +1.1% (0.35) |
| VolTargetMomentum | 2006 | 8.1% | -30.2% | +4.9% | 2.00 | 0.22 | 0.39 | +6.9% (1.84) | +2.5% (0.80) |
| QuarterlyTeCostGate | 2006 | 10.3% | -21.4% | +6.8% | 2.60 | 0.23 | 0.40 | +8.6% (2.21) | +4.5% (1.33) |
| RiskParityCapped | 2000 | 9.0% | -49.2% | +4.9% | 2.19 | 0.45 | 0.72 | +6.1% (1.71) | +1.5% (0.59) |
| SmaDaily | 2000 | 7.9% | -35.2% | +5.0% | 2.33 | 0.31 | 0.60 | +5.7% (1.85) | +0.1% (0.03) |
| SmaDailyGated | 2000 | 7.6% | -19.7% | +6.1% | 3.40 | 0.16 | 0.42 | +6.0% (2.40) | +3.9% (1.62) |

## Interpretation

- **All six bots remain `unproven` by the project bar (t >= 2 in both halves).** None of the books clears t >= 2 across both halves of its sample, even with cash yield credited.
- **Impact correlates strictly with cash share:**
  - For rotation and parity books that stay almost fully invested (`MonthlyRotation` ~2.8% cash, `RiskParityCapped` ~1.9% cash, `QuarterlyTeCostGate` ~2.4% cash), cash yield adds only +0.1% to +0.2% annualized alpha and negligible t-stat gain (+0.04 to +0.05).
  - For trend and gating books with significant cash allocations (`SmaDaily` ~13.7% cash, `SmaDailyGated` ~29.4% cash), cash yield provides a noticeable lift: `SmaDailyGated` gains +0.7%/yr alpha (from +5.4% to +6.1%) and lifts full-window t from 3.03 to 3.40 (H1 t increases from 2.00 to 2.40).
- **Second-half degradation persists:** Across all six books, performance in H2 remains substantially weaker than in H1 (e.g. `SmaDailyGated` H2 t is 1.62 vs H1 t 2.40; `QuarterlyTeCostGate` H2 t is 1.33 vs H1 t 2.21). Cash yield lifts the overall curve level but does not fix the post-2013 alpha decay.
