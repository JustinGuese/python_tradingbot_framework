"""
One day's implied-vol surface, interpolated from whatever that day's chain holds.

Built for replaying sampled histories. The DoltHub SPY/AAPL backfill in
`option_quotes` stores ~3 expiries x ~22 strikes a day, and the set changes
daily: a leg a strategy opened is quoted again on about 1 day in 20, and its
expiry on about 1 in 4. Marking a held position at the last recorded quote
leaves it frozen at its entry price, so the replay measures the gaps in the
data rather than the strategy. A held leg is instead priced off that day's
surface:

- IV per contract is solved from its bid/ask mid with the same r and q the
  bots use (options.with_greeks), out-of-the-money side only.
- Moneyness is standardised, z = ln(K / F) / T**0.35, so a smile steepens as
  expiry nears. At fixed ln(K / F) a far wing carried into the last weeks was
  marked ~5 vol points too low (held-out test, SPY 2019-26; see
  MONEYNESS_POWER).
- Within an expiry: IV linear in z, flat beyond the quoted strikes.
- Across expiries: total variance iv^2 T linear in T at fixed z; before the
  first and after the last expiry, that slice's IV at the same z (a held leg
  drifting inside the shortest bucket is the common case).
- The spread is the median half-spread of the day's quotes nearest in price,
  so a cheap wing gets a wing's spread.

The fills this produces are model fills. What is real is the level, skew and
term structure of the day's vols, which is what the synthetic backtests
(^VIX x 0.85 with a calibrated skew) had to assume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from . import option_math as om
from . import options

MIN_POINTS = 3  # strikes an expiry needs to count as a slice
SPREAD_NEIGHBOURS = 7
MIN_HALF_SPREAD = 0.005
IV_BOUNDS = (0.01, 3.0)
# Moneyness is ln(K / F) / T**MONEYNESS_POWER. Chosen on SPY 2019-26 by holding
# out one expiry a day and predicting it from the rest (median IV bias):
#   power 0    near-expiry wings -4.8 vol pts, 5-15 delta -0.5
#   power 0.5  near-expiry wings +0.2,         5-15 delta +1.1
#   power 0.35 near-expiry wings -1.2,         5-15 delta +0.5, interpolation within +-0.3
MONEYNESS_POWER = 0.35


@dataclass(frozen=True)
class Slice:
    T: float
    z: np.ndarray  # standardised moneyness ln(K / F) / T**MONEYNESS_POWER, ascending
    iv: np.ndarray  # implied vol at z

    def vol(self, z: float) -> float:
        return float(np.interp(z, self.z, self.iv))  # clamps: flat IV past the quoted strikes


@dataclass(frozen=True)
class DaySurface:
    spot: float
    r: float
    q: float
    day: date
    slices: tuple[Slice, ...]  # by T, ascending
    mids: np.ndarray  # log mid of every two-sided quote, ascending
    half_spreads: np.ndarray  # matching half-spreads

    def forward(self, T: float) -> float:
        return self.spot * math.exp((self.r - self.q) * T)

    def iv(self, strike: float, T: float) -> float:
        """Implied vol at (strike, T): total variance interpolated in T at fixed standardised moneyness."""
        z = math.log(strike / self.forward(T)) / T**MONEYNESS_POWER
        first, last = self.slices[0], self.slices[-1]
        if T <= first.T:
            return first.vol(z)
        if T >= last.T:
            return last.vol(z)
        i = next(j for j, s in enumerate(self.slices) if s.T >= T)
        lo, hi = self.slices[i - 1], self.slices[i]
        x = (T - lo.T) / (hi.T - lo.T)
        w = (1 - x) * lo.vol(z) ** 2 * lo.T + x * hi.vol(z) ** 2 * hi.T
        return math.sqrt(max(w, 1e-12) / T)

    def half_spread(self, price: float) -> float:
        if not len(self.mids) or price <= 0:
            return MIN_HALF_SPREAD
        pos = int(np.searchsorted(self.mids, math.log(price)))
        lo = max(0, pos - SPREAD_NEIGHBOURS // 2)
        near = self.half_spreads[lo : lo + SPREAD_NEIGHBOURS]
        return max(float(np.median(near)), MIN_HALF_SPREAD)

    def quote(self, right: str, strike: float, expiry: date) -> tuple[float, float, float]:
        """(bid, ask, mid) of a contract the chain did not record, off the surface."""
        T = om.year_fraction(expiry, self.day)
        intrinsic = max(0.0, self.spot - strike) if right == "C" else max(0.0, strike - self.spot)
        if T <= 0:
            return intrinsic, intrinsic, intrinsic
        mid = max(om.bs_price(self.spot, strike, T, self.r, self.iv(strike, T), right, self.q), intrinsic)
        half = self.half_spread(mid)
        return max(mid - half, 0.0), mid + half, mid


def build_surface(
    underlying: str, chain: pd.DataFrame, spot: float, day: date, r: float | None = None, q: float | None = None
) -> DaySurface | None:
    """The surface of one day's stored chain (option_quotes rows with an `expiry` column), or None if too thin."""
    r = options.risk_free_rate() if r is None else r
    q = options.dividend_yield(underlying) if q is None else q
    slices, mids, spreads = [], [], []
    for expiry, g in chain.groupby("expiry"):
        view = options.ChainView(underlying, expiry, g.reset_index(drop=True), spot, True, day)
        T = view.T
        if T <= 0:
            continue
        forward = spot * math.exp((r - q) * T)
        points = []
        for right in ("P", "C"):
            side = options.with_greeks(view, right, r, q)
            if side.empty:
                continue
            bid = pd.to_numeric(side["bid"], errors="coerce")
            ask = pd.to_numeric(side["ask"], errors="coerce")
            two_sided = (bid > 0) & (ask >= bid)
            mids.extend(np.log(((bid + ask) / 2)[two_sided].to_numpy()))
            spreads.extend(((ask - bid) / 2)[two_sided].to_numpy())
            otm = side[(side["strike"] < forward) if right == "P" else (side["strike"] >= forward)]
            otm = otm[otm["iv"].between(*IV_BOUNDS)]
            points += [
                (math.log(float(k) / forward) / T**MONEYNESS_POWER, float(v))
                for k, v in zip(otm["strike"], otm["iv"], strict=True)
            ]
        if len(points) < MIN_POINTS:
            continue
        pts = pd.DataFrame(points, columns=["z", "iv"]).groupby("z", as_index=False)["iv"].mean().sort_values("z")
        slices.append(Slice(T, pts["z"].to_numpy(), pts["iv"].to_numpy()))
    if not slices:
        return None
    order = np.argsort(mids)
    return DaySurface(
        spot,
        r,
        q,
        day,
        tuple(sorted(slices, key=lambda s: s.T)),
        np.asarray(mids)[order],
        np.asarray(spreads)[order],
    )
