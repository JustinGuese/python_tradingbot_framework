# PLAN: what to improve / add next (items 1, 2, 4 of the shortlist)

Global rules for every step (non-negotiable):
- Research only: new `scripts/onetime_*.py` + a `docs/backtests/*.md` per step. Do NOT touch `helm/`,
  any `tradingbot/*.py` bot, `utils/`, CLAUDE.md or any existing script/doc. Nothing is suspended.
- Do NOT call `backtest_bot` or `scripts/onetime_retune_walkforward.py` (about 5 min per run; the last round
  timed out on it). Use plain-pandas equity curves like `scripts/onetime_quantsmd_cash_yield.py`
  and `scripts/onetime_lift_study.py`: yfinance `auto_adjust=True` closes (cache them under /tmp),
  weights decided at close of day t earn day t+1, 5 bp per unit of turnover, cash earns 0 unless the step
  says T-bill (`^IRX`). Each script must run end to end in under ~9 minutes (run once in the foreground with
  `timeout 540`).
- Alpha vs QQQ per CLAUDE.md: weekday rows only, daily returns, beta = cov/var, alpha = resid mean x 252,
  t = resid mean / resid std x sqrt(n); report alpha, t, beta, corr, max DD, H1 and H2 (two halves).
  Use `tradingbot.utils.alpha_report.alpha_stats` if it fits. No parameter tuning: defaults only.
- Verdict wording: "unproven" unless t >= 2 and alpha > 0 in both halves; say it plainly, never round up.
- One tiny pytest per step only for a pure helper (cheap), or none.

## Batch 1 - three independent steps (disjoint files), run in parallel

### R1 - which sleeves are actually different? (item 1)
Files: `scripts/onetime_sleeve_correlation.py`, `docs/backtests/sleeve-correlation-2026-11.md`
Streams (reuse the existing code, do not re-implement what already exists):
- the six quantsmd survivors: MonthlyRotation, VolTargetMomentum, QuarterlyTeCostGate, RiskParityCapped, SmaDaily,
  SmaDailyGated (`run_bot_sim` in `scripts/onetime_quantsmd_cash_yield.py`, with the T-bill lever on);
- the C2 blend members TARegimeMultiAsset, GoldenButterfly, TSMOM (see `scripts/onetime_c2_blend_backtest.py`
  and `docs/backtests/c2-blend-2026-09.md`; if a member cannot be rebuilt in plain pandas, list it as skipped);
- the event streams buyback_sp100 and activist_13d from `scripts/onetime_lift_study.py`.
Output doc: (a) correlation matrix of raw daily returns on the common window; (b) correlation matrix of
residuals after regressing each stream on QQQ (the one that matters); (c) for every stream its alpha/t/beta;
(d) an equal-weight blend of the six survivors vs its best member (alpha, t) to show the dilution/diversification
effect; (e) three lines of reading: which pairs are redundant (residual corr > 0.7) and which streams are
the most independent of the rest. No bot is promoted.
Acceptance: doc has matrices (a) and (b) with at least the six survivors + 2 other streams; script runs.

### F1 - ContinuousFuturesTrend on long-history proxies (item 2)
Files: `scripts/onetime_cft_longhistory.py`, `docs/backtests/cft-longhistory-2026-11.md`
- The bot (`tradingbot/quantsmd_continuous_futures_trend.py`, `compute_weights`) uses 6 continuous futures,
  data only from 2019. Rebuild the same rule (long-only, fast 63 / slow 252, 63d inverse-vol, monthly) on
  long-history ETF proxies: SPY (MES), QQQ (MNQ), IEF or TLT (ZN), USO or DBO (CL), GLD (GC), DBA or a
  grain ETF (ZC), as far back as the data goes (state the real start per proxy; common window starts when all
  exist, plus a second run on the proxies that exist from 2006 only). Call `compute_weights` itself, do not
  re-implement the signal.
- Report: whole window, then sub-periods 2006-2012 (incl. 2008), 2013-2019, 2020-2026 (incl. 2022), each with
  alpha, t, beta, corr, max DD; plus H1/H2 and a 20 bp cost run. Also report the 2019+ window so it can be
  compared with the documented futures result (+4.9%, t 2.45).
- Doc states: does the t hold outside 2019+, and is 2008 / 2022 handled (max DD in those years)?
Acceptance: doc has the sub-period table and the 2019+ comparison line; script runs.

### X1 - FX carry: does a proper carry sleeve have alpha? (item 4)
Files: `scripts/onetime_fx_carry_study.py`, `docs/backtests/fx-carry-2026-11.md`
- Spot FX bots lose (t -1.2 to -1.6) because spot returns carry no interest. Build carry properly:
  G10 vs USD spot from yfinance (`EURUSD=X`, `GBPUSD=X`, `AUDUSD=X`, `NZDUSD=X`, `USDJPY=X`, `USDCAD=X`,
  `USDCHF=X`, `USDNOK=X`, `USDSEK=X`; invert where the pair is USD-base) and 3-month short rates from
  FRED public CSV (`https://fred.stlouisfed.org/graph/fredgraph.csv?id=...`, e.g. IR3TIB01EZM156N,
  IR3TIB01GBM156N, IR3TIB01AUM156N, IR3TIB01NZM156N, IR3TIB01JPM156N, IR3TIB01CAM156N, IR3TIB01CHM156N,
  IR3TIB01NOM156N, IR3TIB01SEM156N, IR3TIB01USM156N; monthly, forward-fill). If a series is unavailable,
  drop that currency and say so. Daily carry return per currency = spot return + (r_foreign - r_USD) / 252.
- Strategy (long-only, matching our framework; no leverage): each month hold the top 3 currencies by
  rate differential vs USD, equal weight, rest in USD cash earning the US 3m rate. Also report the
  long/short version (top 3 long, bottom 3 short) separately as a reference only, labelled "not tradeable here".
- Also report the existing spot-only momentum for the same window as the baseline.
- Doc: alpha/t/beta/corr/max DD/H1/H2 for each variant, the carry-vs-spot-only difference, and one line on
  whether it is a different return source (corr with QQQ and with the survivors if cheap). Verdict "unproven"
  unless the bar is met; if long-only carry fails, say so and do not suggest tuning.
Acceptance: doc has long-only carry, spot-only baseline and long/short reference rows; script runs.

## Batch 2 - none. After merge, the report lists the verdict per step; follow-up bots only if a verdict is not "fail".
