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

which is the currency's own deposit rate net of the fund's expense ratio. Note
what this is NOT: it is a *level*, not a differential against USD. Getting that
wrong is what broke the first version of this bot — see below. No rate feed, no
second data source, no key to rotate.

**Carry must clear USD cash, not merely rank well.** The original design ranked
the six and held the top three. That loses money by construction, and not
because of any parameter: for a long-only book the alternative to holding a
foreign currency is holding USD, so the sleeve was permanently short USD carry.
Through 2023-24, with the 3-month bill at 5.24%, all six currencies carried
below USD (best: CAD at +1.47%) and the bot held three of them anyway. The gate
is therefore absolute — `carry_i > USD cash + carry_margin`, USD cash read off
^IRX over the same window — and the sleeve is simply empty whenever nothing
clears it. Sitting in cash is the correct answer to "no currency pays more than
cash", and the ranking still decides which of the survivors to hold.

**Why the window is a year.** Measured against known 3-month market rates over
2023-24, this estimator's mean absolute error is 4.4pp at a 63-day window and
2.1pp at 252 days — the ETF/spot snapshot mismatch is large next to a quarter's
interest accrual, and annualizing a short window multiplies it. 63 days is fine
for ranking, where a common bias cancels, and useless for a threshold. The
residual ~1.5pp by which the estimate sits below policy rates is mostly not
error at all: it is the 0.40% fee plus deposit rates below the policy benchmark,
i.e. real drag you pay for using the vehicle, which is exactly what belongs in a
comparison against T-bills. `carry_margin` covers what is left.

Bond carry. The term spread, ^TNX − ^IRX. On a Treasury future the roll yield
*is* the term spread net of financing, so this is the roll-yield leg the brief
asked for, measured on the cash curve instead of the futures curve. The spread
being positive already *is* the "beats cash" test for this sleeve, since ^IRX is
the cash leg of it.

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
rather than left to judgement. It applies to **every** leg including the bonds:
a positive term spread says the roll pays, but duration P&L swamps the roll, and
2022 is the standing example — the curve was upward-sloping into a year that
took TLT down 31%.

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
        carry_window: Trading days over which carry is measured. A year, not a
            quarter: the estimate is compared against an absolute threshold, and
            at 63 days its error (4.4pp) dwarfs the thing being measured. See
            the module docstring.
        carry_margin: How far above USD cash a currency's carry must sit before
            it is worth holding, in annualized return terms. Absorbs the
            residual estimator noise and the round-trip cost, so the sleeve does
            not switch on for a few basis points of apparent edge.
        n_fx: Cap on how many of the six currencies to hold. 3 of 6 is the usual
            top-half construction; holding more dilutes the signal, fewer makes
            the sleeve a bet on one central bank. It is a cap and not a target —
            far fewer clear the absolute gate in a high-USD-rate regime, and in
            2023-24 none of them did.
        momentum_days: Lookback for the crash filter. A leg must have positive
            total return over this window to be held, however well it ranks on
            carry.
        fx_gross / bond_gross: Budget for each sleeve. They sum to 1.0, but each
            sleeve independently uses less when its vol target says so, so the
            book is usually part cash.
    """

    param_grid: ClassVar[dict] = {
        "carry_window": [189, 252, 378],
        "carry_margin": [0.0, 0.005, 0.01],
        "n_fx": [2, 3, 4],
    }

    # The momentum filter needs a year of history before the bot can hold
    # anything; the "1d" default of "1y" would leave almost no evaluable bars.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        carry_window: int = 252,
        carry_margin: float = 0.005,
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
            carry_margin=carry_margin,
            n_fx=n_fx,
            momentum_days=momentum_days,
            target_vol=target_vol,
            vol_window=vol_window,
            fx_gross=fx_gross,
            bond_gross=bond_gross,
            **kwargs,
        )
        self.carry_window = carry_window
        self.carry_margin = carry_margin
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

    def _usd_cash_carry(self) -> float | None:
        """
        What USD cash paid over the carry window, in the same units as _fx_carry.

        This is the hurdle every FX leg has to clear, because it is literally the
        alternative: weights that do not go to a currency stay in USD. ^IRX is
        the 3-month bill yield quoted in percent, so its mean over the window
        scaled by window/year is the return cash earned across the same bars.
        """
        three = self._closes(YIELD_3M)
        if three is None or len(three) < self.carry_window + 1:
            return None
        window_yield = three.iloc[-self.carry_window - 1 :]
        return float(window_yield.mean()) / 100.0 * (self.carry_window / 252.0)

    def _bond_carry_positive(self) -> bool:
        """True when the curve is upward-sloping, i.e. the roll pays."""
        ten = self._closes(YIELD_10Y)
        three = self._closes(YIELD_3M)
        if ten is None or three is None:
            return False
        # Both series are quoted in percent, so the difference is in percentage
        # points and only its sign is used.
        return float(ten.iloc[-1]) - float(three.iloc[-1]) > 0

    def _passes_momentum(self, ticker: str) -> bool:
        """
        The crash filter, applied identically to every leg in both sleeves.

        Returns False while still warming up, so a leg is held only once there is
        enough history to have actually checked it.
        """
        closes = self._closes(ticker)
        if closes is None:
            return False
        momentum = self._window_return(closes, self.momentum_days)
        if momentum is None:
            return False
        if momentum <= 0:
            logger.info(
                "%s: %s filtered out — %dd momentum %.2f%%",
                self.bot_name,
                ticker,
                self.momentum_days,
                momentum * 100,
            )
            return False
        return True

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

        # --- FX sleeve: clear USD cash first, then rank, then the crash filter ---
        # Order matters. Ranking first and gating second would still hold the
        # least-bad currency in a regime where USD out-carries all six; the
        # absolute hurdle has to come first so the sleeve can be empty.
        carry = self._fx_carry()
        usd_carry = self._usd_cash_carry()
        eligible: dict[str, float]
        if usd_carry is None:
            logger.info("%s: no USD cash rate yet — FX sleeve stays flat", self.bot_name)
            eligible = {}
        else:
            hurdle = usd_carry + self.carry_margin * (self.carry_window / 252.0)
            eligible = {t: c for t, c in carry.items() if c > hurdle}
            if carry and not eligible:
                logger.info(
                    "%s: no currency out-carries USD cash (hurdle %.2f%%, best %s at %.2f%%) — FX sleeve flat",
                    self.bot_name,
                    hurdle * 100,
                    max(carry, key=lambda t: carry[t]),
                    max(carry.values()) * 100,
                )

        ranked = sorted(eligible, key=lambda t: eligible[t], reverse=True)[: self.n_fx]
        fx_candidates = [t for t in ranked if self._passes_momentum(t)]

        # --- Bond sleeve: the curve must pay AND the leg must be trending ---
        bond_candidates = [t for t in BOND_ETFS if self._passes_momentum(t)] if self._bond_carry_positive() else []

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
