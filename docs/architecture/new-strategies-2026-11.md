# New Strategies & Architecture Review (November 2026)

This document details the architectural decisions and strategy additions implemented from the November 2026 Strategy Review (`PLAN-alphaideas.md`).

---

## 1. Core Architectural Principles

### 1.1 The Stateless Live Rebalance Rule

**Problem Statement:**
In Kubernetes-orchestrated execution, live trading bots run as scheduled CronJobs. Each run initializes a fresh Python process and a new instance of the `Bot` class. Any state stored on the instance (e.g., `self.last_target`, `self.rolling_turnover`, `self.last_rebalance_bar`) is lost immediately upon job termination.
In bots such as `QuantsmdQuarterlyTeCostGateBot`, this previously caused every run to assume an empty state and execute the "first call builds the book" logic daily, turning a quarterly low-turnover strategy into daily churn.

**The Stateless Rule:**
1. **No Instance State:** Bots must not rely on instance attributes to track previous targets, fill history, or rebalance schedules.
2. **Read Real Holdings via `current_weights(self)`:** When running live, the bot inspects its persisted database state via `current_weights(self)` from `tradingbot/utils/live_state.py`. This resolves signed portfolio weights valued at the latest market prices.
3. **Deterministic Rebalance Triggers:** A rebalance occurs only if:
   - The book is completely flat (`current_weights == {}`).
   - The current bar marks a scheduled rebalance boundary (e.g., first bar of a calendar month or quarter).
   - An active risk/tracking-error gate against actual holdings is breached.
4. **Stateless Composite Recomputation:** Composite bots (e.g., `SleeveBlendBot`) determine each component's target by truncating price series to the sleeve's last scheduled rebalance bar (e.g., last month-first bar for trend, last quarter-first bar for quarterly rotation) and executing `compute_weights`. The resulting weights are deterministic and identical between rebalance dates without instance memory.

---

### 1.2 Idle Cash Parking in SHV (`tradingbot/utils/cash_park.py`)

**Problem Statement:**
Paper bots historically evaluated cash at a 0% return. Many low-beta or event-driven strategies keep 40% to 65% of portfolio equity unallocated between signals. This introduced an artificial 2% to 3%/yr performance drag relative to real accounts, where idle cash earns the short-term Treasury rate (e.g., via SHV or broker interest).

**Implementation:**
- Function: `park_idle_cash(weights: dict[str, float], proxy: str = "SHV", max_gross: float = 1.0, min_weight: float = 0.01) -> dict[str, float]`
- Allocates residual purchasing power to `proxy`:
  $$\text{proxy\_weight} = \min(1.0 - \text{signed net weight}, \text{max\_gross} - \text{gross})$$
- Ensures `proxy_weight >= min_weight`; never shorts the proxy; returns an immutable dictionary copy.
- **Bot Contract:** Every new bot adds `SHV` to its `tickers`, excludes `SHV` from its signal universe, and applies `park_idle_cash` to its target weights.
- **Copier Stacking Protection:** `LiveTradeCopier` counts any bot-provided `SHV` weight prior to applying venue-level cash parking, preventing duplicate allocations.

---

### 1.3 Point-in-Time Universe Reconstruction (`tradingbot/utils/universes.py`)

**Problem Statement:**
Backtesting historical multi-year strategies on today's index constituents induces severe survivorship bias (+4.7%/yr measured on large-caps). Many companies that failed, merged, or were acquired are absent from today's list.

**Implementation:**
- `data/sp500_changes.csv`: Historical record of additions and removals from 1976 to present.
- `sp500_members(as_of) -> tuple[str, ...]`: Rebuilds the exact point-in-time index composition for any historical date by reversing changes from the current `SP500` list.
- Missing yfinance data for delisted components is tracked and reported per study year, preserving explicit documentation of residual survivorship tilt.

---

## 2. New Strategies & Sleeves

### 2.1 SleeveBlendBot (Five-Sleeve Multi-Strategy Blend)
- **Module:** `tradingbot/sleeveblendbot.py`
- **Class:** `SleeveBlendBot`
- **Component Sleeves & Allocation (H1 Fit):**
  - `ContinuousFuturesTrend` (39.4%): Multi-asset trend on liquid ETF proxies (`SPY`, `QQQ`, `IEF`, `USO`, `GLD`, `DBA`).
  - `buyback_sp100` (20.5%): Event drift following Form 8-K buyback authorizations.
  - `SmaDailyGated` (18.5%): Daily trend-gated large-cap momentum.
  - `QuarterlyTeCostGate` (18.0%): Quarterly tracking-error gated rotation.
  - `activist_13d` (3.6%): Schedule 13D activist filing drift.
- **Composition Engine:** Statelessly evaluates price sleeves at their respective rebalance bars and merges active event positions. Unallocated event weight is parked in `SHV`. Total gross is bounded by 1.0 (Reg T compliant).

---

### 2.2 NeutralStockBot (Market-Neutral Equity Book)
- **Module:** `tradingbot/neutralstockbot.py`
- **Core Engine:** `tradingbot/utils/cs_neutral_book.py`
- **Class:** `NeutralStockBot`
- **Signal Generation:**
  - True residual momentum: 12-1 month return of residuals from a 36-month OLS regression on market returns ($R_{i, \tau} = \alpha_i + \beta_i R_{m, \tau} + \epsilon_{i, \tau}$), cleanly separating idiosyncratic performance from market beta.
  - 1-month short-term reversal.
  - Within-sector cross-sectional z-scoring using `SP500_SECTOR`.
- **Portfolio Construction:**
  - Monthly rebalance, $k = 25$ long and $k = 25$ short.
  - Target long weight = 0.90, short weight = -0.90 (net dollar-neutral = 0.0, gross = 1.80, preserving 0.20 cash buffer).
  - Enforces equal counts per sector side across all represented sectors.
  - Shortable universe: `SHORTABLE = SP500`.

---

### 2.3 BuybackSectorHedgedBot (Sector-Hedged Buyback Drift)
- **Module:** `tradingbot/buybacksectorhedgedbot.py`
- **Class:** `BuybackSectorHedgedBot`
- **Mechanism:**
  - Identifies active long Form 8-K buyback authorizations from `BuybackDriftBot`'s event pipeline.
  - Rather than shorting QQQ (which only neutralizes broad market beta without reducing sector noise), each long stock is offset by a short position in its corresponding Sector SPDR ETF (`SECTORS9` via `tradingbot/utils/sector_map.py`).
  - Equal weighting per active pair; dollar-neutral per event.
  - Max gross $\le 2.0$, idle purchasing power parked in SHV.

---

### 2.4 OptionImpliedLongShortBot (Cross-Sectional Skew Long/Short)
- **Module:** `tradingbot/optionimpliedlongshortbot.py`
- **Class:** `OptionImpliedLongShortBot`
- **Mechanism:**
  - Evaluates 25-delta risk reversal (`rr25`) and composite implied volatility skew across option capture names (`OPTION_CAPTURE_UNIVERSE`).
  - Long top-$N$ / short bottom-$N$ (e.g., $N=5$ or $10$), rebalanced weekly on Fridays.
  - Fully dollar-neutral (net = 0.0, gross $\le 1.8$), eliminating index exposure and testing pure implied skew predictive power against an equal-weight universe baseline.

---

## 3. Summary of Promotion Bars

Per `CLAUDE.md` and `PLAN-alphaideas.md`:
- **Production Gate:** Alpha vs QQQ $> 0$, $t \ge 2.0$, positive alpha in both walk-forward halves (H1 and H2), $\beta < 0.5$.
- **Searched Grids:** Any parameter or variant selected from a multi-parameter search must clear $t \ge 3.0$ to be acknowledged as an edge (`TODOS.md`).
- **Reporting Standard:** Always report performance against both QQQ and an Equal-Weight universe hold. Cash yield must be reported at both 0% and T-bill rate.
