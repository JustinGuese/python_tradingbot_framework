# PLAN: build, backtest and tune the four new option ideas

Working file, deleted after merge. Ideas come from the 2026-11-10 option-strategy review.

## Ground rules (apply to every step)

- **Tuning objective is walk-forward alpha t vs QQQ**, not raw Sharpe or return (CLAUDE.md:
  tuning on those selects beta). Every result table also shows Sharpe, CAGR (`true_cagr`),
  beta, corr and max DD, so the Sharpe/return view is visible next to it.
  Protocol (same as `docs/backtests/retune-2026-09.md`): pick on H1, judge on H2, ship tuned
  values only if the H1 winner beats the a-priori defaults on H2. Otherwise defaults ship.
- **Grid cap: at most ~300 combinations per bot**, economically motivated levers only.
  The data windows are short; a big grid just finds noise.
- Real option data: `option_quotes` in the cluster DB holds **~50 underlyings from 2019-05-10 to
  2026-11-09** (about 1,200 sampled days each; verified 2026-11-10), not just SPY/AAPL plus 25 names.
  Sampled ~3 expiries/day, ~22 strikes each. This makes S1 (7 years × ~50 names) and C1 testable
  far beyond what was assumed below; where a step says "25 names 2025-2026" use the full history. Needs a read-only port-forward to the cluster DB
  (`kubectl port-forward -n tradingbots-2025 svc/psql-service 5432:5432`, `POSTGRES_URI` set).
  The orchestrator has it running on localhost:5432 (read-only use only: SELECTs, never write to
  production tables). `.env.secret` is copied into your worktree (gitignored): never print, log or
  commit it. Run DB-backed scripts from the worktree root as
  `/home/jguese/code/tradingbot/python_tradingbot_framework/.venv/bin/python /home/jguese/code/tradingbot/python_tradingbot_framework/.git/agy-wt/withenv.py scripts/<script>.py ...`
  (it loads `.env.secret` via python-dotenv and puts the worktree first on sys.path; do NOT
  `source` .env.secret in a shell, the password has special characters). Never `pkill -f`.
  Pass `POSTGRES_URI` is already inside `.env.secret`.
- New logic goes in NEW modules, so steps never share a file. Only R1 touches helm/README/mkdocs.
- A bot whose out-of-sample result is negative still gets built and registered, but with
  `suspend: true` and the reason in the helm comment (the CrossVol precedent).
  Positive or inconclusive results ship live (paper), unsuspended.
- No linters, mypy or full suite in worker steps. Few, important pytests only.

---

## Batch 0: shared tuning harness

### S0: walk-forward grid runner
Files: `scripts/option_tune_common.py`, `tests/test_option_tune_common.py`
- `walk_forward(grid, run_fn, split, rank_key="alpha_t")` returns a DataFrame: one row per
  combination with H1/H2/full alpha, t, beta, corr, Sharpe, `true_cagr`, max DD, trade count.
- Reports the a-priori defaults row, the H1 winner, its H2 result, the top-10 mean H2 t,
  and the share of the grid with H2 t > 0 (same columns as `option-bots-round2-2026-09.md`).
- `ship_decision(defaults_h2, winner_h2)` returns "winner" or "defaults".
- Reuses `alpha_stats` (`tradingbot/utils/alpha_report.py`) and `metrics` from
  `scripts/onetime_option_bots_backtest.py`. Writes a markdown table via `to_markdown`.
- Tests: ship_decision picks defaults when the H1 winner loses on H2; an empty or too-short
  H2 series yields NaN t and never "winner".

---

## Batch 1: the four bots (parallel, disjoint files)

### P1: option_PutWriteBot (SPY/QQQ cash-secured put-write with curve-slope and trend gates)
Files: `tradingbot/utils/putwrite_rules.py`, `tradingbot/option_putwritebot.py`,
`scripts/onetime_putwrite_backtest.py`, `tests/test_putwrite.py`,
`docs/backtests/putwrite-2026-11.md`
- `PutWriteRules` (frozen dataclass) + pure `decide_putwrite(market, holdings, rules, underlying)`
  built on `option_decide` Market/Holdings (pattern: `decide_indexvol`, `OptionStrategyBot`).
  Cash-secured short put (cash covers strike × 100), assigned shares are not held:
  roll/close before expiry (cash-settled model, same as IndexVol) or take assignment
  like WheelBot, whichever `options` already supports for index ETFs.
  Check `tradingbot/option_wheelbot.py` first and reuse its put selection.
- Levers: underlying {SPY, QQQ}; put delta {0.10, 0.16, 0.25, 0.30}; DTE {21, 35, 45};
  max VIX/VIX3M {None, 1.0, 0.95}; trend filter {none, close > SMA200}; min IV-fair gap
  {None, 0, 3pts}; take profit {None, 0.5}; exit DTE {7, 14, 21}. Defaults chosen a priori:
  SPY, 0.25Δ, 35 DTE, no gates, 50% / 21 DTE.
- Backtest 1 (synthetic, 2000 → 2026, split 2013-06): reuse the pricing of
  `scripts/onetime_index_vol_backtest.py` (calibrated skew, ^VIX × 0.85, ^VXN × 0.90 for QQQ).
- Backtest 2 (real surface, 2019-05 → 2026-09): `SurfaceReplayMarket(live_expiries=True)`
  via `utils/option_replay.run_strategy`, SPY only (QQQ if the backfill has it).
- Tune with `scripts/option_tune_common.walk_forward`. The tuned `RULES` live in the bot class;
  the doc states plainly whether real-surface alpha t ≥ 2 holds and the beta (expect 0.4–0.6).
- Tests: no entry when VIX/VIX3M exceeds the gate or close < SMA200 with the filter on;
  cash-secured sizing never exceeds free cash; take-profit and exit-DTE close.

### S1: option_ImpliedSignalBot (stock selection from option-implied signals)
Files: `tradingbot/utils/implied_signal.py`, `tradingbot/optionimpliedsignalbot.py`,
`scripts/onetime_implied_signal_study.py`, `tests/test_implied_signal.py`,
`docs/backtests/implied-signal-2026-11.md`
- Signals per name per day from `option_quotes` / `vol_surface`: ATM call-IV minus put-IV
  (Cremers-Weinbaum), 25Δ put IV minus ATM IV (put skew), RR25 and the 30-day change of each.
  Cross-sectional rank, smoothed over N days.
- Study: weekly rank → next-week and next-month return. Report rank IC, top-minus-bottom
  quintile spread, and long-only top-N vs the equal-weight universe and vs QQQ (alpha, t, beta).
  Include `n` and the t-stat on the IC, and say plainly how few independent observations
  there are (about 21 months, 25 names).
- Bot: equity bot like `EarningsInsiderTiltBot` (`Bot` + `rebalancePortfolio`), long the
  top-N names by the chosen signal, remainder cash (long-only; the framework's paper
  portfolios are the target). The long/short variant is measured in the study only.
- Levers (small, to avoid overfit): signal {cp_spread, put_skew, rr25, composite},
  smoothing {1, 5, 10}, top-N {3, 5, 8}, rebalance {weekly, biweekly}. Defaults: composite, 5, 5, weekly.
  Pick on the first half of the window, judge on the second.
- If the DoltHub names are too few to run it, the study says so, the bot logs the signals
  daily to `vol_surface` consumers, and the doc defines the date by which IC can be measured.
- Tests: ranks are cross-sectional (invariant to a constant shift of all names); a name
  with missing option data is excluded, not scored zero; weights sum ≤ 1.

### F1: option_FOMCVolBot (index event vol into FOMC)
Files: `tradingbot/utils/fomc_vol_rules.py`, `tradingbot/option_fomcvolbot.py`,
`scripts/onetime_fomc_vol_study.py`, `tests/test_fomc_vol.py`, `docs/backtests/fomc-vol-2026-11.md`
- The sources disagree on direction (short vs long the FOMC straddle), so this step measures it.
- Study: for each FOMC date (`utils/macro_calendar`, 2006–2027 static list), enter at the
  prior close and exit the next close, SPY ATM straddle (and 10Δ strangle) priced off the day's
  real surface 2019 → 2026 (`utils/option_surface`); the synthetic ^VIX-based version for
  2006 → 2019. Test both sides, delta-hedged and not. P&L at real bid/ask.
  Also test CPI and NFP days as a comparison, to see whether FOMC differs.
- `FOMCVolRules` has `side` {"short", "long"} and defined-risk structure options (iron fly vs
  straddle). `decide_fomcvol` is pure. Entry window: the last run before the close of the session before FOMC.
  Exit: the first run after the announcement session.
- Tune side, structure, wing width, strike offset on 2006–2019 (H1); judge on 2019–2026.
  About 70 meetings in total, so the doc must report the t of the mean and a bootstrap
  interval, and must not claim alpha vs QQQ from n ≈ 40 trades without that caveat.
- Tests: enters exactly one session before an FOMC date, exits after the announcement;
  never opens off-hours or when the chain isn't live; max loss ≤ risk budget.

### C1: CrossVol ranking filters (study, then conditional wiring)
Files: `tradingbot/utils/crossvol_rank.py`, `scripts/onetime_crossvol_rank_study.py`,
`tests/test_crossvol_rank.py`, `docs/backtests/crossvol-rank-2026-11.md`,
`tradingbot/utils/option_rules.py` (only `CrossVolRules`, default-off fields)
- Prior: CrossVol on 25 real names scored alpha −10%/yr, t −3.56 and is paused
  (`values.yaml`). This step is a kill-gate, not a rescue.
- Add candidate ranks to the replay: vol-of-vol (std of daily ATM IV changes), idiosyncratic
  vol, and the existing gap, all as `crossvol_rank.py` pure functions. Run the existing
  `scripts/onetime_option_replay_backtest.py --strategy crossvol` machinery with each rank,
  and a delta-hedged-call-writing variant à la Han et al. as a comparison.
- Ship the new fields in `CrossVolRules` (default None) only if some rank turns H2 alpha t
  above 0 AND beats the live rules on H2. If not, the doc is the deliverable, no rule changes,
  and the bot stays paused.
- Tests: ranking is deterministic and drops names with too little IV history;
  the default `CrossVolRules()` behaves exactly as before.

---

## Batch 2: registration and docs (after Batch 1 results)

### R1: register the bots, index the docs
Files: `helm/tradingbots/values.yaml`, `README.md`, `mkdocs.yml`
- Add `option_putwritebot`, `optionimpliedsignalbot`, `option_fomcvolbot` entries with
  schedules that don't collide with the 14:30–16:00 option cluster; `suspend: true` with the
  reason on any bot whose doc ends negative OOS. Do not add CrossVol changes unless C1 shipped them.
- Add the new docs to `mkdocs.yml` nav and one line each to the README option section.
- Test: `helm template helm/tradingbots` renders; each new bot module imports and exposes
  a `RULES` and a `run_bot` entry.

---

## Final review (orchestrator)
Run the new tests once, `git diff pre-agy` against this plan, confirm no config or test was
weakened and no result was cherry-picked (every doc shows the defaults row next to the winner).
Then merge to main and delete PLAN.md.
