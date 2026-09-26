"""
Daily option-chain capture: builds the option history yfinance does not serve.

yfinance has no historical chains; every past chain is lost unless someone
stored it on the day. This captures OPTION_CAPTURE_UNIVERSE (SPY, QQQ and the
50 largest S&P 100 names) once a day into `option_quotes`, the table the
option bots already write, so that skew, term-structure and earnings-IV
strategies can one day be backtested on observed prices instead of a model.

What is kept, to hold the table to ~1.5-2 GB a year on a node with ~18 GB free:
- Expiries: the listed expiry nearest each of CAPTURE_DTES (7 days .. 18
  months), about nine per name — enough to interpolate a constant-maturity
  term structure, without the dozens of weeklies in between.
- Strikes: within moneyness_band(T) of spot, widening with time to expiry
  (+/-16% at a week, +/-25% at a month, +/-60% at a year and beyond).
- Only a LIVE chain (marketState REGULAR). Off-hours yfinance zeroes bid/ask
  and reports IV as 1e-5, which is not worth keeping; a closed market (a
  holiday) ends the run cleanly with nothing written.

Each row carries `underlying_price` from the same fetch, so moneyness and IV
can be recomputed later. No scheduling or env parsing here — see
tradingbot/optionchainsnapshot.py.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import yfinance as yf

from . import options

logger = logging.getLogger(__name__)

# Target days to expiry; each maps to the nearest listed expiry.
CAPTURE_DTES: tuple[int, ...] = (7, 14, 30, 45, 60, 90, 180, 365, 540)


def moneyness_band(T: float) -> float:
    """Strikes kept: spot x (1 +/- band), band = 0.08 + 0.6 sqrt(T), within [0.10, 0.60]."""
    return min(max(0.08 + 0.6 * math.sqrt(max(T, 0.0)), 0.10), 0.60)


def capture_expiries(listed: Sequence[date], today: date, dtes: Sequence[int] = CAPTURE_DTES) -> list[date]:
    """For each target DTE the listed expiry nearest it (at least 1 day out), deduplicated, sorted."""
    future = sorted(e for e in listed if (e - today).days >= 1)
    if not future:
        return []
    return sorted({min(future, key=lambda e, d=d: abs((e - today).days - d)) for d in dtes})


@dataclass
class CaptureResult:
    written: dict[str, int] = field(default_factory=dict)  # symbol -> rows stored
    failed: dict[str, str] = field(default_factory=dict)  # symbol -> error
    market_closed: bool = False

    @property
    def rows(self) -> int:
        return sum(self.written.values())


def capture_symbol(symbol: str, today: date, pause: float = 0.3) -> int:
    """Store today's live chain slices for one symbol. Returns rows written."""
    ticker = yf.Ticker(symbol)
    listed = [date.fromisoformat(e) for e in ticker.options]
    rows = 0
    for expiry in capture_expiries(listed, today):
        band = moneyness_band((expiry - today).days / 365.0)
        frame = options.fetch_option_chain(symbol, expiry, ticker=ticker, moneyness=band, require_live=True)
        rows += len(frame)
        time.sleep(pause)
    return rows


def capture_universe(
    symbols: Sequence[str],
    today: date | None = None,
    pause: float = 0.3,
    retries: int = 2,
    backoff: float = 5.0,
) -> CaptureResult:
    """
    Capture every symbol, retrying each a couple of times with backoff (yfinance
    rate-limits bursts). One symbol failing never stops the others; a closed
    market stops the run, since every symbol would be closed too.
    """
    today = today or options.utc_today()
    result = CaptureResult()
    for i, symbol in enumerate(symbols):
        for attempt in range(retries + 1):
            try:
                result.written[symbol] = capture_symbol(symbol, today, pause)
                result.failed.pop(symbol, None)
                break
            except options.MarketClosedError as e:
                logger.info("Market not open (%s); nothing captured", e)
                result.market_closed = True
                return result
            except Exception as e:
                result.failed[symbol] = f"{type(e).__name__}: {e}"
                logger.warning("Capture %s attempt %d failed: %s", symbol, attempt + 1, e)
                if attempt < retries:
                    time.sleep(backoff * (attempt + 1))
        logger.info("[%d/%d] %s: %s rows", i + 1, len(symbols), symbol, result.written.get(symbol, "failed"))
    return result
