# PLAN: BuybackDriftBot (paper) + 8-K buyback scanner (2026-11-07)

Evidence: `docs/backtests/event-buyback-2026-11.md` (10-session hold after an S&P 100 buyback authorization,
+10%/yr alpha, t 2.56, placebo p 0.006-0.02; verdict `unproven`). Goal: collect a live forward record, paper only,
judged by `alphareport`, not by return. Both new pieces ship ENABLED (a scanner that is built but off looks like it is running).

Rules: pure logic in `tradingbot/utils/`, wrappers in `tradingbot/`. No look-ahead: an event is tradeable from the first
session close strictly AFTER its acceptance time. Generic EDGAR User-Agent `tradingbot-research` (no email), max 5 req/s.
Tests: 3-5 pytest cases per step on pure functions, no network. Reuse the study's logic, do not change the study scripts.

## Batch 0 - shared contract (1 step)

### C0 - table + pure rules
Files: `tradingbot/utils/db.py` (add model only), `tradingbot/utils/buyback_rules.py`, `tests/test_buyback_rules.py`
- `BuybackEvent` table `buyback_events`: `id` PK, `symbol` str idx, `accession` str UNIQUE, `filed_at` datetime (UTC, acceptance time),
  `source_url` str, `snippet` str (<= 300 chars around the match), `created_at` default now. New nullable columns only
  (`_sync_missing_columns` creates the table on `init_db`).
- `buyback_rules.py`, all pure:
  - `matches_buyback(text) -> bool` (port the regex from `scripts/onetime_event_buyback_study.py::matches_buyback_regex`, plus the 8-K Item 8.01/7.01 check helper `has_item_801_or_701(items)`).
  - `entry_session(filed_at_utc) -> date`: first NYSE session whose close is strictly after `filed_at_utc` (use `utils/market_calendar.session_close_utc`, `is_session`, `next_session`).
  - `active_symbols(events, bar_day, hold_sessions=10) -> list[str]`: symbols whose position is held at `bar_day`'s close = entry_session <= bar_day < entry_session + hold sessions (NYSE sessions), deduped.
  - `dedupe_recent(events, window_days=30)`: drop an event if the same symbol had one in the previous 30 days.
- Tests: after-close filing enters next session; Friday-evening filing enters Monday; hold window is exactly 10 sessions across a holiday; 30-day dedupe; regex hits "board authorized repurchase of up to $5 billion" and rejects "no repurchase program".

## Batch 1 - two independent steps

### P1 - scanner (CronJob wrapper + impl)
Files: `tradingbot/buybackscan.py`, `tradingbot/utils/buyback_scan.py`, `tests/test_buyback_scan.py`
- `utils/buyback_scan.py`: `scan(symbols, since_days=5, now=None) -> list[BuybackEvent rows inserted]`. For each S&P 100 symbol (`utils/universes.SP100`),
  fetch EDGAR `submissions/CIK##########.json` (ticker->CIK via `company_tickers.json`, cached in memory per run), keep 8-Ks with Item 8.01/7.01 filed in the last
  `since_days`, fetch the primary document, run `matches_buyback`, skip accessions already in `buyback_events`, insert new rows (idempotent via the UNIQUE accession; on conflict do nothing). Token-bucket at 5 req/s, 15 s timeout, one retry. A single failing symbol is logged and skipped, the run exits non-zero if more than 20% of symbols failed.
- `tradingbot/buybackscan.py`: wrapper only (logging, `init_db()`, call `scan(SP100)`, `sys.exit` code). Add `--backfill-days N`.
- Tests (mock HTTP): parses a saved submissions JSON fixture, inserts a matching 8-K once and not twice, skips non-matching text, tolerates one symbol raising.

### P2 - the bot
Files: `tradingbot/buybackdriftbot.py`, `tests/test_buyback_drift_bot.py`
- `BuybackDriftBot(Bot)`, name `BuybackDriftBot`, `targetWeights` pattern like `tradingbot/macroeventsleevebot.py`: tickers = `SP100` (+ none else), `interval="1d"`, `period="1mo"`, `MIN_UNIVERSE_COVERAGE = 0.95`.
- `targetWeights(rows)`: read events from the last 40 days (`buyback_events`), compute `active_symbols(events, bar_day)`, equal weight among active symbols capped at 0.5 each (so one name is never the whole book; the study held 100% of one name, document this deviation in the docstring), `{}` when none. Raise if the `buyback_events` table is empty AND the scanner has never run (`MAX(created_at)` older than 10 days or no rows) so a dead scanner is loud, not "no events today".
- Tests: no events -> {}; one active event -> {sym: 0.5}; three actives -> 1/3 each; event older than 10 sessions drops out; stale scanner raises.

## Batch 2 - wiring + docs (1 step; owns the shared files)

### W - helm + docs
Files: `helm/tradingbots/values.yaml`, `helm/tradingbots/templates/cronjob-buybackscan.yaml`, `AGENTS.md`, `docs/backtests/event-buyback-2026-11.md` (one line pointing at the bot)
- values.yaml: `bots:` entry `buyback-drift-bot` (name pattern as existing, schedule `10 21 * * 1-5`, after the 21:00 scan) and a `buybackScan:` block (`enabled: true`, schedule `*/30 13-21 * * 1-5` UTC, i.e. every 30 min in US hours; second run `0 21`).
- Template modelled on `cronjob-splitsnapshot.yaml` / `cronjob-telegram-monitor.yaml`, gated on `buybackScan.enabled`, command `python -m tradingbot.buybackscan`.
- AGENTS.md: a `buyback_events` row in the table list, `BuybackDriftBot` row in the strategies table (paper, unproven), one paragraph under the event-logger section.
- Acceptance: `helm template` renders without error (if helm is installed; otherwise YAML parses), ruff format check passes on all new python files.

## Final review (me)
Run all new tests once, run `ruff check` / `ruff format --check` on new files, read the diff against this plan, confirm no existing config or
test changed except `values.yaml`/`AGENTS.md`/`db.py` additions, smoke-run `python -m tradingbot.buybackscan --backfill-days 5` against the prod DB (port-forward),
confirm rows appear and the bot's `targetWeights` returns a sane dict, then merge to main.

## Risks to approve
- Writes a new table in the PROD database (`buyback_events`, created by `init_db` on first run) and adds a CronJob that polls SEC EDGAR.
- The scanner has only existed for a day: expect zero events for days or weeks, and a quiet bot. `alphareport` will say `too short` for a long time.
- I will not push or deploy; main merge only. Deploy happens through your normal CI when you push.
