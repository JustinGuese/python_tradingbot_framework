# Plan: Make IB livetrade copier cron-friendly

## Context

The copier was just verified end-to-end against an IB paper account during pre-market hours. It connects, sizes orders correctly, and submits them — but the run revealed four issues that make it unusable as a scheduled cron job:

1. **Per-order 30s blocking wait** ([interactive_brokers.py:131-138](tradingbot/livetrade/interactive_brokers.py#L131-L138)). With ~40 sells, that's ~20 minutes of dead time during which IB just queues the order anyway. Outside RTH the wait is purely wasted — the order can't possibly fill until next open.
2. **Misleading "Executed" log** ([copier.py:169](tradingbot/livetrade/copier.py#L169)). Logs `Executed SELL …` purely on no-exception, so `PreSubmitted` (queued for next open) and `Cancelled` (e.g. fractional rejected) both look like success in the run output.
3. **Fractional shares rejected** by IB API (error 10243): `VSNT 2.16 shares: Fractional-sized order cannot be placed via API`. Anything that came in via DRIP or corporate action will fail.
4. **No de-duplication across cron runs.** Cron is scheduled post-close ([cronjob-livetrade-interactivebrokers.yaml schedule `20 21 * * 1-5`](helm/tradingbots/templates/cronjob-livetrade-interactivebrokers.yaml)); orders queue as `PreSubmitted` until next open. If the next cron fires (e.g. weekend → Monday) before yesterday's queued orders have filled, the diff still sees the position and submits a *second* order. Duplicates.

The intended cron model is: post-close run computes target diff, submits market orders, IB queues them, they fill at next open. The code needs to match that model.

## Approach

Adopt **submit-and-walk-away with idempotent re-submission**:

- IB's `place_order` fires the order and returns immediately. No 30s wait.
- Before each sync, cancel any open orders we previously submitted (from prior runs that haven't filled). Then re-issue against the latest target. This makes each cron run self-correcting — yesterday's queued sell of 10 shares is replaced by today's queued sell of 12 if the target changed, without doubling up.
- Stocks must be integer-quantity; fractional residuals are rounded down with a warning. Crypto/forex keep their existing precision.
- Logs say what actually happened: `Submitted` (with status), not a fake `Executed`.

This keeps the cron container exit time bounded (~seconds, not minutes) and makes successive runs idempotent.

## Files to modify

### 1. [tradingbot/livetrade/broker.py](tradingbot/livetrade/broker.py)

Add a non-abstract default-no-op method to `LiveBroker`:

```python
def cancel_open_orders(self) -> int:
    """Cancel any orders this broker session has previously submitted that
    are still open. Override per-broker. Returns count cancelled."""
    return 0
```

C2 inherits the no-op (REST: orders submitted are immediately accepted or rejected; no working-order concept on our side).

### 2. [tradingbot/livetrade/interactive_brokers.py](tradingbot/livetrade/interactive_brokers.py)

**Replace** `place_order` ([interactive_brokers.py:119-146](tradingbot/livetrade/interactive_brokers.py#L119-L146)):

- Remove the `while not trade.isDone()` loop and 30s timeout.
- For `STK` contracts, round quantity to `int(quantity)` (floor, not banker's-round). If 0, log warning and return without submitting.
- Submit via `ib.placeOrder`. Single short `ib.sleep(0.3)` to let the first ack come back, then log final status (`PendingSubmit`/`PreSubmitted`/`Submitted`/`Filled`/`Cancelled`).
- No exception on `Cancelled` — IB cancellation includes valid rejections (e.g. fractional 10243); the next run will re-evaluate.

**Add** `cancel_open_orders` override:

```python
def cancel_open_orders(self) -> int:
    self.connect(readonly=False)
    open_trades = [t for t in self.ib.openTrades() if not t.isDone()]
    for t in open_trades:
        self.ib.cancelOrder(t.order)
    if open_trades:
        self.ib.sleep(1)  # let cancellations propagate
    return len(open_trades)
```

Note: `ib.openTrades()` returns trades visible to *this* clientId only (default behavior unless a master client ID is configured), so this won't touch orders the user placed manually with a different ID. Safe.

### 3. [tradingbot/livetrade/copier.py](tradingbot/livetrade/copier.py)

In `sync()`, after computing target weights and before calling `_execute_orders`:

```python
cancelled = self.broker.cancel_open_orders()
if cancelled:
    logger.info(f"Cancelled {cancelled} stale open orders before sync")
```

In `_execute_orders` ([copier.py:158-183](tradingbot/livetrade/copier.py)):

- Change the per-order log from `Executed {side}` to `Submitted {side}` to match reality. The broker logs the actual order status separately.

The existing `LIVETRADE_SETTLE_DELAY_SECONDS` between sells and buys stays — it's only meaningful intraday, and the dry-run/empty-batch guard already makes it a no-op when there are no sells.

### 4. [tests/test_livetrade_ib.py](tests/test_livetrade_ib.py)

Update `test_ib_place_order`:

- The test currently mocks `trade.isDone() == True` and expects the loop to exit. With the loop gone, the test needs to be relaxed — just assert `ib.placeOrder` was called with the right contract+order, drop the orderStatus assertions about `Filled`.

Add `test_ib_place_order_floors_fractional_stock`:

- Quantity `2.16`, `STK` contract → `placeOrder` called with `totalQuantity == 2`. Quantity `0.4` → `placeOrder` NOT called (warned and skipped).

Add `test_ib_cancel_open_orders`:

- Mock `ib.openTrades()` to return two non-done trades; assert `cancelOrder` called twice and method returns `2`.

Existing 8 tests stay valid.

## Files NOT changing

- [tradingbot/livetrade/collective2.py](tradingbot/livetrade/collective2.py) — REST broker, no fractional / no working-order concept. Inherits the no-op `cancel_open_orders`.
- [tradingbot/livetrade_interactive_brokers.py](tradingbot/livetrade_interactive_brokers.py) entry point — copier orchestrates everything.
- Helm cronjobs / values — same env vars, same schedule. The fix is purely behavioral.

## Risks

1. **`ib.openTrades()` scope** — if the user runs Gateway with a Master Client ID configured, it will see *every* order in the account, including manual ones. Document this in [docs/guides/live-trading.md](docs/guides/live-trading.md) under the IB section: "Run the cron and any other scripts with distinct clientIds and **without** Master Client ID set, otherwise `cancel_open_orders` may cancel manual orders too." Code-side, we could filter by `t.order.clientId == self.client_id` for belt-and-braces; cheap, do it.

2. **Cancel race** — between cancelling and re-submitting, a partial fill could occur. For market orders queued outside RTH this is essentially impossible. Inside RTH it's possible but unlikely in the 1s window. Acceptable.

3. **Fractional floor loses value** — `2.16 → 2` strands `0.16` shares. The user must liquidate fractional residuals manually via desktop. Surface a clear log line so they know.

## Verification

1. `POSTGRES_URI=… PYTHONPATH=. uv run pytest tests/test_livetrade.py tests/test_livetrade_ib.py -q` — all pass (existing 8 + 2 new).
2. **Local dry-run** via VSCode "LiveTrade: Interactive Brokers (paper)" with `LIVETRADE_DRY_RUN=true` — confirm log says `[DRY RUN] Would SELL …` and exits in seconds, not minutes.
3. **Live paper run** during after-hours with `LIVETRADE_DRY_RUN=false`:
   - First run: confirm 40 orders submit in ~10s total (no 30s waits). Log shows `Submitted SELL …` lines plus broker-side `PreSubmitted` status. Container exits cleanly.
   - Second run (immediately after first): confirm log says `Cancelled N stale open orders before sync`, then orders are re-submitted with up-to-date sizes. Final IB account state shows N working orders, not 2N.
   - Confirm fractional `VSNT 2.16` is rounded to `2` and submits cleanly (no error 10243).
4. **Next-day check** (after market opens): orders should fill. Confirm subsequent cron run sees the new positions and computes new diffs correctly.
