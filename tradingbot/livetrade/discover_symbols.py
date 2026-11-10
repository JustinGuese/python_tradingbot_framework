import argparse
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.db import Bot as BotModel
from tradingbot.utils.db import Trade, get_db_session
from tradingbot.utils.options import is_option_symbol

from .broker import LiveBroker
from .registry import REGISTRY, ConfigError
from .symbol_map import SymbolMapper

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("discover_symbols")

#: Short --broker values kept working because the guides document them
#: (docs/guides/live-trading.md uses `--broker ib`).
_BROKER_ALIASES = {
    "c2": "collective2",
    "ib": "interactive_brokers",
    "mt5": "mt5_mql5",
}


class SymbolDiscoverer:
    def __init__(
        self,
        broker,
        broker_name: str | None = None,
        map_file: str | None = None,
    ):
        self.broker = broker
        if broker_name is not None:
            self.broker_name = broker_name
        elif isinstance(broker, str):
            self.broker_name = broker
        else:
            self.broker_name = getattr(broker, "name", None)
        self.map_file = map_file or str(Path(__file__).parent / "symbol_map.json")
        self.mapper = SymbolMapper(self.map_file)
        self.bot_repo = BotRepository()

    def collect_all_tickers(self) -> set[str]:
        tickers = set()

        with get_db_session() as session:
            # 1. Tickers from Bot portfolios
            bots = session.query(BotModel).all()
            for bot in bots:
                if bot.portfolio:
                    for s in bot.portfolio:
                        if s != "USD":
                            tickers.add(s)

            # 2. Tickers from recent Trades (90 days)
            # Naive UTC: Trade.timestamp is a bare DateTime, so an aware cutoff would
            # raise on comparison. utcnow() is deprecated in 3.12 — same value, no warning.
            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90)
            trades = session.query(Trade.symbol).filter(Trade.timestamp > cutoff).distinct().all()
            for (s,) in trades:
                tickers.add(s)

        # 3. Tickers from Bot class files
        tickers.update(self._parse_bot_files())

        # Option contracts never reach a broker (the copier drops them), so
        # there is nothing to map.
        return {t for t in tickers if not is_option_symbol(t)}

    def _parse_bot_files(self) -> set[str]:
        found: set[str] = set()
        bot_dir = "tradingbot"
        if not os.path.exists(bot_dir):
            return found

        symbol_pattern = re.compile(r"['\"]([A-Z0-9=.\-]{2,10})['\"]")

        for filename in os.listdir(bot_dir):
            if filename.endswith("bot.py") or filename == "adaptivemeanreversionbot.py":
                path = os.path.join(bot_dir, filename)
                try:
                    with open(path) as f:
                        content = f.read()
                        matches = symbol_pattern.findall(content)
                        for m in matches:
                            if m not in ["USD", "BUY", "SELL", "HOLD", "INFO", "DEBUG", "True", "False"]:
                                found.add(m)
                except Exception:
                    continue
        return found

    def discover(self, review_file: str = "symbol_map.review.json"):
        all_tickers = self.collect_all_tickers()
        logger.info(f"Collected {len(all_tickers)} unique tickers from database and code.")

        to_review: dict[str, str | dict] = {
            "_instructions": (
                "For each ticker below, pick the correct candidate by adding "
                "'selected_symbol': '...' and 'selected_type': '...' to its object. "
                "Then run with --apply to merge approved entries into symbol_map.json."
            )
        }

        new_tickers_count = 0
        for yf_symbol in sorted(all_tickers):
            # Skip if already in overrides for this broker
            if self.broker_name:
                entry = self.mapper.overrides.get(yf_symbol)
                if isinstance(entry, dict) and self.broker_name in entry:
                    continue
                if isinstance(entry, dict) and "symbol" in entry:
                    continue
            elif yf_symbol in self.mapper.overrides:
                continue

            # Check if default rule works cleanly
            default_meta = self.mapper.map_symbol(yf_symbol, broker_name=self.broker_name)

            # Optimization: Skip if default-rule is stock and search returns exact match
            exact_match = None
            if (
                default_meta is not None
                and default_meta["source"] == "default-rule"
                and default_meta["type"] == "stock"
            ):
                candidates = self.broker.search_symbol(yf_symbol)
                exact_match = next((c for c in candidates if c["symbol"] == yf_symbol), None)
                if exact_match:
                    logger.info(f"Skipping {yf_symbol}: Clean default-rule mapping verified via search.")
                    continue
            else:
                candidates = self.broker.search_symbol(yf_symbol)

            logger.info(f"Adding {yf_symbol} to review file.")

            # Also try stripped version for candidates
            stripped = yf_symbol.replace("=X", "").replace("-USD", "").replace("^", "")
            if stripped != yf_symbol:
                candidates.extend(self.broker.search_symbol(stripped))

            # Also try common suffixed versions (e.g. MT5 / Darwinex style .US/.P/.O/.N)
            # if bare search didn't find an exact match
            if not exact_match and not any(ch in stripped for ch in "=-^."):
                for sfx in (".US", ".P", ".O", ".N"):
                    candidates.extend(self.broker.search_symbol(f"{stripped}{sfx}"))

            # Dedupe candidates
            seen_c = set()
            unique_candidates = []
            for c in candidates:
                if c["symbol"] not in seen_c:
                    unique_candidates.append(c)
                    seen_c.add(c["symbol"])

            to_review[yf_symbol] = {"default_mapping": default_meta, "candidates": unique_candidates[:5]}
            new_tickers_count += 1

        if new_tickers_count > 0:
            with open(review_file, "w") as f:
                json.dump(to_review, f, indent=2)
            logger.info(f"Wrote {new_tickers_count} tickers to {review_file} for review.")
        else:
            logger.info("No new tickers to discover.")

    def apply_review(self, review_file: str = "symbol_map.review.json"):
        if not os.path.exists(review_file):
            logger.error(f"Review file {review_file} not found.")
            return

        with open(review_file) as f:
            review_data = json.load(f)

        count = 0
        for yf_symbol, data in review_data.items():
            if yf_symbol.startswith("_"):
                continue

            selected = data.get("selected_symbol")
            if selected:
                entry = {
                    "symbol": selected,
                    "type": data.get("selected_type", data.get("default_mapping", {}).get("type", "stock")),
                    "verified": datetime.now().strftime("%Y-%m-%d"),
                    "source": "human",
                }
                if self.broker_name:
                    if (
                        yf_symbol not in self.mapper.overrides
                        or not isinstance(self.mapper.overrides[yf_symbol], dict)
                        or "symbol" in self.mapper.overrides[yf_symbol]
                    ):
                        self.mapper.overrides[yf_symbol] = {}
                    self.mapper.overrides[yf_symbol][self.broker_name] = entry
                else:
                    self.mapper.overrides[yf_symbol] = entry
                count += 1

        if count > 0:
            with open(self.map_file, "w") as f:
                json.dump(self.mapper.overrides, f, indent=2)
                f.write("\n")
            logger.info(f"Merged {count} entries into {self.map_file}")
        else:
            logger.info("No entries were marked with 'selected_symbol' in the review file.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover and map yfinance tickers to broker symbols.")
    parser.add_argument("--apply", action="store_true", help="Merge approved entries from review file")
    parser.add_argument("--review-file", default="symbol_map.review.json", help="Path to review file")
    parser.add_argument("--map-file", default=None, help="Path to symbol map file")
    parser.add_argument(
        "--broker",
        default="c2",
        choices=sorted(set(REGISTRY) | set(_BROKER_ALIASES) | set(_BROKER_ALIASES.values())),
        help="Broker to use for search",
    )

    args = parser.parse_args(argv)

    # Construction comes from the registry rather than a third hand-rolled copy of
    # env->broker wiring. Two things follow for free: hyperliquid becomes selectable
    # (it was missing from the old choices list), and missing credentials now raise
    # ConfigError instead of constructing an adapter with empty-string secrets that
    # fails later with an opaque auth error.
    broker_key = _BROKER_ALIASES.get(args.broker, args.broker)
    spec = REGISTRY[broker_key]
    try:
        spec.check_required_env()
        broker: LiveBroker = spec.build()
    except ConfigError as e:
        logger.error(str(e))
        return 2

    discoverer = SymbolDiscoverer(broker, broker_name=broker_key, map_file=args.map_file)

    try:
        # No-op except on IBKR, which needs a session. readonly=True because symbol
        # discovery only ever searches — it must never be able to place an order.
        broker.connect(readonly=True)

        if args.apply:
            discoverer.apply_review(args.review_file)
        else:
            discoverer.discover(review_file=args.review_file)
    finally:
        broker.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
