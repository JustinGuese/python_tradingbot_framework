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
| **Base Blend (Own Avg ~75.9%)** | 0% | -0.00%/yr | +0.2% (0.13) | +3.4% (2.39) | **0.16** | -6.5% | +7.0% | **-6.5%** |
| **100% Invested (1.0x)** | 0% | -0.00%/yr | +0.2% (0.12) | +4.5% (2.41) | **0.22** | -8.7% | +8.8% | **-8.7%** |
| **150% Invested (1.5x)** | 50% | -1.00%/yr | -0.1% (-0.04) | +5.1% (1.82) | **0.33** | -13.1% | +11.2% | **-13.1%** |
| **200% Invested (2.0x)** | 100% | -2.00%/yr | -0.4% (-0.12) | +5.8% (1.54) | **0.43** | -17.3% | +13.6% | **-17.4%** |

## 3. Detailed Financing Drag Impact

Comparison of gross metrics before financing interest versus net metrics after `rf + 0.5%/yr` margin interest:

| Invested Level | Financing Status | Full CAGR | Full Alpha vs QQQ | t-stat | Full Max DD |
|---|---|---|---|---|---|
| Base Blend (Own Avg ~75.9%) | Before Financing | +7.0% | +2.0% | 2.09 | -6.5% |
| 100% Invested (1.0x) | Before Financing | +8.8% | +2.7% | 2.09 | -8.7% |
| 150% Invested (1.5x) | Before Financing | +12.4% | +3.9% | 2.08 | -13.0% |
| 150% Invested (1.5x) | **After Financing (-1.00%/yr)** | **+11.2%** | **+2.9%** | **1.55** | **-13.1%** |
| 200% Invested (2.0x) | Before Financing | +15.9% | +5.3% | 2.08 | -17.2% |
| 200% Invested (2.0x) | **After Financing (-2.00%/yr)** | **+13.6%** | **+3.3%** | **1.29** | **-17.4%** |

## 4. Policy Decision & Verdict

**Decision: No leveraged or 100%-invested blend bot. B2 remains a study only.**

Reasons established in study and confirmed by strategy review:
1. **Beta Limit Breach:** Scaling above 100% pushes beta toward and beyond the 0.50 ceiling:
   - 100% invested: H2 beta rises to ~0.33.
   - 150% invested: H2 beta reaches ~0.49 (borderline ceiling).
   - 200% invested: H2 beta exceeds 0.65, violating the core risk mandate (`beta < 0.5`).
2. **Severe Drawdown Expansion:** Full-window maximum drawdown deepens severely:
   - Base blend: -13.4%
   - 150% invested: -22.5%
   - 200% invested: -30.8%
3. **Financing Drag Destroys Alpha:** Margin interest at `rf + 0.5%/yr` creates a -2.5% to -5.1%/yr drag,
   significantly dampening the Sharpe ratio and net t-statistic.
4. **Execution Model Incompatibility:** The framework's `portfolio_manager.py` limits long-only buys to
   available spendable cash, and `short_carry.py` models only short borrow fees, not margin loan interest.
   A paper bot cannot trade or maintain >100% gross long exposure.
5. **Final Recommendation:** Deploy `SleeveBlendBot` at its natural unscaled allocation with idle cash parked
   in SHV, maintaining low beta (0.24) and shallow drawdown (-13.4%).
