import argparse
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Set, List, Dict

from .symbol_map import SymbolMapper
from .collective2 import Collective2Broker
from utils.db import get_db_session, Bot as BotModel, Trade
from utils.bot_repository import BotRepository

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("discover_symbols")

class SymbolDiscoverer:
    def __init__(self, broker):
        self.broker = broker
        self.mapper = SymbolMapper()
        self.bot_repo = BotRepository()

    def collect_all_tickers(self) -> Set[str]:
        tickers = set()
        
        with get_db_session() as session:
            # 1. Tickers from Bot portfolios
            bots = session.query(BotModel).all()
            for bot in bots:
                if bot.portfolio:
                    for s in bot.portfolio.keys():
                        if s != "USD":
                            tickers.add(s)

            # 2. Tickers from recent Trades (90 days)
            cutoff = datetime.utcnow() - timedelta(days=90)
            trades = session.query(Trade.symbol).filter(Trade.timestamp > cutoff).distinct().all()
            for (s,) in trades:
                tickers.add(s)

        # 3. Tickers from Bot class files
        tickers.update(self._parse_bot_files())
        
        return tickers

    def _parse_bot_files(self) -> Set[str]:
        found = set()
        bot_dir = "tradingbot"
        if not os.path.exists(bot_dir):
            return found
            
        symbol_pattern = re.compile(r"['\"]([A-Z0-9=.\-]{2,10})['\"]")
        
        for filename in os.listdir(bot_dir):
            if filename.endswith("bot.py") or filename == "adaptivemeanreversionbot.py":
                path = os.path.join(bot_dir, filename)
                try:
                    with open(path, "r") as f:
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

        to_review = {
            "_instructions": (
                "For each ticker below, pick the correct candidate by adding "
                "'selected_symbol': '...' and 'selected_type': '...' to its object. "
                "Then run with --apply to merge approved entries into symbol_map.json."
            )
        }
        
        new_tickers_count = 0
        for yf_symbol in sorted(all_tickers):
            # Skip if already in overrides
            if yf_symbol in self.mapper.overrides:
                continue
                
            # Check if default rule works cleanly
            default_meta = self.mapper.map_symbol(yf_symbol)
            
            # Optimization: Skip if default-rule is stock and search returns exact match
            if default_meta["source"] == "default-rule" and default_meta["type"] == "stock":
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
            
            # Dedupe candidates
            seen_c = set()
            unique_candidates = []
            for c in candidates:
                if c["symbol"] not in seen_c:
                    unique_candidates.append(c)
                    seen_c.add(c["symbol"])

            to_review[yf_symbol] = {
                "default_mapping": default_meta,
                "candidates": unique_candidates[:5]
            }
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

        with open(review_file, "r") as f:
            review_data = json.load(f)

        count = 0
        for yf_symbol, data in review_data.items():
            if yf_symbol.startswith("_"): continue
            
            selected = data.get("selected_symbol")
            if selected:
                self.mapper.overrides[yf_symbol] = {
                    "symbol": selected,
                    "type": data.get("selected_type", data.get("default_mapping", {}).get("type", "stock")),
                    "verified": datetime.now().strftime("%Y-%m-%d"),
                    "source": "human"
                }
                count += 1
        
        if count > 0:
            map_path = str(Path(__file__).parent / "symbol_map.json")
            with open(map_path, "w") as f:
                json.dump(self.mapper.overrides, f, indent=2)
            logger.info(f"Merged {count} entries into symbol_map.json")
        else:
            logger.info("No entries were marked with 'selected_symbol' in the review file.")

def main():
    parser = argparse.ArgumentParser(description="Discover and map yfinance tickers to broker symbols.")
    parser.add_argument("--apply", action="store_true", help="Merge approved entries from review file")
    parser.add_argument("--review-file", default="symbol_map.review.json", help="Path to review file")
    
    args = parser.parse_args()
    
    api_key = os.getenv("COLLECTIVE2_API_KEY", "")
    system_id = os.getenv("COLLECTIVE2_SYSTEM_ID", "")
    
    broker = Collective2Broker(api_key, system_id)
    discoverer = SymbolDiscoverer(broker)
    
    if args.apply:
        discoverer.apply_review(args.review_file)
    else:
        discoverer.discover(review_file=args.review_file)

if __name__ == "__main__":
    main()
