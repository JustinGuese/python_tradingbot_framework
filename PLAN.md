# PLAN — retail switch to InstitutionalFlowBot + a real 13F-based institutional bot (gembuild)

Branch: `agy/workinstitution` (integration worktree `.git/agy-wt-inst/integ`, cut from `agy/work` at fa3fc46).
Other sessions use `agy/work` and `agy/workshorts`: never switch the shared checkout, never touch their worktrees.
Step worktrees: `$G/agy-wt-inst/<id>` on branch `agy/inst-<id>`; logs go in `$G/agy-wt-inst/logs/<id>.log`
(a log named like a worktree dir broke the last launch).

Rules for every step: touch only listed files; max 2 test rounds; never weaken configs/tests; default is LIVE
(no new dry-run defaults); SEC requests use the existing generic `SEC_USER_AGENT` of `utils/buyback_scan.py`
(no personal contact details anywhere); only CUSIPs go to external mappers.

Judging bar (CLAUDE.md): alpha vs QQQ, t >= 2, positive in both halves, beta < 0.5 preferred. Say "unproven" otherwise.

## Decisions I need from you (defaults used if you say nothing)
1. **Collective2**: DECIDED 2026-11-14, it moves to the retail book together with eToro and MT5. IBKR stays on the low-beta blend.
2. **13F bot**: DECIDED, paper only at first. It is in no copier until 2 live paper rebalances. The bot itself runs live on paper from day one.
3. **Scope**: ignore `agy/work` / `agy/workshorts` and their worktrees; work only on `agy/workinstitution`.
4. **Execution waves** (max 4 parallel): W1 = B0, A1, A2, A3; W2 = B1a, B1b, B1c; W3 = B2; gates; then A4 and B3a/B3b; then B4.

---
# PART A — Switch retail livetrades (eToro, MT5/MQL5) to InstitutionalFlowBot

Ship rule (fixed before any result): the retail book ships only if, on the second half (2019-06 to 2026-11),
CAGR >= 15%, max DD >= -30%, and alpha vs the equal-weight S&P 100 hold is positive in both halves, and it is not
beaten on H2 return AND drawdown by a plain "QQQ above SMA200 else cash" book. If the gated QQQ wins, A does not ship
and I report that instead. If only the blend fails, InstitutionalFlowBot ships alone.

## Batch 0 — nothing shared (Part A needs no contract)

## Batch 1 — parallel (3 steps, disjoint files)
### A1 Joint walk-forward study
Files: scripts/onetime_retail_blend_study.py (new), docs/backtests/retail-blend-2026-11.md (new)
- Streams: InstitutionalFlowBot (reuse `scripts/onetime_institutional_flow_backtest.py` logic, 95-name universe),
  option_ImpliedSignalBot (reuse `scripts/onetime_implied_signal_study.py` output if it exposes the equity curve;
  otherwise drop it and say so), EarningsInsiderTiltBot only if a backtest curve is already reproducible.
- Weights: inverse-vol, fit on H1 (2019-2022) only. Baselines: QQQ, QQQ+SMA200 gate (cash otherwise), EW universe,
  each inst-flow component stream alone.
- Report per half: CAGR, max DD, alpha/t/beta/corr vs QQQ and vs EW, cash share, trades per week, 20 bp cost column.
- Ends with a `SHIP` / `DO NOT SHIP` line applying the ship rule above, and the weights if SHIP.
- Tests (tests/test_retail_blend_study.py): inverse-vol weights use H1 only and sum to 1; the SMA200-gated QQQ baseline
  is cash when QQQ < SMA200 (no look-ahead: signal at close t, return t+1).

### A2 Venue coverage + unmapped-name behaviour
Files: scripts/onetime_retail_venue_coverage.py (new), tests/test_retail_coverage.py (new), docs/backtests/retail-venue-coverage-2026-11.md (new)
- Build on `tradingbot/livetrade/coverage.py`: for every S&P 100 name (incl. PLTR, SNDK, GEV, DELL, ANET) report whether
  eToro and MT5 `is_tradeable` / map it, offline from `symbol_map.json` and adapter logic; live broker lookups are
  run by me and pasted into the doc.
- Pin the behaviour of `LIVETRADE_STRICT_MAPPING=true` (copier.py:101): one unmapped pick aborts the whole sync. Decide
  and document: either unmapped names drop to cash like Collective2's `is_tradeable`, or the bot's universe is filtered.
- Tests: a target with one unmapped ticker on a strict copier aborts (current behaviour pinned); the proposed
  drop-to-cash path leaves the other names traded and the weight in cash/SHV.

### A3 Honest naming and docs
Files: tradingbot/institutionalflowbot.py (docstring/comments only), docs/backtests/institutionalflowbot.md, MARKETING.md
- Describe it as "large-cap momentum with a trend filter". State plainly it uses price and volume only, no 13F, dark-pool
  or options-flow data. Bot name, DB name and schedule stay (history is not orphaned).
- Add the live note: gate-off periods are 100% cash (SHV) and look idle on copy platforms.
- Tests: none (docs/docstring only). Acceptance: `git diff` shows no code-path change.

## Batch 2 — gate (me, not agy)
Read A1/A2 docs. If SHIP, fix the weights and the unmapped-name decision into the B3 prompt. If DO NOT SHIP, skip Batch 3
and report. Also confirm via `kubectl --context=luxvps` why the first `portfolio_worth` row (2026-09-25) was 9,495 on a
10,000 start (suspected one unpriced name), because it distorts the live alpha window.

## Batch 3 — config (1 step, owns values.yaml)
### A4 Retail botWeights
Files: helm/tradingbots/values.yaml, docs/guides/live-trading.md, TODOS.md, tests/test_livetrade_runner.py (add one case only)
- Set `etoro.botWeights` and `mt5Mql5.botWeights` to the A1 result (e.g. `{"InstitutionalFlowBot": 0.7, "OptionImpliedSignalBot": 0.3}`,
  final names/weights from A1). `collective2XauZerifine` (strategy 155809898) gets the same weights. `interactiveBrokers`
  stays on the validated blend. C2 syncs are target-state: the first run liquidates the old blend positions. Both stay demo accounts (`demo: true`); add a TODOS item to open real-money accounts.
- Comment the reason and that the first sync will liquidate and rebuild the demo book (target-state sync).
- Keep strictMapping behaviour per the A2 decision.
- Tests: rendered botWeights JSON for etoro/mt5 parse, sum to <= 1.0 and only name scheduled bots.

---
# PART B — A real institutional bot: 13F "best ideas" book

Thesis: concentrated, low-turnover managers' top positions carry information that survives the 45-day filing lag.
Expect roughly a one-in-three chance of clearing the bar. Pre-registered grid of 8 (top 20/30 x level / level+change x gate on/off);
the manager rule is fixed in the signal module docstring before any result is read.

## Batch 0 — shared contract (1 step)
### B0 Schema and interfaces
Files: tradingbot/utils/db.py, tradingbot/utils/inst13f_signal.py (stub with signatures + docstring), tests/test_inst13f_models.py (new)
- Tables: `inst13f_filings` (accession unique, cik, filer_name, period_end, filed_at = SEC acceptance time, is_amendment),
  `inst13f_holdings` (accession, cusip, ticker nullable, value_usd, shares, put_call nullable; value normalised to dollars),
  `cusip_map` (cusip pk, ticker, source, mapped_at).
- Interfaces (stub raises NotImplementedError): `eligible_filers(filings, holdings, as_of, params) -> set[cik]`,
  `conviction_scores(filings, holdings, as_of, params) -> pd.Series[ticker]`, both strictly point-in-time on `filed_at <= as_of`.
- Test: models create on an empty SQLite DB and the unique accession constraint rejects a duplicate.

## Batch 1 — parallel (3 steps, disjoint files)
### B1a SEC 13F bulk ingest
Files: tradingbot/utils/inst13f_ingest.py (new), scripts/inst13f_backfill.py (new), tests/test_inst13f_ingest.py (new)
- Parse the SEC Form 13F structured data sets (SUBMISSION, COVERPAGE, INFOTABLE tab files from the quarterly zips).
- Rules: key by acceptance date not period end; keep the latest amendment per (cik, period) but never before its own
  acceptance date; drop option rows (`put_call` set); handle the 2023 value-unit change (thousands to dollars) by
  filing date; skip non-equity (no CUSIP match later is fine here, only flag).
- Backfill script: resumable per quarter, 5 requests/second, generic User-Agent, writes to the configured DB or a scratch SQLite.
- Tests (inline tab-separated fixtures): value units normalised on both sides of the 2023 change; amendment replaces
  the original only from its own filing date; option rows dropped.

### B1b CUSIP to ticker mapping
Files: tradingbot/utils/cusip_map.py (new), tests/test_cusip_map.py (new)
- Resolve CUSIP -> ticker via the OpenFIGI mapping API in batches, rate-limited, cached in `cusip_map`; unresolved CUSIPs
  stay NULL and are reported with their dollar weight so coverage of the S&P 500 can be measured.
- Tests (HTTP mocked): batching and cache hit makes no second call; unresolved CUSIP is stored as NULL, not retried every run.

### B1c Manager eligibility and conviction score
Files: tradingbot/utils/inst13f_signal.py (implement B0 stubs), tests/test_inst13f_signal.py (new)
- Eligible filer on date `as_of`: >= 8 consecutive quarters filed, 10-60 positions, top-10 weight >= 40%,
  trailing turnover < 25%/quarter. Score per ticker = number of eligible managers holding it in their top 10;
  variant adds net new buys / increases in the latest filing.
- Tests: a filing accepted after `as_of` is invisible (lag guard); concentration and turnover filters include/exclude
  correctly on a synthetic panel; score is zero for names no eligible manager holds.

## Batch 2 — study (1 step; needs B1 merged; runs the backfill)
### B2 Walk-forward study with kill tests
Files: scripts/onetime_inst13f_study.py (new), docs/backtests/inst13f-2026-11.md (new)
- Fit 2013-2019, judge 2020-2026. Benchmarks: QQQ, EW eligible universe, plain 12-1 momentum on the same names,
  current InstitutionalFlowBot. Book: top N equal weight, 7% name cap, 35% sector cap, quarterly rebalance two days after
  the filing deadline, QQQ < SMA200 -> cash (SHV), 5 and 20 bp costs.
- Kill tests, all reported: (1) lag test: alpha with holdings known at quarter end vs day 45 (if it only exists at quarter end,
  untradeable); (2) placebo: 2,000 random manager sets of similar size, p < 0.05; (3) materiality: beats EW universe by >= 2%/yr in H2.
- Reports CUSIP coverage of the S&P 500 and the count of eligible managers per quarter first; a coverage < 85% or fewer than
  15 managers in any quarter stops the study with a "data insufficient" verdict.
- Tests (tests/test_inst13f_study.py, new, add to Files): no look-ahead in the rebalance date; placebo draws match the real set size.
- Verdict line: `PASS` / `FAIL` / `DATA INSUFFICIENT` per the bar.

## Batch 3 — gate (me)
Read the study. FAIL or DATA INSUFFICIENT: stop Part B, report why. PASS: continue.

## Batch 4 — bot and refresh job, parallel (2 steps)
### B3a Bot
Files: tradingbot/inst13fconvictionbot.py (new), tests/test_inst13f_conviction_bot.py (new)
- `targetWeights` from `conviction_scores` at the latest filing deadline, caps as in the study, gate to cash, default params
  = the study's pre-registered winner. Quarterly rebalance; other days return None (hold), same in backtest.
- Tests: weights sum <= 1, name/sector caps hold; gate off returns cash; non-rebalance day returns None.

### B3b Quarterly refresh job
Files: tradingbot/inst13fscan.py (new), tests/test_inst13fscan.py (new)
- Entry point that ingests newly filed 13Fs since the last stored `filed_at`, maps new CUSIPs, and writes a heartbeat row
  like the sc13d scanner so a quiet quarter is not mistaken for a dead job. Idempotent on accession.
- Tests: second run inserts nothing; heartbeat is refreshed on a run with no new filings.

## Batch 5 — config (1 step, owns values.yaml)
### B4 Schedules and docs
Files: helm/tradingbots/values.yaml, docs/guides/live-trading.md, TODOS.md
- Add the bot (paper, live from day one; daily run is cheap, rebalances only in the quarterly windows) and the refresh
  CronJob (daily during Feb/May/Aug/Nov filing windows is enough, daily all year is simpler). No copier lists it yet:
  TODOS item "add to retail botWeights after 2 live rebalances and `alphareport` verdict".

---
## Final review (me)
Run the new tests once; `git diff pre-agy` (or `agy/work` base) against this plan; confirm no config or test was weakened
and no personal details appear in any SEC/OpenFIGI request. Then merge `agy/workinstitution` into main only after you approve.

## Risks I am carrying
- Part A's second half of the sample is short and PLTR/SNDK/GEV-type names are excluded from the backtest but held live.
- 13F data is long-only US equity, 45-135 days stale; hedge-fund favourites sell off together.
- Roughly 54 quarterly rebalances: the daily t-stat overstates the number of independent bets.
- Demo accounts build no real track record; A4 only adds the TODO, it cannot open accounts.
