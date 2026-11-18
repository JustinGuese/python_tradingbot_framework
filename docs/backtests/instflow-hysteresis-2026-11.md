# InstitutionalFlowBot Turnover Hysteresis Study (2026-11)

Evaluates whether adding rank hysteresis (`hold_rank`) to InstitutionalFlowBot reduces churn and trading costs without destroying momentum alpha.

## Problem and Mechanism

- **The churn problem:** In weekly cross-sectional ranking with `top_n=20`, names oscillating between ranks 18 and 22 are repeatedly bought and sold through slippage and commissions, despite their accumulation scores being statistically indistinguishable.
- **Turnover hysteresis:** A holding stays as long as it still passes the mandate filter and ranks within `hold_rank`; open slots are filled from the top.
- **Stateless implementation:** Computed statelessly by replaying weekly ranking over trailing 26 Fridays from `self.datas`, ensuring live CronJob execution and backtests produce identical target weights without instance state.

## Study Setup

- **Universe:** S&P 100 (99 traded names) + QQQ benchmark.
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
| instflow_hold_none | None | H1 | +7.4% (1.25) | 0.30 | 0.56 | -22.0% | +6.0% (1.01) | 4.21 |
| instflow_hold_none | None | H2 | +10.0% (1.56) | 0.48 | 0.61 | -15.0% | +6.8% (1.02) | 5.48 |
| instflow_hold_none | None | FULL | +9.7% (2.20) | 0.36 | 0.58 | -22.0% | +8.4% (1.86) | 4.82 |
| instflow_hold_30 | 30 | H1 | +5.8% (0.97) | 0.31 | 0.57 | -21.7% | +4.4% (0.74) | 2.79 |
| instflow_hold_30 | 30 | H2 | +9.2% (1.44) | 0.50 | 0.62 | -15.2% | +6.2% (0.92) | 3.45 |
| instflow_hold_30 | 30 | FULL | +8.6% (1.93) | 0.37 | 0.58 | -21.7% | +7.4% (1.61) | 3.10 |
| instflow_hold_40 | 40 | H1 | +6.2% (1.04) | 0.31 | 0.58 | -22.0% | +4.8% (0.81) | 2.12 |
| instflow_hold_40 | 40 | H2 | +8.3% (1.27) | 0.51 | 0.63 | -15.7% | +5.6% (0.81) | 2.58 |
| instflow_hold_40 | 40 | FULL | +8.4% (1.87) | 0.38 | 0.59 | -22.0% | +7.3% (1.57) | 2.34 |

### Cash and Cost Variations (FULL Window)

| Variant | hold_rank | CAGR (0% cash) | CAGR (T-bill) | CAGR (20 bp cost) | H1 Alpha (t) | H2 Alpha (t) | H2 Fills/Wk |
|---|---|---|---|---|---|---|---|
| instflow_hold_none | None | +18.3% | +19.6% | +16.2% | +7.4% (1.25) | +10.0% (1.56) | 5.48 |
| instflow_hold_30 | 30 | +17.1% | +18.2% | +15.8% | +5.8% (0.97) | +9.2% (1.44) | 3.45 |
| instflow_hold_40 | 40 | +17.1% | +18.0% | +16.1% | +6.2% (1.04) | +8.3% (1.27) | 2.58 |

## Verdict and Decision

- **Bar**: New parameter ships only if winner beats default on H2 alpha t AND on 20 bp CAGR.

**Outcome**: `Default stays None`.
Neither `hold_rank=30` nor `hold_rank=40` cleared the hurdle of beating the default on both H2 alpha t and 20 bp CAGR (default H2 t=1.56, default 20bp CAGR=16.19%). InstitutionalFlowBot ships with default `hold_rank=None`.

