# Trading Bot Framework — Action Items & TODOs

As of **October 2026**, this document tracks open technical debt, broker integration blockers, strategy evaluations, and upcoming maintenance tasks.

---

## 1. Broker Live Execution & API Blockers

- [ ] **Collective2 Trade Leader Subscription**
  - **Issue:** Live copier cronjob (`livetrade-collective2`) returns HTTP 400 Error `1020` ("A paid Trade Leader plan is required") when attempting to submit orders.
  - **Impact:** Live execution is frozen; account equity sits idle at ~$60k without placing real or paper trades on C2.
  - **Action Required:** Decide whether to purchase a paid C2 Trade Leader plan or migrate the C2 blend execution to another broker (e.g., unblocking IBKR or enabling eToro).
- [ ] **Interactive Brokers (`livetrade-ib`) Provisioning**
  - **Issue:** Dead since August 2026 (`ssodh/init 500 blocker`).
  - **Action Required:** Verify whether paper account `DU6347896` is provisioned for brokerage sessions/API trading, or re-authenticate credentials.
- [ ] **Darwinex DXtrade Limitation Notice**
  - **Note:** Darwinex DXtrade supports FX, indices, and commodities only (no equities or ETFs). Equity and ETF bots (such as GDX or the C2 blend sleeves) cannot be traded via DXtrade (MT5 required if trading equities there).

---

## 2. Strategy Fleet & Alpha Tracking

- [ ] **C2 Inverse-Volatility Blend Monitoring**
  - **Details:** Blend consists of `TARegimeMultiAssetBot` (0.39), `GoldenButterflyMomBot` (0.37), and `TSMOMTrendBot` (0.24) fitted on the first half of historical data.
  - **Goal:** Monitor live paper performance over a ~6-month horizon (targeting March 2027) before re-fitting or promoting.
- [ ] **Covariance-Aware Weighting Study**
  - **Action:** Test covariance-aware weighting (Equal Risk Contribution / Minimum Variance) against the current inverse-volatility weights on out-of-sample H2 data.
- [ ] **GoldenButterflyMomBot Live Audit**
  - **Action:** Audit live performance in March 2027 to verify if drawdown and correlation match backtest expectations.
- [ ] **Keep Paused Bots Suspended**
  - Ensure the following remain paused (`suspend: true` in `values.yaml` / cluster):
    - `RecursiveDecayHarvestBot` (t = −2.06)
    - `EURUSDTreeBot` (t = −2.40)
    - `SynthesizedHyperConvexityBot`, `AIHedgeFundBot`, `DeepSeekToolBot`
    - `SqueezeMomentumBot`, `StockNewsSentimentBot`, `option_CrossVolBot` (t = −3.56)

---

## 3. Option Bots & Advanced Sleeves

- [ ] **Option History Maturity**
  - **Details:** Most option bots currently sit in the "too short" window (~14 daily observations).
  - **Action:** Allow option paper bots (Wheel, PMCC, IndexVol with VIX term-ratio gate) to accumulate history before final alpha/t evaluation.
- [ ] **Option Quotes Storage Management**
  - **Details:** Daily option chain captures write ~35k rows/day (~2–2.5 GB/year).
  - **Action:** Monitor node disk space and verify database growth and retention policies.

---

## 4. Development & CI Hygiene

- [ ] **Format Check Compliance**
  - **Rule:** `ruff format --check` and `ruff check` cover Python and Markdown snippets. Always run checks locally before pushing to prevent silent CI freezes (`ci-format-check-blocks-deploy`).
- [ ] **SOPS Secrets Management**
  - **Rule:** Keep `.env.secret.enc` re-encrypted (`sops encrypt`) whenever `.env.secret` is modified. Never commit plaintext secrets.
