"""
Venue-generic symbol coverage tool.

Checks whether a broker adapter can resolve and trade every symbol in the
Collective2 blend universe (TARegimeMultiAssetBot, TSMOMTrendBot, GoldenButterflyMomBot).
"""

import argparse
import logging
import sys
from collections.abc import Iterable
from typing import Any

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.registry import REGISTRY, ConfigError

logger = logging.getLogger(__name__)

_BROKER_ALIASES: dict[str, str] = {
    "c2": "collective2",
    "ib": "interactive_brokers",
}


def blend_universe() -> list[str]:
    """Union of every ticker the three blend bots can hold.

    Reads universe constants from the bot modules and tradingbot.utils.universes
    rather than hardcoding a list.
    """
    from tradingbot.goldenbutterflymombot import UNIVERSE as gb_universe
    from tradingbot.taregimemultiassetbot import UNIVERSE as ta_universe
    from tradingbot.tsmomtrendbot import UNIVERSE as tsmom_universe
    import tradingbot.utils.universes as universes_module

    tickers: set[str] = set()
    tickers.update(ta_universe)
    tickers.update(tsmom_universe)
    tickers.update(gb_universe)

    for attr in ("BLEND_UNIVERSE", "C2_BLEND_UNIVERSE"):
        extra = getattr(universes_module, attr, None)
        if extra:
            tickers.update(extra)

    return sorted(tickers)


def check_coverage(broker: LiveBroker, tickers: Iterable[str] | None = None) -> dict[str, Any]:
    """Check symbol coverage for the given broker across tickers.

    Returns:
        {
            "resolved": dict[str, str],  # yf_symbol -> mapped broker symbol
            "missing": list[str],        # unmapped / unresolved tickers
            "untradeable": list[str],    # tickers broker cannot trade
        }
    It never writes anything.
    """
    if tickers is None:
        tickers = blend_universe()

    resolved: dict[str, str] = {}
    missing: list[str] = []
    untradeable: list[str] = []

    seen: set[str] = set()
    for ticker in tickers:
        if ticker in seen:
            continue
        seen.add(ticker)

        if not broker.is_tradeable(ticker):
            untradeable.append(ticker)
            continue

        meta = broker.map_symbol(ticker)
        mapped = (
            meta.get("symbol")
            if isinstance(meta, dict)
            else (meta if isinstance(meta, str) else None)
        )

        if meta and mapped and not str(mapped).startswith("^"):
            resolved[ticker] = str(mapped)
        else:
            missing.append(ticker)

    return {
        "resolved": resolved,
        "missing": sorted(missing),
        "untradeable": sorted(untradeable),
    }


def format_coverage_table(broker_name: str, results: dict[str, Any]) -> str:
    """Format coverage results into a human-readable table."""
    resolved = results.get("resolved", {})
    missing = results.get("missing", [])
    untradeable = results.get("untradeable", [])
    total = len(resolved) + len(missing) + len(untradeable)

    lines = [
        f"\nCoverage report: {broker_name}",
        f"Universe size: {total} | Resolved: {len(resolved)} | Missing: {len(missing)} | Untradeable: {len(untradeable)}",
        "-" * 55,
        f"{'Ticker':<12} {'Status':<15} {'Broker Symbol':<20}",
        "-" * 55,
    ]

    all_tickers = sorted(set(resolved) | set(missing) | set(untradeable))
    for t in all_tickers:
        if t in untradeable:
            status = "untradeable"
            mapped = "-"
        elif t in missing:
            status = "missing"
            mapped = "-"
        else:
            status = "resolved"
            mapped = resolved.get(t, "")
        lines.append(f"{t:<12} {status:<15} {mapped:<20}")

    lines.append("-" * 55)
    if missing or untradeable:
        lines.append(f"Gaps found: {len(missing)} missing, {len(untradeable)} untradeable.")
    else:
        lines.append("All symbols resolved successfully. No gaps.")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check symbol coverage for a broker against the blend universe"
    )
    parser.add_argument(
        "--broker",
        required=True,
        help="Broker registry key or alias to check",
    )
    args = parser.parse_args(argv)

    broker_key = _BROKER_ALIASES.get(args.broker, args.broker)
    if broker_key not in REGISTRY:
        logger.error(
            "Unknown broker '%s'. Available brokers: %s",
            args.broker,
            ", ".join(sorted(REGISTRY)),
        )
        return 2

    spec = REGISTRY[broker_key]
    try:
        spec.check_required_env()
        broker = spec.build()
    except ConfigError as e:
        logger.error("Configuration error for broker '%s': %s", broker_key, e)
        return 2
    except Exception as e:
        logger.error("Failed to build broker '%s': %s", broker_key, e)
        return 2

    try:
        broker.connect(readonly=True)
        tickers = blend_universe()
        results = check_coverage(broker, tickers)
    except Exception as e:
        logger.error("Coverage check failed for '%s': %s", broker_key, e)
        return 1
    finally:
        try:
            broker.disconnect()
        except Exception:
            pass

    print(format_coverage_table(broker_key, results))

    if results["missing"] or results["untradeable"]:
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    sys.exit(main())
