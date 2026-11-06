# PLAN: does existing event data hold an edge vs QQQ?

Goal: with data already in the prod DB, run quick event studies and judge each by
alpha vs QQQ and t-stat (CLAUDE.md bar: t >= 2, positive in BOTH halves, beta < 0.5).
No live logger, no new infra. Output is evidence for or against building event bots.

## What the data audit found (2026-11-06)

| Source | Size | Verdict |
|---|---|---|
| `telegram_messages` ch `-1002202241417` | 1.33M msgs | 99% pump.fun memecoin dev-sell spam (SOL/DEV/PUMP). Not tradeable by any broker here. **Excluded.** |
| `telegram_messages` ch `-1001998690333` (Signals Bank) | 8.5k msgs, 1.4k with symbol, since 2026-03 | Real signals (FX/gold/index/crypto). Study **E3**. |
| `stock_news` | 11.5k headlines, 84 symbols, 2024-09..2026-09 | Study **E2**. |
| `macro_events` | FOMC/CPI/NFP, 2000..2027 | Study **E1**. |
| `historic_data` | 1d for 218 symbols from 2023-11; 1m only ~1.2k bars/symbol (sparse) | Use **daily bars** (yfinance for longer history). No intraday claims. |
| `stock_earnings` | -- | PEAD already tested and rejected (docs/backtests/pead-2026-09.md). **Skip.** |

Honest prior: expect most of these to fail t >= 2. A clean "no edge" is a valid result.

## Contract (Batch 0)

`tradingbot/utils/event_study.py`, pure functions, no DB or network:

```python
def forward_returns(prices: pd.Series, event_ts: pd.Timestamp, horizons_days: Sequence[int]) -> dict[int, float]
    # entry = first daily close STRICTLY AFTER the event timestamp's session (no look-ahead);
    # return from entry close to close `h` sessions later; NaN if not enough bars.
def abnormal_returns(events: pd.DataFrame, prices: dict[str, pd.Series], bench: pd.Series, horizons: Sequence[int]) -> pd.DataFrame
    # events cols: ts, symbol, label(optional). Output: one row per event/horizon with raw, bench, abnormal (= raw - beta*bench, beta from 250d pre-event, fallback 1.0).
def event_strategy_returns(signals: pd.DataFrame, prices: dict[str, pd.Series], hold_days: int) -> pd.Series
    # daily return series of an equal-weight book that holds each signalled symbol for hold_days after entry, cash otherwise.
def judge(strategy_returns: pd.Series, bench_returns: pd.Series) -> dict
    # uses tradingbot.utils.alpha_report.alpha_stats; also returns alpha/t for first and second half of the sample and a bool `passes` (t>=2, both halves alpha>0, beta<0.5). None stats -> passes False.
```

## Batch 0 (1 step)

### S0 -- shared helpers
Files: `tradingbot/utils/event_study.py`, `tests/test_event_study.py`
Acceptance:
- Functions above implemented as specified.
- Tests: (1) entry for an event after the close uses the NEXT session, not the same day (look-ahead guard); (2) `judge` returns `passes=False` when halves disagree in sign; (3) `event_strategy_returns` is flat/zero on days with no open position.

## Batch 1 (3 parallel steps, disjoint files)

All three scripts: read from the DB via `get_db_session()` (POSTGRES_URI is provided in env), load prices via the existing `getYFData`-style/yfinance path used by other `scripts/onetime_*` files, benchmark = QQQ over the same window, print a table of alpha/t/beta/n per variant and write `docs/backtests/<name>-2026-11.md` with the numbers and a one-line verdict (`edge` / `unproven` / `no edge`). Pre-register the variant grid in the script header BEFORE looking at results; report all variants, not just the best. Split the sample in half by event date for every variant.

### E1 -- macro calendar drift
Files: `scripts/onetime_event_macro_study.py`, `docs/backtests/event-macro-2026-11.md`, `tests/test_event_macro_study.py`
Study: for FOMC / CPI / NFP in `macro_events` (past dates only), SPY/QQQ/TLT/GLD/UUP abnormal return for windows [-1,0], [0,+1], [0,+2] sessions. Strategy variants: hold X only around the event window, else cash. Report alpha/t/beta per kind x asset x window (cap at 40 variants).
Test: only past events are used (future-dated rows are excluded); window arithmetic uses sessions not calendar days.

### E2 -- stock_news headline drift
Files: `scripts/onetime_event_news_study.py`, `docs/backtests/event-news-2026-11.md`, `tests/test_event_news_study.py`
Study: score each `stock_news` headline with a small finance lexicon (positive/negative word lists, no LLM, deterministic; keep lexicon in the script). Entry at the next session close after `published_at` (use `forward_returns`). Compare long-positive, short-negative-as-avoid (long-only book: skip, only measure abnormal returns for negatives), and top-vs-bottom tercile abnormal return at +1/+3/+5 days. Dedupe identical titles per symbol per day. Report n per bucket; buckets with n < 30 flagged "too few".
Test: lexicon scoring on 4 fixed headlines (incl. negation like "not beat"), and dedupe.

### E3 -- Signals Bank follow-the-signal
Files: `scripts/onetime_event_signals_study.py`, `docs/backtests/event-signals-2026-11.md`, `tests/test_event_signals_study.py`
Study: channel `-1001998690333`, rows with non-null `symbol` and `summary`; map symbol to a yfinance ticker with the same mapping idea as `telegramsignalsbankbot.py` (reuse its helpers if importable without side effects, else a small dict in the script). Direction from summary text via simple keyword rule (BUY/LONG vs SELL/SHORT). Measure signed forward return at +1/+3/+5 sessions, raw and abnormal vs QQQ, long-only subset separately (the framework cannot short). Report the count of signals that could be mapped at all.
Test: direction parser on 4 fixed summaries; unmappable symbols are dropped, not defaulted.

## After Batch 1 (me, not agy)
Write `docs/backtests/event-edge-summary-2026-11.md`: one table of every variant that was run, the pass/fail from `judge`, and a recommendation on whether any class deserves a live `world_events` recorder or a `targetWeights` bot. Delete this PLAN.md.

## Out of scope
The `eventlogger` Deployment and `world_events` tables stay as they are (still disabled). Nothing here changes Helm, bots, or live behaviour.
