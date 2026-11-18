# Sleeve Blend Leverage and Volatility Scaling Study (2026-11)

Evaluation of PLAN-alphaideas.md Step B2: evaluating the risk, return, beta, and financing drag of raising
the five-sleeve blend's invested equity share across {Base (~75.9%), 100%, 150%, 200%}.

## 1. Study Specification & Rules

- **Pre-registered Variants (4):** Invested equity share in {75.9% (Base), 100%, 150%, 200%}.
- **Borrow Financing Model:** Up to 100% requires no borrowing. Above 100%, borrowed equity incurs
  financing cost at `^IRX + 0.5%/yr` spread. Evaluated both before and after financing drag.
- **Framework Constraints Verified:**
  - `tradingbot/utils/short_carry.py` accrues short borrow fees and dividends in lieu only (no margin borrowing
    interest on cash, no debit interest).
  - `tradingbot/utils/portfolio_manager.py` enforces `spendable_cash` capping buys at equity; long-only paper
    bots cannot hold >100% gross exposure.
- **Global Bar (CLAUDE.md):** alpha vs QQQ, t >= 2, positive in both halves, beta < 0.50.

## 2. Scaling Results (Net of Financing Drag)

| Invested Level | Borrowed | Financing Drag | H1 Alpha (t) | H2 Alpha (t) | H2 Beta | H2 Max DD | Full CAGR (SHV) | Full Max DD |
|---|---|---|---|---|---|---|---|---|
| **Base Blend (Own Avg ~75.9%)** | 0% | -0.00%/yr | -1.4% (-1.17) | +1.6% (1.15) | **0.17** | -9.5% | +5.3% | **-9.8%** |
| **100% Invested (1.0x)** | 0% | -0.00%/yr | -1.9% (-1.17) | +2.2% (1.16) | **0.22** | -12.5% | +6.4% | **-12.9%** |
| **150% Invested (1.5x)** | 50% | -1.00%/yr | -3.3% (-1.34) | +1.7% (0.60) | **0.33** | -18.4% | +7.7% | **-21.2%** |
| **200% Invested (2.0x)** | 100% | -2.00%/yr | -4.6% (-1.42) | +1.2% (0.31) | **0.44** | -23.9% | +8.8% | **-29.0%** |

## 3. Detailed Financing Drag Impact

Comparison of gross metrics before financing interest versus net metrics after `rf + 0.5%/yr` margin interest:

| Invested Level | Financing Status | Full CAGR | Full Alpha vs QQQ | t-stat | Full Max DD |
|---|---|---|---|---|---|
| Base Blend (Own Avg ~75.9%) | Before Financing | +5.3% | +0.3% | 0.33 | -9.8% |
| 100% Invested (1.0x) | Before Financing | +6.4% | +0.4% | 0.33 | -12.9% |
| 150% Invested (1.5x) | Before Financing | +8.8% | +0.6% | 0.33 | -19.2% |
| 150% Invested (1.5x) | **After Financing (-1.00%/yr)** | **+7.7%** | **-0.4%** | **-0.20** | **-21.2%** |
| 200% Invested (2.0x) | Before Financing | +11.0% | +0.8% | 0.33 | -25.2% |
| 200% Invested (2.0x) | **After Financing (-2.00%/yr)** | **+8.8%** | **-1.2%** | **-0.46** | **-29.0%** |

## 4. Policy Decision & Verdict

**Decision: No leveraged variant passed the promotion bar (beta < 0.5, t >= 2.0, positive alpha in both halves). B2 remains a study only.**

Observed metrics by target allocation:
- **Base Blend (Own Avg ~75.9%)**: H2 Beta = 0.17, H2 t = 1.15, Full Max DD = -9.8%, Financing Drag = 0.00%/yr
- **100% Invested (1.0x)**: H2 Beta = 0.22, H2 t = 1.16, Full Max DD = -12.9%, Financing Drag = 0.00%/yr
- **150% Invested (1.5x)**: H2 Beta = 0.33, H2 t = 0.60, Full Max DD = -21.2%, Financing Drag = 1.00%/yr
- **200% Invested (2.0x)**: H2 Beta = 0.44, H2 t = 0.31, Full Max DD = -29.0%, Financing Drag = 2.00%/yr
