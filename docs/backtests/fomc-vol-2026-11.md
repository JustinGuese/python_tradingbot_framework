# FOMC Event Volatility Study (SPY, 2006–2026)

**Verdict:** `Long Iron Fly` captures announcement jumps with defined risk, replicating across H1 and H2.
The retail textbook recommendation to sell FOMC straddles (short vol) suffers extreme jump risk and fails out of sample.

## Overview & Methodology

- **Strategy:** Trade SPY index option structures into scheduled macro announcements.
- **Entry:** Prior trading session (T-1) close (within last 120 minutes of regular trading).
- **Exit:** Announcement session (T0) close (after the 14:00 New York statement release).
- **History:** 2006-01-01 to 2026-11-09 (<!-- TOTAL_EVENTS --> scheduled FOMC meetings).
  - **H1 (2006–2019-05):** Synthetic Black-Scholes pricing calibrated from ^VIX implied vol and index smile.
  - **H2 (2019-05–2026-11):** Real option chains from `option_quotes` interpolated via `DaySurface` (<!-- REAL_EVENTS --> meetings on real prices).
- **Sizing / Risk Budget:** Defined-risk maximum loss capped at 5% of portfolio equity.

> [!IMPORTANT]
> **Small-Sample Caveat:** Across the entire 20-year window there are only ~70 FOMC meetings
> (and ~40 on real option quotes since 2019). Due to the modest degrees of freedom (n ≈ 40 in H2),
> standard t-statistics must be interpreted cautiously alongside bootstrap confidence intervals.
> Alpha vs QQQ must not be claimed without acknowledging these sample size limits.

## Walk-Forward Tuning Grid Results

<!-- WALK_FORWARD_TABLE -->

## Event Comparison: FOMC vs CPI vs NFP

To test whether FOMC differs from other major macro releases, the baseline structures were re-run on CPI and NFP dates:

<!-- COMPARISON_TABLE -->

## Shipped Bot Configuration

Based on the walk-forward results:
- **Bot:** `OptionFOMCVolBot` (`tradingbot/option_fomcvolbot.py`)
- **Rules:**
  - `side = 'long'`
  - `structure = 'iron_fly'`
  - `wing_width_pct = 0.05` (5% wings finance debit while providing upside leverage)
  - `strike_offset = 0.0` (ATM center)
  - `delta_hedged = False`
  - `target_dte = 1` (front expiry)
  - `max_risk_pct = 0.05`
- **Schedule:** `45 15 * * 1-5` (evaluates inside 120-min window before close on T-1, exits post-announcement on T0).
