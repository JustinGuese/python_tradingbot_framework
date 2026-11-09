# PLAN: one allocation across the independent sleeves (F1 + R1 follow-up)

Why a study and not a new bot: the sleeves are already running as separate paper bots
(QuarterlyTeCostGate, SmaDailyGated, buyback drift, 13D activist); the futures sleeve (F1) is a research
rebuild on ETF proxies. A blended bot would have to re-run the event books statelessly, which they cannot do.
So the deliverable is the allocation (weights) and its evidence, which is what a copier / C2 weight needs.

Global rules (non-negotiable):
- Research only: one new `scripts/onetime_*.py` and one `docs/backtests/*.md`. Do NOT touch `helm/`, any
  `tradingbot/*.py` bot, `utils/`, CLAUDE.md or any existing script/doc. Nothing is suspended or promoted.
- No `backtest_bot`, no `scripts/onetime_retune_walkforward.py`. Plain-pandas, reuse the stream builders that
  already exist (import or copy from `scripts/onetime_sleeve_correlation.py`, `scripts/onetime_cft_longhistory.py`,
  `scripts/onetime_lift_study.py`, `scripts/onetime_c2_blend_backtest.py`). Run once in the foreground with
  `timeout 540`. Cache downloads under /tmp.
- Alpha vs QQQ per CLAUDE.md (weekday rows, daily returns, alpha = resid mean x 252, t = resid mean / resid std
  x sqrt(n)), report alpha, t, beta, corr, max DD, H1 and H2. T-bill yield on cash where a stream has cash.
- Verdict wording: "unproven" unless t >= 2 and alpha > 0 in both halves. Never round up.

## Batch 1 - one step

### B1 - cluster-representative blend, weights fit on the first half only
Files: `scripts/onetime_sleeve_blend.py`, `docs/backtests/sleeve-blend-2026-11.md`
Streams (one representative per redundant cluster from `docs/backtests/sleeve-correlation-2026-11.md`):
1. QuarterlyTeCostGate (momentum cluster; the other two members are 0.72-0.96 residual-correlated with it)
2. SmaDailyGated (trend cluster, best t of its cluster)
3. ContinuousFuturesTrend on the 6 ETF proxies, 5 bp (F1, `docs/backtests/cft-longhistory-2026-11.md`)
4. buyback_sp100 (lift-study stream)
5. activist_13d (lift-study stream)
6. the C2 blend sleeve (TARegimeMultiAsset / GoldenButterfly / TSMOM, as in `docs/backtests/c2-blend-2026-09.md`),
   only if it can be rebuilt in plain pandas; otherwise list it as skipped.
Method:
- Common window = the latest stream start (the event books start 2010-02); H1 = first half, H2 = second half.
- Fit weights on H1 ONLY, inverse-volatility of each stream's H1 returns (same rule as the C2 blend), normalised
  to sum 1. Also report equal weight as a reference. No other fitting, no tuning.
- Report, on H2 (the out-of-sample half) and on the full window: alpha, t, beta, corr, max DD for the
  inverse-vol blend, the equal-weight blend, and each single stream on the same window.
- Leave-one-out: the blend's H2 alpha/t with each stream removed (shows what each sleeve adds).
- Realism notes in the doc: 13D has few trades (its t is fragile), buyback/13D beta, and that H1-fit weights are
  stated so they can be used as a starting copier allocation.
Acceptance:
- Doc has the weights table, the H2 and full-window tables for blend vs single streams, and the leave-one-out table.
- Doc states plainly whether the blend beats its best single stream on H2 t, and the verdict per the bar.
- Script runs end to end; no other file is changed.
Tests: none (research script).

## Batch 2 - none. If B1's verdict is not a fail, the next step is the copier-weight change, which needs your decision.
