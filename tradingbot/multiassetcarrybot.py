"""
MultiAssetCarryBot — carry in the two asset classes where it is directly observable.

Carry (Koijen/Moskowitz/Pedersen 2018) is the return an asset earns if its price
simply does not move. It is historically close to uncorrelated with trend, which
is the whole reason to run it alongside TSMOMTrendBot: "trend + carry" is a
two-line explanation, but only if the two sleeves genuinely diverge. Check the
realized correlation before believing it.

**How the carry is measured, using nothing but yfinance.**

FX forward carry. A currency ETF holds interest-bearing deposits in its
currency; the matching `=X` pair is pure spot. So over the same window:

    carry_i  ≈  return(FXi)  −  spot_return(pair_i)

which is the interest-rate differential minus the fund's expense ratio. The fee
is the same 0.40% for all six CurrencyShares funds, so it cancels entirely in a
cross-sectional ranking — the thing this bot actually uses. No rate feed, no
second data source, no key to rotate.

Bond carry. The term spread, ^TNX − ^IRX. On a Treasury future the roll yield
*is* the term spread net of financing, so this is the roll-yield leg the brief
asked for, measured on the cash curve instead of the futures curve.

**Commodity roll yield is deliberately absent.** It needs a futures curve, and
the framework has no source for one. The available hack — reading a front-month
ETF against a laddered one, e.g. USO versus USL — measures those two funds'
construction differences as much as the curve, and would put a number in a
backtest that nobody could defend. Left undone rather than faked; it wants IBKR
contract data.

**Long-only caveat.** KMP2018 goes long high-carry and short low-carry, levered.
Here the bottom of the ranking goes to cash rather than short, and gross is
capped at 1.0. This is the long half of carry, unlevered. Do not present a
backtest of it as a replication.

**The crash filter is not optional.** Carry's failure mode is famous and
one-directional: high-carry currencies pay a small premium for months and then
gap. A 12-month momentum filter on each leg — hold it only if it is also
trending up — is the cheapest known mitigation, and it is in the rules here
rather than left to judgement.

Schedule: 10 21 * * 1-5 — after the US close, before the 21:20 IBKR copier.
"""

import logging
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.vol_target import inverse_vol_weights, periods_per_year, realized_vol

logger = logging.getLogger(__name__)

# Currency ETF -> (spot pair, invert). Invert is True where Yahoo quotes the pair
# USD-first (USDJPY=X rises as the yen WEAKENS, while FXY rises as it strengthens),
# so without it three of the six carry estimates come out with the sign flipped —
# a bug that would look like a working strategy with an unlucky backtest.
FX_PAIRS: dict[str, tuple[str, bool]] = {
    "FXE": ("EURUSD=X", False),
    "FXB": ("GBPUSD=X", False),
    "FXA": ("AUDUSD=X", False),
    "FXC": ("USDCAD=X", True),
    "FXF": ("USDCHF=X", True),
    "FXY": ("USDJPY=X", True),
}

FX_ETFS = list(FX_PAIRS)
SPOT_PAIRS = [pair for pair, _ in FX_PAIRS.values()]

# Bond legs and the two yield series that decide whether they are held.
BOND_ETFS = ["IEF", "TLT"]
YIELD_10Y = "^TNX"
YIELD_3M = "^IRX"


class MultiAssetCarryBot(Bot):
    """
    Long the highest-carry currencies and, when the curve pays, the bond legs.

    Args:
        carry_window: Trading days over which carry is measured. ~63 is three
            months: long enough that the interest differential dominates the
            tracking noise between an ETF and its spot pair, short enough to
            still reflect the current rate regime.
        n_fx: How many of the six currencies to hold. 3 of 6 is the usual
            top-half construction; holding more dilutes the signal, fewer makes
            the sleeve a bet on one central bank.
        momentum_days: Lookback for the crash filter. A leg must have positive
            total return over this window to be held, however well it ranks on
            carry.
        fx_gross / bond_gross: Budget for each sleeve. They sum to 1.0, but each
            sleeve independently uses less when its vol target says so, so the
            book is usually part cash.
    """

    param_grid: ClassVar[dict] = {
        "carry_window": [42, 63, 126],
        "n_fx": [2, 3, 4],
        "target_vol": [0.04, 0.06, 0.08],
    }

    # The momentum filter needs a year of history before the bot can hold
    # anything; the "1d" default of "1y" would leave almost no evaluable bars.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        carry_window: int = 63,
        n_fx: int = 3,
        momentum_days: int = 252,
        target_vol: float = 0.06,
        vol_window: int = 60,
        fx_gross: float = 0.60,
        bond_gross: float = 0.40,
        **kwargs,
    ):
        super().__init__(
            "MultiAssetCarryBot",
            tickers=[*FX_ETFS, *BOND_ETFS, *SPOT_PAIRS, YIELD_10Y, YIELD_3M],
            # The spot pairs and the two yield series are inputs, not positions.
            # benchmark_tickers is exactly the mechanism for that: they are
            # fetched and visible in self.datas, excluded from the sizing base,
            # and can never receive weight.
            benchmark_tickers=[*SPOT_PAIRS, YIELD_10Y, YIELD_3M],
            interval="1d",
            period="2y",
            carry_window=carry_window,
            n_fx=n_fx,
            momentum_days=momentum_days,
            target_vol=target_vol,
            vol_window=vol_window,
            fx_gross=fx_gross,
            bond_gross=bond_gross,
            **kwargs,
        )
        self.carry_window = carry_window
        self.n_fx = n_fx
        self.momentum_days = momentum_days
        self.target_vol = target_vol
        self.vol_window = vol_window
        self.fx_gross = fx_gross
        self.bond_gross = bond_gross

    # ------------------------------------------------------------------
    # Signal components
    # ------------------------------------------------------------------

    def _closes(self, ticker: str) -> pd.Series | None:
        frame = self.datas.get(ticker)
        if frame is None or frame.empty or "close" not in frame:
            return None
        closes = frame["close"].dropna()
        return closes if len(closes) else None

    def _window_return(self, closes: pd.Series, window: int, invert: bool = False) -> float | None:
        """Total return over `window` bars, or None if history is short."""
        if len(closes) < window + 1:
            return None
        past = float(closes.iloc[-1 - window])
        now = float(closes.iloc[-1])
        if past <= 0 or now <= 0:
            return None
        if invert:
            # The pair is quoted USD-first, so the currency's own return is the
            # reciprocal series' return.
            past, now = 1.0 / past, 1.0 / now
        return now / past - 1.0

    def _fx_carry(self) -> dict[str, float]:
        """Estimated annualized-equivalent carry per currency ETF, in return terms."""
        carry: dict[str, float] = {}
        for etf, (pair, invert) in FX_PAIRS.items():
            etf_closes = self._closes(etf)
            spot_closes = self._closes(pair)
            if etf_closes is None or spot_closes is None:
                continue
            etf_return = self._window_return(etf_closes, self.carry_window)
            spot_return = self._window_return(spot_closes, self.carry_window, invert=invert)
            if etf_return is None or spot_return is None:
                continue
            carry[etf] = etf_return - spot_return
        return carry

    def _bond_carry_positive(self) -> bool:
        """True when the curve is upward-sloping, i.e. the roll pays."""
        ten = self._closes(YIELD_10Y)
        three = self._closes(YIELD_3M)
        if ten is None or three is None:
            return False
        # Both series are quoted in percent, so the difference is in percentage
        # points and only its sign is used.
        return float(ten.iloc[-1]) - float(three.iloc[-1]) > 0

    def _sleeve(self, candidates: list[str], gross: float, ppy: float) -> dict[str, float]:
        """Vol-target `candidates` into a `gross` budget."""
        vols: dict[str, float] = {}
        for ticker in candidates:
            closes = self._closes(ticker)
            if closes is None:
                continue
            vol = realized_vol(closes, window=self.vol_window, periods_per_year=ppy)
            if vol > 0:
                vols[ticker] = vol
        if not vols:
            return {}
        return inverse_vol_weights(vols, target_vol=self.target_vol, max_leg=gross, max_gross=gross)

    # ------------------------------------------------------------------

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        ppy = periods_per_year(self.interval)

        # --- FX sleeve: rank on carry, then survive the crash filter ---
        carry = self._fx_carry()
        ranked = sorted(carry, key=lambda t: carry[t], reverse=True)[: self.n_fx]

        fx_candidates = []
        for ticker in ranked:
            closes = self._closes(ticker)
            if closes is None:
                continue
            momentum = self._window_return(closes, self.momentum_days)
            if momentum is None:
                continue  # still warming up
            if momentum <= 0:
                logger.info(
                    "%s: %s ranks top-%d on carry (%.2f%%) but its %dd momentum is %.2f%% — filtered out",
                    self.bot_name,
                    ticker,
                    self.n_fx,
                    carry[ticker] * 100,
                    self.momentum_days,
                    momentum * 100,
                )
                continue
            fx_candidates.append(ticker)

        # --- Bond sleeve: on only when the curve pays ---
        bond_candidates = BOND_ETFS if self._bond_carry_positive() else []

        weights = {
            **self._sleeve(fx_candidates, self.fx_gross, ppy),
            **self._sleeve(bond_candidates, self.bond_gross, ppy),
        }
        if not weights:
            logger.info("%s: no leg passes carry + filter — holding cash", self.bot_name)
            return {}

        logger.info(
            "%s: FX %s, bonds %s, gross %.1f%%",
            self.bot_name,
            fx_candidates or "none",
            bond_candidates or "none",
            sum(weights.values()) * 100,
        )
        return weights


if __name__ == "__main__":
    # bot = MultiAssetCarryBot()
    # bot.local_development(objective="sharpe_ratio", param_sample_ratio=0.3)
    run_bot(MultiAssetCarryBot)
