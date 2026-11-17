# PortfolioManager API Reference

::: tradingbot.utils.portfolio_manager.PortfolioManager

---

## Overview

`PortfolioManager` handles order execution, position tracking, cash management, option structures, and portfolio rebalancing. For short selling, it integrates directly with the Regulation T margin engine in `tradingbot.utils.margin` to enforce leverage caps, collateral locks, and fail-closed pricing.

---

## Short Selling Operations

### `short`

```python
def short(
    self,
    symbol: str,
    quantity_usd: float,
    cached_data: pd.DataFrame | None = None,
    session: Session | None = None,
    *,
    shortable: tuple[str, ...] | list[str] | set[str] | None = None,
    max_gross: float = 1.0,
) -> float: ...
```

Opens or increases a short stock position:

1. **Contract validation**: Stock symbols only; shorting option contracts directly with `short()` is rejected (`trade_option_legs` must be used for option legs).
2. **Shortable universe check**: Validates that `symbol` is present in `shortable` (or default `config.SHORTABLE`).
3. **Fail-closed pricing**: Validates that the latest market price is strictly positive. If the price is missing or invalid, raises `MissingPriceError`.
4. **Whole shares**: Floors shares to integer counts (`math.floor(quantity_usd / price)`).
5. **Margin validation**: Routes order through `trade_legs` with negative quantity (`[(symbol, -float(shares))]`), checking `initial_ok(resulting_portfolio, prices, max_gross)` before committing.
6. **Logging**: Logs a trade with `action="SHORT"` and credits gross proceeds to cash.

### `cover`

```python
def cover(
    self,
    symbol: str,
    quantity_usd: float = -1,
    cached_data: pd.DataFrame | None = None,
    session: Session | None = None,
) -> float: ...
```

Covers an existing short position:

1. **Position check**: Verifies an existing negative quantity for `symbol` exists; logs a warning and returns `0.0` if not short.
2. **Quantity resolution**: `quantity_usd = -1` covers the entire short position. Dollar amounts are converted to whole shares capped at `abs(current_holding)`.
3. **Execution**: Routes order through `trade_legs` with positive quantity (`[(symbol, float(shares))]`), deducting purchase cost from cash.
4. **Logging**: Logs a trade with `action="COVER"`.

---

## Portfolio Rebalancing (`rebalance_portfolio`)

```python
def rebalance_portfolio(
    self,
    target_portfolio: dict[str, float],
    only_over_50_usd: bool = False,
    *,
    allow_short: bool = False,
    max_gross: float = 1.0,
    shortable: tuple[str, ...] | list[str] | set[str] | None = None,
) -> None: ...
```

Rebalances a bot's portfolio to target weights in a single database transaction with row locking (`with_for_update`):

- **Target weight normalization**: Enforces `require_normalized(target_portfolio)`, requiring signed weights to sum to 1.0.
- **Short opt-in guard**: When `allow_short=False`, any negative target weight raises `ValueError`. When `allow_short=True`, negative target weights are validated against the `shortable` allowlist.
- **Fail-closed valuation**: Fetches prices for all held symbols and target symbols before locking the row. If any held short position lacks a valid price, raises `MissingPriceError`.
- **Two-phase leg execution**:
  1. **Phase 1 (Reductions & Covers)**: Closes exits, reduces oversized longs, and covers short positions. This frees cash collateral and margin capacity before entering new risk.
  2. **Phase 2 (Expansions & Short Sales)**: Enters new long positions and opens new short positions, validating `initial_ok(resulting_portfolio, prices, max_gross)`.

---

## Margin Checks & Spendable Cash

`PortfolioManager` utilizes pure margin functions from `tradingbot.utils.margin`:

- **`book_values(portfolio, prices)`**: Returns `(long_value, short_value, equity)`. Stock shorts only; option underlyings keep `options.margin_requirement`.
- **`initial_ok(portfolio, prices, max_gross)`**: Enforces Regulation T initial margin:
  $$\text{equity} \ge 0.5 \times \text{long\_value} + 0.5 \times \text{short\_value}$$
  and gross leverage constraint:
  $$\frac{\text{long\_value} + \text{short\_value}}{\text{equity}} \le \text{max\_gross}$$
- **`maintenance_breach(portfolio, prices)`**: Detects margin calls:
  $$\text{equity} < 0.25 \times \text{long\_value} + 0.30 \times \text{short\_value}$$
- **`spendable_cash(portfolio, prices)`**: Deducts short collateral from `USD` cash:
  $$\text{spendable\_cash} = \max(0.0, \text{cash} - \text{short\_value})$$
  Ensures short sale proceeds cannot be spent on long assets.
