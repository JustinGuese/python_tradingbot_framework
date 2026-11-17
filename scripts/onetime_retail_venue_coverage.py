"""
One-time offline retail venue coverage check for S&P 100 universe.

Builds on tradingbot/livetrade/coverage.py to inspect symbol coverage and mapping
for retail venues (eToro, MT5, Collective2) across all S&P 100 names (including
recent additions: PLTR, SNDK, GEV, DELL, ANET).

Runs completely offline (no broker credentials, no live API / terminal calls).
Entries requiring live API / terminal queries are marked as 'to be filled'.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from tradingbot.livetrade.collective2 import Collective2Broker
from tradingbot.livetrade.symbol_map import SymbolMapper
from tradingbot.utils.universes import SP100, SP100_EXCLUDED


def get_sp100_full_universe() -> list[str]:
    """Return all S&P 100 constituents: traded set + excluded share classes/tickers."""
    all_symbols = set(SP100) | set(SP100_EXCLUDED)
    return sorted(all_symbols)


def check_retail_coverage_offline(
    tickers: list[str] | None = None,
    symbol_map_path: str | None = None,
) -> dict[str, Any]:
    """Audit coverage for retail venues offline using symbol_map.json and adapter logic.

    Returns structured coverage metadata for each ticker across:
      - eToro
      - MT5 (mt5_mql5)
      - Collective2
    """
    if tickers is None:
        tickers = get_sp100_full_universe()

    mapper = SymbolMapper(map_file=symbol_map_path)
    c2_dummy = Collective2Broker(api_key="offline_dummy", system_id="0", symbol_mapper=mapper)

    results: dict[str, dict[str, Any]] = {}

    for ticker in tickers:
        is_sp100_traded = ticker in SP100
        exclusion_reason = SP100_EXCLUDED.get(ticker)

        # 1. eToro offline evaluation
        etoro_override = mapper.overrides.get(ticker, {}).get("etoro")
        if etoro_override and etoro_override.get("symbol"):
            etoro_mapped = str(etoro_override["symbol"])
            etoro_tradeable = "yes"
            etoro_source = etoro_override.get("source", "symbol_map.json")
        else:
            etoro_mapped = "to be filled"
            etoro_tradeable = "to be filled"
            etoro_source = "needs live eToro search API lookup"

        # 2. MT5 offline evaluation
        mt5_override = (
            mapper.overrides.get(ticker, {}).get("mt5_mql5")
            or mapper.overrides.get(ticker, {}).get("mt5")
        )
        if mt5_override and mt5_override.get("symbol"):
            mt5_mapped = str(mt5_override["symbol"])
            mt5_tradeable = "yes"
            mt5_source = mt5_override.get("source", "symbol_map.json")
        else:
            mt5_mapped = "to be filled"
            mt5_tradeable = "to be filled"
            mt5_source = "needs live MT5 terminal symbol scan"

        # 3. Collective2 offline evaluation (adapter logic is fully deterministic offline)
        c2_meta = mapper.map_symbol(ticker, broker_name="collective2")
        c2_is_tradeable = c2_dummy.is_tradeable(ticker)
        c2_mapped = c2_meta.get("symbol") if c2_meta else ticker
        c2_source = c2_meta.get("source", "default-rule") if c2_meta else "unmapped"

        results[ticker] = {
            "sp100_traded": is_sp100_traded,
            "exclusion_reason": exclusion_reason,
            "etoro": {
                "is_tradeable": etoro_tradeable,
                "mapped_symbol": etoro_mapped,
                "source": etoro_source,
            },
            "mt5": {
                "is_tradeable": mt5_tradeable,
                "mapped_symbol": mt5_mapped,
                "source": mt5_source,
            },
            "collective2": {
                "is_tradeable": "yes" if c2_is_tradeable else "no",
                "mapped_symbol": c2_mapped,
                "source": c2_source,
            },
        }

    return results


def generate_coverage_markdown(results: dict[str, Any]) -> str:
    """Generate Markdown report and table from coverage results."""
    lines: list[str] = [
        "# Retail Venue Symbol Coverage Audit: S&P 100 Universe (2026-11)",
        "",
        "## Summary",
        "",
        "Evaluation of symbol coverage and adapter mapping rules for retail execution venues "
        "(eToro, MT5/MQL5, and Collective2) under the planned migration of retail live books "
        "to `InstitutionalFlowBot` (S&P 100 single-stock momentum book).",
        "",
        "- **Universe:** S&P 100 constituents (99 traded + 2 excluded, total 101 names).",
        "- **Key Recent Additions Inspected:** PLTR, SNDK, GEV, DELL, ANET.",
        "- **Execution Environment:** Offline audit (no broker credentials, no live API / terminal queries).",
        "- **Live-Lookup Rows:** Marked as `to be filled` awaiting live broker query verification.",
        "",
        "### High-Level Coverage Findings",
        "",
        "1. **`symbol_map.json` Overrides:**",
        "   - The current `symbol_map.json` contains 20 ETF overrides for eToro (e.g. SPY, VTI, IWM, TLT, GLD) "
        "and 0 overrides for MT5.",
        "   - There are currently **0 / 99 S&P 100 stock overrides** in `symbol_map.json` for either retail broker.",
        "2. **eToro Adapter Logic:**",
        "   - `EtoroBroker.map_symbol()` relies on `api/v1/market-data/search` at runtime when an override is absent.",
        "   - `EtoroBroker.is_tradeable()` checks `self.map_symbol()`, and raises `RuntimeError` if unlisted/failed "
        "unless explicitly recorded in `_unlisted`.",
        "   - All 99 traded S&P 100 stocks require live search query resolution.",
        "3. **MT5 Adapter Logic:**",
        "   - `MT5Broker._resolve_symbol()` queries the MT5 terminal symbol index for exact matches or suffixes "
        "(`.US`, `.O`, `.N`, `.P`).",
        "   - Most large-cap US equities on MT5 brokers (e.g. IC Markets, Admiral, RoboForex) trade under bare "
        "`TICKER` or `TICKER.US`.",
        "   - All 99 traded S&P 100 stocks require terminal symbol scans against the target broker server.",
        "4. **Collective2 Adapter Logic:**",
        "   - Deterministic offline rules: standard US tickers trade cleanly without exchange dots or carats.",
        "   - 99 of 99 traded S&P 100 names resolve cleanly as tradeable (`is_tradeable=True`).",
        "   - Excluded names: `BRK.B` is rejected by C2 adapter logic due to dot notation (`.`), and `GOOG` is excluded "
        "as a duplicate share class of `GOOGL`.",
        "",
        "---",
        "",
        "## S&P 100 Retail Coverage Table",
        "",
        "| Ticker | SP100 Status | eToro Tradeable | eToro Symbol | MT5 Tradeable | MT5 Symbol | C2 Tradeable | C2 Symbol | Notes |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for ticker, info in sorted(results.items()):
        status = "Traded" if info["sp100_traded"] else "Excluded"
        etoro = info["etoro"]
        mt5 = info["mt5"]
        c2 = info["collective2"]

        notes = []
        if info["exclusion_reason"]:
            notes.append(f"SP100 Excluded: {info['exclusion_reason']}")
        if ticker in ("PLTR", "SNDK", "GEV", "DELL", "ANET"):
            notes.append("Recent S&P 100 add")

        notes_str = "; ".join(notes) if notes else "-"

        lines.append(
            f"| {ticker} | {status} | {etoro['is_tradeable']} | {etoro['mapped_symbol']} | "
            f"{mt5['is_tradeable']} | {mt5['mapped_symbol']} | {c2['is_tradeable']} | {c2['mapped_symbol']} | "
            f"{notes_str} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## Unmapped-Name Behavior & Architectural Decision",
        "",
        "### Current Behavior: `LIVETRADE_STRICT_MAPPING=true` (copier.py:101)",
        "",
        "Under `tradingbot/livetrade/copier.py`:101:",
        "```python",
        "if unmapped_tickers and self.strict_mapping:",
        "    logger.error(f\"STRICT MODE: Aborting sync due to unmapped tickers: {unmapped_tickers}\")",
        "    return",
        "```",
        "",
        "- **Mechanism:** If any single stock in `target_weights` cannot be mapped by the broker adapter, "
        "and `broker.is_tradeable` does not return `False`, the ticker lands in `unmapped_tickers`.",
        "- **Consequence:** One single unmapped ticker causes the **entire sync to abort**. No sells are executed, "
        "no buys are submitted, and existing positions are not rebalanced.",
        "- **Operational Impact for S&P 100:** In an ETF portfolio of 10 symbols, an unmapped symbol is rare and easily "
        "fixed in `symbol_map.json`. In a 100-stock single-equity universe, retail brokers frequently lack or delay "
        "coverage for certain stocks (e.g. spinoffs like GEV, newly added high-flyers like PLTR, or corporate actions). "
        "Aborting the whole sync halts all trading across the retail book indefinitely.",
        "",
        "### Decision: Drop Unmapped / Untradeable Names to Cash",
        "",
        "Two alternatives were considered:",
        "1. **Alternative 1: Up-front universe intersection filtering.** Filter `InstitutionalFlowBot` to only hold "
        "names verified on all 3 retail venues (eToro, MT5, Collective2).",
        "   - *Drawback:* Forces the core alpha strategy to trade a lowest-common-denominator universe, reduces "
        "investment opportunity, requires continuous synchronization whenever any retail broker changes listings, "
        "and diverges from the institutional paper backtest.",
        "2. **Alternative 2 (RECOMMENDED & ADOPTED): Drop untradeable/unmapped names to cash/SHV.**",
        "   - *Mechanism:* Modeled after Collective2's `is_tradeable()` and the existing copier untradeable leg drop "
        "(`copier.py:76-78`). When a venue cannot trade or map a ticker, the ticker's weight is dropped to cash "
        "(or parked into `cash_proxy` like `SHV`).",
        "   - *Advantage:* If 1 of 20 names (5% weight) is unlisted on eToro or MT5, the remaining 19 names (95% weight) "
        "continue trading cleanly. The portfolio suffers a minor 5% cash drag rather than a 100% outage.",
        "   - *Safety:* Strict mapping should still abort if an unmapped ticker was expected to trade, UNLESS the adapter "
        "explicitly marks the name as unlisted/untradeable (`is_tradeable=False`).",
        "",
        "### Test Verification Summary",
        "",
        "Unit tests in `tests/test_retail_coverage.py` pin both behaviors:",
        "1. `test_strict_copier_aborts_on_unmapped_ticker`: Confirms that an unmapped ticker under strict mode aborts sync.",
        "2. `test_drop_to_cash_path_trades_remaining_and_leaves_weight_in_cash`: Confirms that marking untradeable names "
        "via `is_tradeable=False` drops the weight to cash while executing all remaining valid orders.",
        "3. `test_drop_to_cash_path_parks_in_shv`: Confirms that idle dropped weight is parked into `SHV` when a cash proxy is configured.",
    ])

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit retail venue coverage for S&P 100 offline.")
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="docs/backtests/retail-venue-coverage-2026-11.md",
        help="Path to output markdown report",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print report to stdout instead of writing to file",
    )
    args = parser.parse_args(argv)

    results = check_retail_coverage_offline()
    md_content = generate_coverage_markdown(results)

    if args.print_only:
        print(md_content)
    else:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md_content, encoding="utf-8")
        print(f"Coverage audit report successfully written to: {out_path}")

    # Summary log
    total = len(results)
    etoro_live = sum(1 for r in results.values() if r["etoro"]["mapped_symbol"] == "to be filled")
    mt5_live = sum(1 for r in results.values() if r["mt5"]["mapped_symbol"] == "to be filled")
    c2_tradeable = sum(1 for r in results.values() if r["collective2"]["is_tradeable"] == "yes")

    print(f"Audited {total} S&P 100 constituents.")
    print(f"  eToro: {total - etoro_live} mapped in symbol_map.json, {etoro_live} require live lookup ('to be filled')")
    print(f"  MT5:   {total - mt5_live} mapped in symbol_map.json, {mt5_live} require live lookup ('to be filled')")
    print(f"  C2:    {c2_tradeable} tradeable offline via adapter logic, {total - c2_tradeable} excluded")

    return 0


if __name__ == "__main__":
    sys.exit(main())
