"""TS151 Roll Yield Bot (§9.1).

Source: Kakushadze & Serur (2018) §9.1 "Roll yield".
Forward-only strategy (not backtestable with historical continuous contracts because
continuous contracts eliminate price jumps at rolls).

Spec:
Long commodities in backwardation (F1 > F2, positive roll yield), short commodities in contango
(F1 < F2, negative roll yield). Roll yield = log(F1 / F2) * (12 / months_apart).
Trades ETF proxies: USO, UNG, GLD, SLV, CPER, CORN, WEAT, SOYB, CANE.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, ClassVar

import yfinance as yf

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.common import is_live_as_of
from tradingbot.utils.ts151.signals_commod import roll_yield_scores, topk_bottomk

logger = logging.getLogger(__name__)

COMMODITY_FUTURES_MAP: dict[str, tuple[str, str]] = {
    "CL": ("USO", "NYM"),
    "NG": ("UNG", "NYM"),
    "GC": ("GLD", "CMX"),
    "SI": ("SLV", "CMX"),
    "HG": ("CPER", "CMX"),
    "ZC": ("CORN", "CBT"),
    "ZW": ("WEAT", "CBT"),
    "ZS": ("SOYB", "CBT"),
    "SB": ("CANE", "NYB"),
}

MONTH_CODES: dict[int, str] = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}


def fetch_live_contracts() -> dict[str, tuple[float, float, float]]:
    """Fetch live front and second month futures quotes per commodity via yfinance.

    Returns dict mapping ETF ticker -> (f1, f2, months_apart).
    Raises RuntimeError if fewer than 4 commodities resolve.
    """
    now = datetime.now()
    candidate_tickers: dict[str, list[str]] = {}
    ticker_meta: dict[str, tuple[str, str, int, int]] = {}

    for sym, (etf, exc) in COMMODITY_FUTURES_MAP.items():
        candidate_tickers[sym] = []
        for i in range(1, 8):
            m = ((now.month - 1 + i) % 12) + 1
            y = now.year + ((now.month - 1 + i) // 12)
            yy = str(y)[-2:]
            code = MONTH_CODES[m]
            t = f"{sym}{code}{yy}.{exc}"
            candidate_tickers[sym].append(t)
            ticker_meta[t] = (sym, etf, y, m)

    all_tickers = [t for list_t in candidate_tickers.values() for t in list_t]
    try:
        data = yf.download(all_tickers, period="5d", progress=False)
        closes = data.get("Close", data)
    except Exception as exc:
        logger.warning("Failed to fetch live futures quotes from yfinance: %s", exc)
        return {}

    resolved: dict[str, tuple[float, float, float]] = {}
    for sym, (etf, _exc) in COMMODITY_FUTURES_MAP.items():
        found: list[tuple[str, float, int, int]] = []
        for t in candidate_tickers[sym]:
            if t in closes:
                series = closes[t].dropna()
                if not series.empty:
                    last_price = float(series.iloc[-1])
                    if last_price > 0:
                        _, _, y, m = ticker_meta[t]
                        found.append((t, last_price, y, m))
        if len(found) >= 2:
            _f1_t, f1, y1, m1 = found[0]
            _f2_t, f2, y2, m2 = found[1]
            m_apart = (y2 - y1) * 12 + (m2 - m1)
            if m_apart > 0:
                resolved[etf] = (f1, f2, float(m_apart))

    if len(resolved) < 4:
        raise RuntimeError(f"Fewer than 4 commodities resolved live contract quotes (resolved {len(resolved)})")

    return resolved


class TS151RollYieldBot(TS151Bot):
    """Roll Yield strategy (§9.1). Forward-only (not backtestable).

    Docstring:
    Kakushadze & Serur (2018) §9.1 "Roll yield".
    In backtest (no live contract quotes), returns None.
    In live trading, queries front and second listed futures contracts from yfinance,
    calculates roll yield = log(F1 / F2) * 12 / months_apart, and holds top-k longs
    and bottom-k shorts in corresponding commodity ETFs.
    """

    BOT_NAME: str = "ts151_RollYieldBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = (
        "USO",
        "UNG",
        "GLD",
        "SLV",
        "CPER",
        "CORN",
        "WEAT",
        "SOYB",
        "CANE",
    )
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    NEEDS_NETWORK: bool = True
    NEEDS_DB: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "k": 3,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "k": [2, 3],
        "gross": [1.0, 2.0],
    }

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Calculate target weights from live futures roll yield.

        Returns None during backtesting since individual futures contracts are forward-only.
        """
        if not is_live_as_of(ctx.as_of):
            return None

        # When running in backtest or when network/live quotes unavailable, return None
        try:
            contracts = fetch_live_contracts()
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("Could not obtain live contracts: %s", exc)
            return None

        if not contracts:
            return None

        scores = roll_yield_scores(contracts)
        if len(scores) < 2 * self.k:
            return None

        return topk_bottomk(
            scores,
            k=self.k,
            long_only=self.long_only,
            gross=self.gross,
        )
