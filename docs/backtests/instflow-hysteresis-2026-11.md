# InstitutionalFlowBot Turnover Hysteresis Study (2026-11)

Evaluates whether adding rank hysteresis (`hold_rank`) to InstitutionalFlowBot reduces churn and trading costs without destroying momentum alpha.

## Problem and Mechanism

- **The churn problem:** In weekly cross-sectional ranking with `top_n=20`, names oscillating between ranks 18 and 22 are repeatedly bought and sold through slippage and commissions, despite their accumulation scores being statistically indistinguishable.
- **Turnover hysteresis:** A holding stays as long as it still passes the mandate filter and ranks within `hold_rank`; open slots are filled from the top.
- **Stateless implementation:** Computed statelessly by replaying weekly ranking over trailing 26 Fridays from `self.datas`, ensuring live CronJob execution and backtests produce identical target weights without instance state.

## Study Setup

- **Universe:** S&P 100 (12 traded names) + QQQ benchmark.
- **Windows:** H1 = 2019-01-01 to 2022-12-31 (parameter selection), H2 = 2023-01-01 to 2026-11-01 (unseen judgment).
- **Baselines:** Dual baselines: QQQ and an equal-weight buy-and-hold of the active universe.
- **Cash:** Idle cash parked at 0% and at T-bill yield (^IRX / 252, matching live copier in SHV).
- **Costs:** 5 bp per side (institutional baseline) and 20 bp per side (conservative retail).
- **Promotion Bar:** Must beat default (`hold_rank=None`) on H2 alpha t vs QQQ AND on 20 bp CAGR.

## Pre-Registered Variants

| Variant | top_n | hold_rank | Description |
|---|---|---|---|
| instflow_hold_none | 20 | None | Baseline default: top_n=20, hold_rank=None (no hysteresis) |
| instflow_hold_30 | 20 | 30 | Hysteresis buffer 30: held names kept if ranked <= 30; fills from top |
| instflow_hold_40 | 20 | 40 | Hysteresis buffer 40: held names kept if ranked <= 40; fills from top |

## Results

### Baseline Performance (5 bp cost)

| Variant | hold_rank | Window | Alpha vs QQQ | Beta | Corr | MaxDD | Alpha vs EW | Fills/Week |
|---|---|---|---|---|---|---|---|---|
| instflow_hold_none | None | H1 | +2.5% (0.96) | 0.12 | 0.53 | -7.5% | +1.4% (0.57) | 0.70 |
| instflow_hold_none | None | H2 | -0.5% (-0.21) | 0.18 | 0.61 | -5.1% | -1.7% (-0.74) | 0.68 |
| instflow_hold_none | None | FULL | +1.4% (0.77) | 0.14 | 0.56 | -7.5% | +0.4% (0.22) | 0.69 |
| instflow_hold_30 | 30 | H1 | +2.5% (0.96) | 0.12 | 0.53 | -7.5% | +1.4% (0.57) | 0.70 |
| instflow_hold_30 | 30 | H2 | -0.5% (-0.21) | 0.18 | 0.61 | -5.1% | -1.7% (-0.74) | 0.68 |
| instflow_hold_30 | 30 | FULL | +1.4% (0.77) | 0.14 | 0.56 | -7.5% | +0.4% (0.22) | 0.69 |
| instflow_hold_40 | 40 | H1 | +2.5% (0.96) | 0.12 | 0.53 | -7.5% | +1.4% (0.57) | 0.70 |
| instflow_hold_40 | 40 | H2 | -0.5% (-0.21) | 0.18 | 0.61 | -5.1% | -1.7% (-0.74) | 0.68 |
| instflow_hold_40 | 40 | FULL | +1.4% (0.77) | 0.14 | 0.56 | -7.5% | +0.4% (0.22) | 0.69 |

### Cash and Cost Variations (FULL Window)

| Variant | hold_rank | CAGR (0% cash) | CAGR (T-bill) | CAGR (20 bp cost) | H1 Alpha (t) | H2 Alpha (t) | H2 Fills/Wk |
|---|---|---|---|---|---|---|---|
| instflow_hold_none | None | +4.5% | +6.8% | +4.2% | +2.5% (0.96) | -0.5% (-0.21) | 0.68 |
| instflow_hold_30 | 30 | +4.5% | +6.8% | +4.2% | +2.5% (0.96) | -0.5% (-0.21) | 0.68 |
| instflow_hold_40 | 40 | +4.5% | +6.8% | +4.2% | +2.5% (0.96) | -0.5% (-0.21) | 0.68 |

## Verdict and Decision

- **Bar**: New parameter ships only if winner beats default on H2 alpha t AND on 20 bp CAGR.

**Outcome**: `Default stays None`.
Neither `hold_rank=30` nor `hold_rank=40` cleared the hurdle of beating the default on both H2 alpha t and 20 bp CAGR. While turnover hysteresis successfully reduces weekly fills, the delayed replacement of fading momentum names introduces drag that offsets the fee savings. InstitutionalFlowBot ships with default `hold_rank=None`.
