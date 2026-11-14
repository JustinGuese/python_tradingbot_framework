# PLAN — short selling on the paper book, IBKR-style (gembuild)

Source design: PLAN_SHORTS.md. Rules for every step: touch only listed files; max 2 test rounds; never weaken
configs/tests; ruff-format any python snippet in .md; shipped behaviour is LIVE (no new dry-run defaults).
`ALLOW_SHORT=False` is a guard that makes existing bots unchanged, not a dry-run: the new sleeve (S2c) turns it on.

## Shared contract (all batches rely on these names)
- `utils/margin.py` pure fns (no I/O): `book_values(portfolio, prices) -> (long, short, equity)`,
  `initial_ok(portfolio, prices, max_gross) -> bool` (Reg T: equity >= 0.5*long + 0.5*short AND (long+short)/equity <= max_gross),
  `maintenance_breach(portfolio, prices) -> bool` (equity < 0.25*long + 0.30*short),
  `spendable_cash(portfolio, prices) -> float` (cash minus short collateral, floored at 0; equals `USD` for a long-only book),
  `cover_order(portfolio, prices) -> str | None` (largest short by value).
  Stock shorts only; option underlyings keep `options.margin_requirement`.
- `ShortConfig` in `utils/config.py` (env): `SHORT_BORROW_RATE_PCT=1.0` (per year), `SHORT_MAINT_PCT=0.30`, `SHORT_INIT_PCT=0.50`;
  `SHORT_BORROW_OVERRIDES` (json symbol->rate). `SHORTABLE` default allowlist (liquid ETFs + S&P 100) in the same file.
- Bot class attrs (defined in S1b): `ALLOW_SHORT=False`, `MAX_GROSS=1.0`, `SHORTABLE=None` (None = config default).
- `PortfolioManager` (S1a): `short(symbol, quantity_usd)`, `cover(symbol, quantity_usd=-1)`,
  `rebalance_portfolio(target, only_over_50_usd=False, *, allow_short=False, max_gross=1.0, shortable=None)`; targets may be negative only if `allow_short`.
- `trades.action` nullable str (BUY/SELL/SHORT/COVER) via `log_trade(..., action=None)`; table `cash_adjustments(bot_name, date, symbol, kind, amount)` PK (bot_name, date, symbol, kind).
- Broker (S2): `LiveBroker.supports_short: bool = False`; order sides `"BUY"|"SELL"|"SHORT"|"COVER"`.

---
## Batch 0 — contract (1 step)
### S0 margin core + schema
Files: tradingbot/utils/margin.py (new), tradingbot/utils/config.py, tradingbot/utils/db.py, tradingbot/utils/bot_repository.py, tests/test_margin.py (new)
- Implement the contract above; add `Trade.action`, `CashAdjustment` model (follow the repo's existing schema-migration mechanism; grep first), `log_trade(action=)`.
- Tests: long-only book => spendable == USD and no breach (zero behaviour change); 100 long / 100 short at max_gross 2.0 passes, at 1.0 fails;
  maintenance breach triggers and `cover_order` picks the largest short; missing price raises (fail closed).

---
## Batch 1 — parallel (4 steps, disjoint files)
### S1a PortfolioManager
Files: tradingbot/utils/portfolio_manager.py, tests/test_portfolio_manager_shorts.py (new)
- Generalise `trade_option_legs` into signed `trade_legs` (options wrapper keeps its API); add `short`/`cover`; `sell()` stays clamped at zero.
- `rebalance_portfolio`: remove the refusal on negative holdings and the `qty > 0` valuation; with `allow_short` compute signed share deltas for the whole
  book, check `margin.initial_ok` once on the resulting book, reductions always pass; whole shares on the short side; refuse symbols outside `shortable`;
  missing price on a short raises. Log `action` on every trade. Without `allow_short` behaviour is byte-identical (existing tests stay green).
- Tests: sell never goes negative; short then cover round-trips cash exactly; rebalance 100 long / -100 QQQ passes at max_gross 2.0 and is refused whole at 1.0;
  non-shortable symbol refused; naked option underlying still uses options margin.

### S1b Bot class + weights
Files: tradingbot/utils/botclass.py, tradingbot/utils/weights.py, tests/test_botclass_short_weights.py (new)
- Add `ALLOW_SHORT/MAX_GROSS/SHORTABLE`; `short()/cover()` wrappers on Bot; never exposed to the LLM tools.
- `_coerce_target_weights`: keep the `max(0.0, w)` clamp unless `ALLOW_SHORT`; then allow negatives, signed sum incl. USD == 1, gross <= MAX_GROSS (scale down + ERROR log if exceeded).
  `weights.py`: signed-aware helpers (`gross_weight`, `net_weight`) without changing `positive_weight_sum` semantics for existing callers.
  Decision `-1` stays "exit" everywhere. Pass the new kwargs to `rebalance_portfolio` only when ALLOW_SHORT (tests mock PortfolioManager).
- Tests: default bot clamps negatives exactly as today; opted-in bot keeps -0.5 and USD residual normalises; gross above MAX_GROSS is scaled; -1 decision still exits.

### S1c Backtest parity
Files: tradingbot/utils/backtest.py, tests/test_backtest_shorts.py (new)
- Phase 1/2 loops symmetric for shorts using `margin.py`; per-bar borrow cost (`SHORT_BORROW_RATE_PCT`/bars per year), force-cover on maintenance breach, shortable check,
  slippage/commission on both sides. Read `ALLOW_SHORT/MAX_GROSS` via getattr defaults. Long-only path unchanged (tests/test_backtest_parity.py stays green).
- Tests: short on a falling series profits net of borrow; rising series triggers force-cover not negative equity; long-only result identical to before; alpha metrics report lower beta for a hedged book.

### S1d Carry, worth, splits
Files: tradingbot/utils/short_carry.py (new), tradingbot/utils/portfolio_utils.py, tradingbot/calculate_portfolio_worth.py, tests/test_short_carry.py (new), tests/test_splits.py
- `apply_carry(bot_name, today)`: daily borrow fee on short market value (calendar days since last accrual), dividends in lieu on ex-date (shorts only); idempotent via
  `cash_adjustments`; maintenance liquidation logged with action COVER and a reason. Called from calculate_portfolio_worth before valuing.
- `calculate_portfolio_worth`: a short with no price raises instead of being skipped (long behaviour unchanged).
- tests/test_splits.py: add a 2:1 split on a short holding (qty doubles, still negative, cash untouched).
- Tests: accrual twice on the same day charges once; weekend gap charges 3 days; dividend debit once; missing short price raises.

---
## Batch 2 — parallel (3 steps, disjoint files)
### S2a Copier
Files: tradingbot/livetrade/copier.py, tradingbot/livetrade/broker.py, tests/test_livetrade_copier_shorts.py (new)
- `supports_short=False` on LiveBroker. `_calculate_target_weights` keeps negative stock weights when the broker supports shorts, otherwise drops them with an
  honest log line ("short dropped", not "option structure"). `_park_idle_cash` uses gross not net. Orders: SHORT/COVER intents; covers never scaled by the buy-cash clamp;
  a positions read showing an unexpected short on a non-short broker aborts that symbol (the Hyperliquid/C2 lesson), never doubles it.
- Tests: short weight dropped for a non-short broker; supported broker gets SHORT then COVER on flip; cash clamp never scales a cover; stray short is covered once, not doubled.

### S2b IB adapter
Files: tradingbot/livetrade/interactive_brokers.py, tests/test_livetrade_ib_short.py (new)
- `supports_short=True`; map SHORT->SELL / COVER->BUY with the held contract; skip (log, don't raise) when shares are not available (`reqMktData` shortable tick > 2.5
  rule or tick missing => skip); whole shares only. Unit tests with fakes; no live IB calls (IB session known broken since Aug).
- Tests: SHORT builds a SELL on an empty position; COVER buys at most the open short; unshortable tick skips the order; fractional qty floors.

### S2c First consumer: hedged buyback sleeve (paper, live ON)
Files: tradingbot/buybackhedgedbot.py (new), helm/ values file where buybackdriftbot's CronJob is defined (add one entry, not suspended), tests/test_buybackhedgedbot.py (new)
- Reuse `buybackdriftbot` signal; long book plus a QQQ short sized to beta ~0 (ALLOW_SHORT=True, MAX_GROSS=2.0, SHORTABLE includes QQQ). Mirror the existing bot's helm wiring exactly.
- Tests: target weights sum to 1 signed, QQQ weight negative, gross <= 2.0; flat when no signals (no stranded short).

---
## Batch 3 — docs (2 steps, disjoint files)
### S3a Architecture doc + nav
Files: docs/architecture/short-selling.md (new), mkdocs.yml
- Design rules, IBKR mapping table, margin math, borrow/dividend model, backtest parity, copier behaviour, how to opt a bot in, known limits (no hard-to-borrow, no locates, paper only on IB until its session works). Add to nav.

### S3b Existing docs
Files: docs/architecture/database-models.md, docs/architecture/bot-class-system.md, docs/api/portfolio-manager.md, README.md
- Document `trades.action`, `cash_adjustments`, `ALLOW_SHORT/MAX_GROSS/SHORTABLE`, `short/cover`, signed rebalance; README: replace "the framework is long-only" statements, add the hedged sleeve to the live list.

---
## Final review (me, not agy)
Run the new tests once; `git diff pre-agy` vs this plan; verify golden parity (existing portfolio_manager/backtest/splits/copier tests unchanged and green); `ruff format --check` on touched files incl. .md snippets.
