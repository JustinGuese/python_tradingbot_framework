# Retail Venue Symbol Coverage Audit: S&P 100 Universe (2026-11)

## Summary

Evaluation of symbol coverage and adapter mapping rules for retail execution venues (eToro, MT5/MQL5, and Collective2) under the planned migration of retail live books to `InstitutionalFlowBot` (S&P 100 single-stock momentum book).

- **Universe:** S&P 100 constituents (99 traded + 2 excluded, total 101 names).
- **Key Recent Additions Inspected:** PLTR, SNDK, GEV, DELL, ANET.
- **Execution Environment:** Offline audit (no broker credentials, no live API / terminal queries).
- **Live-Lookup Rows:** Marked as `to be filled` awaiting live broker query verification.

### High-Level Coverage Findings

1. **`symbol_map.json` Overrides:**
   - The current `symbol_map.json` contains 20 ETF overrides for eToro (e.g. SPY, VTI, IWM, TLT, GLD) and 0 overrides for MT5.
   - There are currently **0 / 99 S&P 100 stock overrides** in `symbol_map.json` for either retail broker.
2. **eToro Adapter Logic:**
   - `EtoroBroker.map_symbol()` relies on `api/v1/market-data/search` at runtime when an override is absent.
   - `EtoroBroker.is_tradeable()` checks `self.map_symbol()`, and raises `RuntimeError` if unlisted/failed unless explicitly recorded in `_unlisted`.
   - All 99 traded S&P 100 stocks require live search query resolution.
3. **MT5 Adapter Logic:**
   - `MT5Broker._resolve_symbol()` queries the MT5 terminal symbol index for exact matches or suffixes (`.US`, `.O`, `.N`, `.P`).
   - Most large-cap US equities on MT5 brokers (e.g. IC Markets, Admiral, RoboForex) trade under bare `TICKER` or `TICKER.US`.
   - All 99 traded S&P 100 stocks require terminal symbol scans against the target broker server.
4. **Collective2 Adapter Logic:**
   - Deterministic offline rules: standard US tickers trade cleanly without exchange dots or carats.
   - 99 of 99 traded S&P 100 names resolve cleanly as tradeable (`is_tradeable=True`).
   - Excluded names: `BRK.B` is rejected by C2 adapter logic due to dot notation (`.`), and `GOOG` is excluded as a duplicate share class of `GOOGL`.

---

## S&P 100 Retail Coverage Table

| Ticker | SP100 Status | eToro Tradeable | eToro Symbol | MT5 Tradeable | MT5 Symbol | C2 Tradeable | C2 Symbol | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| AAPL | Traded | to be filled | to be filled | to be filled | to be filled | yes | AAPL | - |
| ABBV | Traded | to be filled | to be filled | to be filled | to be filled | yes | ABBV | - |
| ABT | Traded | to be filled | to be filled | to be filled | to be filled | yes | ABT | - |
| ACN | Traded | to be filled | to be filled | to be filled | to be filled | yes | ACN | - |
| ADBE | Traded | to be filled | to be filled | to be filled | to be filled | yes | ADBE | - |
| AMAT | Traded | to be filled | to be filled | to be filled | to be filled | yes | AMAT | - |
| AMD | Traded | to be filled | to be filled | to be filled | to be filled | yes | AMD | - |
| AMGN | Traded | to be filled | to be filled | to be filled | to be filled | yes | AMGN | - |
| AMT | Traded | to be filled | to be filled | to be filled | to be filled | yes | AMT | - |
| AMZN | Traded | to be filled | to be filled | to be filled | to be filled | yes | AMZN | - |
| ANET | Traded | to be filled | to be filled | to be filled | to be filled | yes | ANET | Recent S&P 100 add |
| AVGO | Traded | to be filled | to be filled | to be filled | to be filled | yes | AVGO | - |
| AXP | Traded | to be filled | to be filled | to be filled | to be filled | yes | AXP | - |
| BA | Traded | to be filled | to be filled | to be filled | to be filled | yes | BA | - |
| BAC | Traded | to be filled | to be filled | to be filled | to be filled | yes | BAC | - |
| BKNG | Traded | to be filled | to be filled | to be filled | to be filled | yes | BKNG | - |
| BLK | Traded | to be filled | to be filled | to be filled | to be filled | yes | BLK | - |
| BMY | Traded | to be filled | to be filled | to be filled | to be filled | yes | BMY | - |
| BNY | Traded | to be filled | to be filled | to be filled | to be filled | yes | BNY | - |
| BRK.B | Excluded | to be filled | to be filled | to be filled | to be filled | no | BRK.B | SP100 Excluded: class-B ticker not tradeable through the copier's symbol rules |
| C | Traded | to be filled | to be filled | to be filled | to be filled | yes | C | - |
| CAT | Traded | to be filled | to be filled | to be filled | to be filled | yes | CAT | - |
| CMCSA | Traded | to be filled | to be filled | to be filled | to be filled | yes | CMCSA | - |
| COF | Traded | to be filled | to be filled | to be filled | to be filled | yes | COF | - |
| COP | Traded | to be filled | to be filled | to be filled | to be filled | yes | COP | - |
| COST | Traded | to be filled | to be filled | to be filled | to be filled | yes | COST | - |
| CRM | Traded | to be filled | to be filled | to be filled | to be filled | yes | CRM | - |
| CSCO | Traded | to be filled | to be filled | to be filled | to be filled | yes | CSCO | - |
| CVS | Traded | to be filled | to be filled | to be filled | to be filled | yes | CVS | - |
| CVX | Traded | to be filled | to be filled | to be filled | to be filled | yes | CVX | - |
| DE | Traded | to be filled | to be filled | to be filled | to be filled | yes | DE | - |
| DELL | Traded | to be filled | to be filled | to be filled | to be filled | yes | DELL | Recent S&P 100 add |
| DHR | Traded | to be filled | to be filled | to be filled | to be filled | yes | DHR | - |
| DIS | Traded | to be filled | to be filled | to be filled | to be filled | yes | DIS | - |
| DUK | Traded | to be filled | to be filled | to be filled | to be filled | yes | DUK | - |
| EMR | Traded | to be filled | to be filled | to be filled | to be filled | yes | EMR | - |
| FDX | Traded | to be filled | to be filled | to be filled | to be filled | yes | FDX | - |
| GD | Traded | to be filled | to be filled | to be filled | to be filled | yes | GD | - |
| GE | Traded | to be filled | to be filled | to be filled | to be filled | yes | GE | - |
| GEV | Traded | to be filled | to be filled | to be filled | to be filled | yes | GEV | Recent S&P 100 add |
| GILD | Traded | to be filled | to be filled | to be filled | to be filled | yes | GILD | - |
| GM | Traded | to be filled | to be filled | to be filled | to be filled | yes | GM | - |
| GOOG | Excluded | to be filled | to be filled | to be filled | to be filled | yes | GOOG | SP100 Excluded: duplicate share class of GOOGL |
| GOOGL | Traded | to be filled | to be filled | to be filled | to be filled | yes | GOOGL | - |
| GS | Traded | to be filled | to be filled | to be filled | to be filled | yes | GS | - |
| HD | Traded | to be filled | to be filled | to be filled | to be filled | yes | HD | - |
| IBM | Traded | to be filled | to be filled | to be filled | to be filled | yes | IBM | - |
| INTC | Traded | to be filled | to be filled | to be filled | to be filled | yes | INTC | - |
| INTU | Traded | to be filled | to be filled | to be filled | to be filled | yes | INTU | - |
| ISRG | Traded | to be filled | to be filled | to be filled | to be filled | yes | ISRG | - |
| JNJ | Traded | to be filled | to be filled | to be filled | to be filled | yes | JNJ | - |
| JPM | Traded | to be filled | to be filled | to be filled | to be filled | yes | JPM | - |
| KO | Traded | to be filled | to be filled | to be filled | to be filled | yes | KO | - |
| LIN | Traded | to be filled | to be filled | to be filled | to be filled | yes | LIN | - |
| LLY | Traded | to be filled | to be filled | to be filled | to be filled | yes | LLY | - |
| LMT | Traded | to be filled | to be filled | to be filled | to be filled | yes | LMT | - |
| LOW | Traded | to be filled | to be filled | to be filled | to be filled | yes | LOW | - |
| LRCX | Traded | to be filled | to be filled | to be filled | to be filled | yes | LRCX | - |
| MA | Traded | to be filled | to be filled | to be filled | to be filled | yes | MA | - |
| MCD | Traded | to be filled | to be filled | to be filled | to be filled | yes | MCD | - |
| MDLZ | Traded | to be filled | to be filled | to be filled | to be filled | yes | MDLZ | - |
| MDT | Traded | to be filled | to be filled | to be filled | to be filled | yes | MDT | - |
| META | Traded | to be filled | to be filled | to be filled | to be filled | yes | META | - |
| MMM | Traded | to be filled | to be filled | to be filled | to be filled | yes | MMM | - |
| MO | Traded | to be filled | to be filled | to be filled | to be filled | yes | MO | - |
| MRK | Traded | to be filled | to be filled | to be filled | to be filled | yes | MRK | - |
| MS | Traded | to be filled | to be filled | to be filled | to be filled | yes | MS | - |
| MSFT | Traded | to be filled | to be filled | to be filled | to be filled | yes | MSFT | - |
| MU | Traded | to be filled | to be filled | to be filled | to be filled | yes | MU | - |
| NEE | Traded | to be filled | to be filled | to be filled | to be filled | yes | NEE | - |
| NFLX | Traded | to be filled | to be filled | to be filled | to be filled | yes | NFLX | - |
| NOW | Traded | to be filled | to be filled | to be filled | to be filled | yes | NOW | - |
| NVDA | Traded | to be filled | to be filled | to be filled | to be filled | yes | NVDA | - |
| ORCL | Traded | to be filled | to be filled | to be filled | to be filled | yes | ORCL | - |
| PANW | Traded | to be filled | to be filled | to be filled | to be filled | yes | PANW | - |
| PEP | Traded | to be filled | to be filled | to be filled | to be filled | yes | PEP | - |
| PFE | Traded | to be filled | to be filled | to be filled | to be filled | yes | PFE | - |
| PG | Traded | to be filled | to be filled | to be filled | to be filled | yes | PG | - |
| PLTR | Traded | to be filled | to be filled | to be filled | to be filled | yes | PLTR | Recent S&P 100 add |
| PM | Traded | to be filled | to be filled | to be filled | to be filled | yes | PM | - |
| QCOM | Traded | to be filled | to be filled | to be filled | to be filled | yes | QCOM | - |
| RTX | Traded | to be filled | to be filled | to be filled | to be filled | yes | RTX | - |
| SBUX | Traded | to be filled | to be filled | to be filled | to be filled | yes | SBUX | - |
| SCHW | Traded | to be filled | to be filled | to be filled | to be filled | yes | SCHW | - |
| SNDK | Traded | to be filled | to be filled | to be filled | to be filled | yes | SNDK | Recent S&P 100 add |
| SO | Traded | to be filled | to be filled | to be filled | to be filled | yes | SO | - |
| T | Traded | to be filled | to be filled | to be filled | to be filled | yes | T | - |
| TMO | Traded | to be filled | to be filled | to be filled | to be filled | yes | TMO | - |
| TMUS | Traded | to be filled | to be filled | to be filled | to be filled | yes | TMUS | - |
| TSLA | Traded | to be filled | to be filled | to be filled | to be filled | yes | TSLA | - |
| TXN | Traded | to be filled | to be filled | to be filled | to be filled | yes | TXN | - |
| UBER | Traded | to be filled | to be filled | to be filled | to be filled | yes | UBER | - |
| UNH | Traded | to be filled | to be filled | to be filled | to be filled | yes | UNH | - |
| UNP | Traded | to be filled | to be filled | to be filled | to be filled | yes | UNP | - |
| UPS | Traded | to be filled | to be filled | to be filled | to be filled | yes | UPS | - |
| USB | Traded | to be filled | to be filled | to be filled | to be filled | yes | USB | - |
| V | Traded | to be filled | to be filled | to be filled | to be filled | yes | V | - |
| VZ | Traded | to be filled | to be filled | to be filled | to be filled | yes | VZ | - |
| WFC | Traded | to be filled | to be filled | to be filled | to be filled | yes | WFC | - |
| WMT | Traded | to be filled | to be filled | to be filled | to be filled | yes | WMT | - |
| XOM | Traded | to be filled | to be filled | to be filled | to be filled | yes | XOM | - |

---

## Unmapped-Name Behavior & Architectural Decision

### Current Behavior: `LIVETRADE_STRICT_MAPPING=true` (copier.py:101)

Under `tradingbot/livetrade/copier.py`:101:
```python
if unmapped_tickers and self.strict_mapping:
    logger.error(f"STRICT MODE: Aborting sync due to unmapped tickers: {unmapped_tickers}")
    return
```

- **Mechanism:** If any single stock in `target_weights` cannot be mapped by the broker adapter, and `broker.is_tradeable` does not return `False`, the ticker lands in `unmapped_tickers`.
- **Consequence:** One single unmapped ticker causes the **entire sync to abort**. No sells are executed, no buys are submitted, and existing positions are not rebalanced.
- **Operational Impact for S&P 100:** In an ETF portfolio of 10 symbols, an unmapped symbol is rare and easily fixed in `symbol_map.json`. In a 100-stock single-equity universe, retail brokers frequently lack or delay coverage for certain stocks (e.g. spinoffs like GEV, newly added high-flyers like PLTR, or corporate actions). Aborting the whole sync halts all trading across the retail book indefinitely.

### Decision: Drop Unmapped / Untradeable Names to Cash

Two alternatives were considered:
1. **Alternative 1: Up-front universe intersection filtering.** Filter `InstitutionalFlowBot` to only hold names verified on all 3 retail venues (eToro, MT5, Collective2).
   - *Drawback:* Forces the core alpha strategy to trade a lowest-common-denominator universe, reduces investment opportunity, requires continuous synchronization whenever any retail broker changes listings, and diverges from the institutional paper backtest.
2. **Alternative 2 (RECOMMENDED & ADOPTED): Drop untradeable/unmapped names to cash/SHV.**
   - *Mechanism:* Modeled after Collective2's `is_tradeable()` and the existing copier untradeable leg drop (`copier.py:76-78`). When a venue cannot trade or map a ticker, the ticker's weight is dropped to cash (or parked into `cash_proxy` like `SHV`).
   - *Advantage:* If 1 of 20 names (5% weight) is unlisted on eToro or MT5, the remaining 19 names (95% weight) continue trading cleanly. The portfolio suffers a minor 5% cash drag rather than a 100% outage.
   - *Safety:* Strict mapping should still abort if an unmapped ticker was expected to trade, UNLESS the adapter explicitly marks the name as unlisted/untradeable (`is_tradeable=False`).

### Test Verification Summary

Unit tests in `tests/test_retail_coverage.py` pin both behaviors:
1. `test_strict_copier_aborts_on_unmapped_ticker`: Confirms that an unmapped ticker under strict mode aborts sync.
2. `test_drop_to_cash_path_trades_remaining_and_leaves_weight_in_cash`: Confirms that marking untradeable names via `is_tradeable=False` drops the weight to cash while executing all remaining valid orders.
3. `test_drop_to_cash_path_parks_in_shv`: Confirms that idle dropped weight is parked into `SHV` when a cash proxy is configured.
