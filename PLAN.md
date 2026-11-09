# PLAN: quantsmd follow-up (measurement + levers; nothing is suspended)

Rules for every step: do NOT touch `helm/`, do NOT suspend or remove any bot, do NOT change any bot's
default parameters. We are gathering data; all quantsmd CronJobs keep running. New work is
scripts + docs only. Alpha is always vs QQQ (see CLAUDE.md). Offline, yfinance, scratch SQLite, like
`scripts/onetime_quantsmd_backtest.py`.

## Batch 0 - shared contract (one step)

### S0 - harness reports a real CAGR
Files: `scripts/onetime_quantsmd_backtest.py`, `tests/test_quantsmd_cagr.py`
- In `run_one`, rename the misnamed `cagr` field (it is `yearly_return`, a TOTAL return fraction) to
  `total_return`, and add `cagr = (1 + total_return) ** (1 / years) - 1` using the bot's own window
  (`years` from first/last equity-curve stamps). Keep `qqq_cagr`, `bh_cagr` as they are.
- Factor the formula into a small pure function `true_cagr(total_return, start, end)` in that script
  (or importable helper) so it is testable.
- Acceptance: `true_cagr(4.58, "2006-03-16", "2026-11-06")` is within 0.1 of 8.7 (percent); a negative
  total return works; zero-length window raises or returns None, not a crash.
- Tests (2): the MonthlyRotation example above; negative/edge case.

## Batch 1 - parallel, disjoint files

### D1 - correct the CAGR column in the two docs
Files: `scripts/onetime_quantsmd_fix_doc_cagr.py`, `docs/backtests/quantsmd-2026-11.md`,
`docs/backtests/quantsmd-2026-11-all.md`, `tests/test_quantsmd_fix_doc_cagr.py`
- Script reads each table row's `Start` (year or month-day) and the doc's "CAGR" value (which is really a
  total-return multiple printed as %), recomputes true CAGR to end 2026-11-06, rewrites the column
  (rename header to `CAGR (true)`), leaves intraday rows (start is month-day) as `n/a`.
- Add a note under each doc's method section: the old column was total return, corrected 2026-11-09, values
  are approximate (start year, not exact first bar). Fix the sentence "lag QQQ by 5-15 points of CAGR" to
  "about 7-10 points".
- Acceptance: MonthlyRotation row shows ~8.8%; SmaDaily ~7.7%; row count unchanged; script idempotent
  (second run changes nothing).
- Tests (2): conversion of a known row; idempotency.

### C1 - cash-yield lever across the surviving quantsmd books
Files: `scripts/onetime_quantsmd_cash_yield.py`, `docs/backtests/quantsmd-cash-yield-2026-11.md`
- For MonthlyRotation, VolTargetMomentum, QuarterlyTeCostGate, RiskParityCapped, SmaDaily, SmaDailyGated:
  run `backtest_bot`, then add 13-week T-bill yield (use the same series as `scripts/onetime_lift_study.py`)
  to the cash share of each bar, recompute alpha / t / beta vs QQQ, H1/H2. No parameter changes.
- Doc: table baseline (cash 0%) vs with-yield, avg cash share, plus 2-3 lines of interpretation. Say
  "unproven" unless t >= 2 in both halves.
- Acceptance: script runs end to end on at least 2 bots (full run may be started in background and the doc
  generated from whatever finished; list unfinished bots in the doc).

### G1 - SPY SMA200 gate on two more books (untuned)
Files: `scripts/onetime_quantsmd_gate_more.py`, `docs/backtests/quantsmd-gate-more-2026-11.md`
- Apply the exact SmaDailyGated rule (SPY < SMA200 -> cash, `off_scale` 0.0, window 200) to the weight
  output of MonthlyRotation and RiskParityCapped via a wrapper inside the script (do NOT add new bot files
  and do NOT edit existing bots). Compare gated vs ungated: alpha, t, beta, H1/H2, max DD, and the
  gated-minus-ungated difference regressed on QQQ (same layout as `gate-other-markets-2026-11.md`).
- Acceptance: doc has the two comparison tables; the verdict states pass/fail on the bar (t >= 2, both
  halves positive, beta < 0.5) without tuning anything.

## Batch 2 - one step, depends on Batch 1 docs

### W1 - walk-forward check and summary
Files: `scripts/onetime_quantsmd_walkforward_survivors.py`, `docs/backtests/quantsmd-survivors-2026-11.md`
- Reuse `scripts/onetime_retune_walkforward.py` with `RETUNE_START=2000-01-01` and
  `RETUNE_SPLIT=2013-01-01` on the six survivors' own `param_grid` (grid on H1 by `alpha_t`, judge on H2).
  Ship nothing automatically: the doc only reports whether any H1 winner beats defaults on H2.
- Summary doc links the cash-yield and gate docs, and states one conclusion line per bot.
- Acceptance: doc exists with the defaults-vs-winner H2 table; no bot file or helm file is modified.

## Explicitly out of scope (per owner)
Suspending intraday / FX / churn / duplicate bots. They keep running to gather live data.
