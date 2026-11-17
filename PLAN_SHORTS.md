# Plan: short selling on the paper book, modelled on IBKR

**Reuse the options code for the mechanics, not for the margin.** The options work already gave the book signed quantities, signed valuation, average-cost entry tracking through zero, and a one-transaction "check margin on the resulting book" trade path. Its margin model is the wrong one for shorts: `margin_requirement` prices worst-case loss at expiry, so naked short stock is infinite by design ([options.py:1033](tradingbot/utils/options.py#L1033)). Stock shorts need a Reg T style model beside it.

I found no write-up of the earlier attempt, so the guards below come from what the code shows: the Hyperliquid stray short that doubled every run, the C2 double-submit that went net short 34 QQQ, and the long-only clamps added since.

## Design rules

1. **A short is never a side effect.** `sell()` keeps its clamp at zero. Only a new `short()` / `cover()` pair and signed target weights can cross zero.
2. **Opt-in per bot.** `ALLOW_SHORT = False` by default, and the LLM tool surface never gets it. After every write, a negative stock holding on a non-opted bot raises and rolls back.
3. **Decision `-1` stays "exit".** Redefining it as "short" would flip every existing bot. Shorts come only from negative `targetWeights`.
4. **One transaction per rebalance.** Compute signed share deltas for the whole book and check margin once on the result. Reductions always pass, so a position can always be closed.
5. **Fail closed on a missing price.** Valuing a short at 0 inflates equity by the whole proceeds.

## IBKR rules the paper book should mirror

Confirm the numbers against IB's current margin and fee pages before coding; these are from memory.

| IB behaviour | Paper model |
|---|---|
| Reg T initial margin 50% long and short | Opens require `equity ≥ 0.5·long + 0.5·short` |
| Short maintenance about 30%, long 25% | Checked every run; breach force-covers the largest short first |
| Proceeds held as collateral, no interest at small balances | Proceeds sit in `USD` but are not spendable and earn 0% |
| Daily borrow fee on short market value | Daily accrual, flat default rate plus per-symbol override |
| Short pays dividends in lieu | Debit on ex-date |
| No fractional shorts | Whole shares only on the short side |
| Not everything is shortable | A `SHORTABLE` allowlist of liquid ETFs and large caps |

On top of Reg T, add a per-bot `MAX_GROSS` defaulting to 1.0. A long-only bot then behaves exactly as today, because spendable cash reduces to `cash`. A beta-hedged sleeve (100 long, −100 QQQ) sets it to 2.0.

## Phases

**Phase 0: pin current behaviour.** Golden tests show every existing bot's backtest and rebalance output is identical before and after. This is the regression gate for all later phases.

**Phase 1: book core.**
- New pure `utils/margin.py` (equity, long/short value, initial and maintenance checks, spendable cash), shared by live paper and backtest.
- Generalise `trade_option_legs` into a signed `trade_legs`; options and shorts both call it. Structured underlyings keep the options margin; unstructured short shares use Reg T.
- `rebalance_portfolio`: drop the refusal on negative holdings ([portfolio_manager.py:832](tradingbot/utils/portfolio_manager.py#L832)) and the `qty > 0` valuation, then route through `trade_legs`.
- Weights: the signed sum including USD still equals 1, so `require_normalized` holds. Add a gross check, and remove the `max(0.0, weight)` clamp only for opted-in bots ([botclass.py:1204](tradingbot/utils/botclass.py#L1204)).
- Add a nullable `action` column on `trades` (BUY / SELL / SHORT / COVER) so the log is readable without replaying positions.

**Phase 2: carry and corporate actions.**
- `apply_carry(bot)` runs on the same hook as `apply_splits`: borrow fee per calendar day and dividends in lieu.
- A new `cash_adjustments` table keyed on (bot, date, symbol, kind) makes the accrual idempotent.
- Maintenance liquidation is logged as a trade with its reason.
- Add a split test on a short holding; the maths looks signed-safe but is untested.
- `calculate_portfolio_worth` currently skips a holding with no price ([portfolio_utils.py](tradingbot/utils/portfolio_utils.py)); for a short it must raise.

**Phase 3: backtest parity.** The loop in [backtest.py](tradingbot/utils/backtest.py) skips `holding <= 0` and caps buys at cash. Make both phases symmetric using the same `margin.py`, and add per-bar borrow cost and liquidation. Alpha metrics need no change: a short hedge shows up as lower beta.

**Phase 4: first live paper consumer.** I recommend the beta-hedged buyback sleeve from TODOS item 2, deployed as a running paper CronJob. The copier keeps dropping negative weights, with an honest log line; today it calls them "part of an option structure" ([copier.py:208](tradingbot/livetrade/copier.py#L208)).

**Phase 5: IB copier, later.**
- Add a `supports_short` flag per broker and four order intents.
- Covers are never scaled by the buy-cash clamp.
- `_park_idle_cash` uses gross exposure, not net; otherwise short proceeds get parked in SHV as leverage.
- Check shortable shares before sending a short order.

My notes say the IB session was still failing in August, so this phase waits on that.

## Decisions for you

- **Borrow fee default:** I would use a flat 1%/yr, which is pessimistic against IB's rate for easy-to-borrow names, so paper does not flatter shorts.
- **Dividends on longs:** the paper book credits none today. Charging shorts only makes shorts look slightly worse than reality, which I think is acceptable; crediting longs too is a separate change.
- **`MAX_GROSS` default of 1.0:** safer, but the hedged sleeve must raise it explicitly.

`CLAUDE.md` imports `AGENTS.md`, which is not on this branch, so the "PortfolioWorth Model" rules it points to are not being loaded.