"""
Preview what the multi-ticker bots WOULD do, without trading.

Part C changed how GoldenButterflyMomBot and KronosTraderBot allocate: the
equal-weight divisor now excludes benchmark-only tickers, overweight positions
are trimmed, and the whole book is reconciled in one rebalance instead of N
per-leg buys. Those bots run unattended (Kronos daily 22:15 UTC, GB Mondays
14:00 UTC), so this prints the exact target weights and per-leg dollar deltas
days ahead of the live run.

Read-only: it calls the pure weights function directly and never invokes
rebalancePortfolio, so nothing is written to bots / trades / run_logs. Data
fetches use saveToDB=False.

    POSTGRES_URI=... uv run python scripts/onetime_preview_multiticker.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tradingbot"))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from goldenbutterflymombot import GoldenButterflyMomBot  # noqa: E402
from kronostraderbot import KronosTraderBot  # noqa: E402

from utils.core import BotRepository  # noqa: E402


def preview(bot) -> None:
    print(f"\n{'=' * 78}\n{bot.bot_name}\n{'=' * 78}")

    tradeable = bot.tradeable_tickers
    print(f"  tickers      : {len(bot.tickers)}  {bot.tickers if len(bot.tickers) <= 12 else '(...)'}")
    print(f"  benchmarks   : {bot.benchmark_tickers or 'none'}")
    print(f"  divisor      : {len(tradeable)}  (was {len(bot.tickers)} before benchmarks were excluded)")

    for ticker in bot.tickers:
        try:
            bot.datas[ticker] = bot.getYFDataWithTA(
                symbol=ticker, saveToDB=False, interval=bot.interval, period=bot.period
            )
        except Exception as exc:
            print(f"  ! data fetch failed for {ticker}: {exc}")
            bot.datas[ticker] = None

    decisions = {}
    for ticker in tradeable:
        bot._current_ticker = ticker
        try:
            decisions[ticker] = bot.getLatestDecision(bot.datas[ticker])
        except Exception as exc:
            print(f"  ! decision failed for {ticker}: {exc}")
            decisions[ticker] = 0

    portfolio = BotRepository.read_portfolio(bot.bot_name) or {}
    symbols = sorted({*bot.tickers, *(s for s in portfolio if s != "USD")})
    prices = bot.getLatestPricesBatch(symbols)

    weights = bot._multi_ticker_target_weights(decisions, prices, portfolio)
    if not weights:
        print("  -> no rebalance this run")
        return

    total = portfolio.get("USD", 0.0) + sum(portfolio.get(s, 0.0) * (prices.get(s) or 0.0) for s in symbols)
    print(f"  book value   : ${total:,.2f}  (cash ${portfolio.get('USD', 0.0):,.2f})")
    print(f"\n  {'SYM':<10} {'SIG':>4} {'CURRENT':>12} {'TARGET':>12} {'DELTA':>12}")
    print(f"  {'-' * 54}")

    for sym in sorted(set(weights) | {s for s in portfolio if s != "USD"}):
        if sym == "USD":
            continue
        current = portfolio.get(sym, 0.0) * (prices.get(sym) or 0.0)
        target = weights.get(sym, 0.0) * total
        delta = target - current
        if abs(delta) < 0.01 and current == 0:
            continue
        sig = decisions.get(sym)
        print(f"  {sym:<10} {sig if sig is not None else '-':>4} {current:>12,.2f} {target:>12,.2f} {delta:>+12,.2f}")

    cash_target = weights.get("USD", 0.0) * total
    print(
        f"  {'USD':<10} {'-':>4} {portfolio.get('USD', 0.0):>12,.2f} {cash_target:>12,.2f} "
        f"{cash_target - portfolio.get('USD', 0.0):>+12,.2f}"
    )
    print(f"\n  invested after: {(1 - weights.get('USD', 0.0)) * 100:.1f}% of book")


if __name__ == "__main__":
    for factory in (GoldenButterflyMomBot, KronosTraderBot):
        try:
            preview(factory())
        except Exception as exc:
            print(f"\n!! {factory.__name__} preview failed: {exc}")
    print("\nNothing was written: rebalancePortfolio was never called.")
