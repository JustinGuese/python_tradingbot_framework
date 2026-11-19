"""
SignedFxCarryBot — Dollar-neutral long/short FX carry strategy on 9 spot FX pairs.

Implements PLAN-carryblend.md S2:
- Universe: 9 spot FX pairs:
    Direct pairs (quote currency is USD, base is foreign):
      EURUSD=X (EUR), GBPUSD=X (GBP), AUDUSD=X (AUD), NZDUSD=X (NZD).
      Positive rate differential (r_foreign > r_usd) means long foreign ccy -> LONG the pair.
    Inverted pairs (base currency is USD, quote is foreign):
      USDJPY=X (JPY), USDCAD=X (CAD), USDCHF=X (CHF), USDNOK=X (NOK), USDSEK=X (SEK).
      Positive rate differential (r_foreign > r_usd) means long foreign ccy -> SHORT the pair
      (reversed sign for foreign carry: score = r_usd - r_foreign).
- ALLOW_SHORT = True
- SHORTABLE = ("EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X", "USDJPY=X", "USDCAD=X", "USDCHF=X", "USDNOK=X", "USDSEK=X")
- CARRY_ACCRUAL = True
- Rebalance monthly on first bar of the calendar month (or when flat live).
- Ranking and sizing via signed_topk_weights from tradingbot.utils.fx_carry
  (each side gross/2, equal weight).
- carry_rates(self, ts) returns annual rates per held symbol + "USD" collateral credit,
  deducting swap markup.
- Parameters: k=3 (grid: 2, 3, 4), gross=1.0 (grid: 1.0, 1.5), markup=0.0 (grid: 0.0, 0.005, 0.01).
- Returns {} if history is too short (< 2 bars) or fewer than 2*k valid differentials.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.fx_carry import (
    DIRECT_PAIRS,
    INVERT_PAIRS,
    load_fred_rates,
    signed_topk_weights,
)
from tradingbot.utils.live_state import live_holdings
from tradingbot.utils.quantsmd_common import closes_frame, period_rolled
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

ALL_FX_PAIRS: tuple[str, ...] = (
    "EURUSD=X",
    "GBPUSD=X",
    "AUDUSD=X",
    "NZDUSD=X",
    "USDJPY=X",
    "USDCAD=X",
    "USDCHF=X",
    "USDNOK=X",
    "USDSEK=X",
)


def compute_signed_fx_carry_weights(
    rates_row: pd.Series,
    k: int = 3,
    gross: float = 1.0,
    available_tickers: Sequence[str] | None = None,
) -> dict[str, float]:
    """
    Compute dollar-neutral long/short weights for the 9 spot FX pairs from 3-month short rates.

    Pairs are scored by expected directional carry:
    - Direct pairs (EURUSD=X, GBPUSD=X, AUDUSD=X, NZDUSD=X):
        score = r_foreign - r_usd. Positive score -> buy foreign ccy (long pair).
    - Inverted pairs (USDJPY=X, USDCAD=X, USDCHF=X, USDNOK=X, USDSEK=X):
        score = r_usd - r_foreign (reversed sign of foreign carry).
        Positive score -> foreign yield is lower than USD, buy USD / sell foreign ccy (long pair).
        Negative score -> foreign yield is higher than USD, sell USD / buy foreign ccy (short pair).

    Long top-k, short bottom-k using signed_topk_weights (each side gross/2, equal weight).
    Returns {} if fewer than 2*k valid pair differentials are available.
    """
    if k <= 0 or "USD" not in rates_row or pd.isna(rates_row["USD"]):
        return {}

    r_usd = float(rates_row["USD"])
    pair_scores: dict[str, float] = {}

    allowed = set(available_tickers) if available_tickers is not None else set(ALL_FX_PAIRS)

    # Direct pairs: score = r_foreign - r_usd
    for pair, ccy in DIRECT_PAIRS.items():
        if pair not in allowed:
            continue
        if ccy in rates_row and not pd.isna(rates_row[ccy]):
            pair_scores[pair] = float(rates_row[ccy]) - r_usd

    # Inverted pairs: score = r_usd - r_foreign (reversed sign for foreign carry)
    for pair, ccy in INVERT_PAIRS.items():
        if pair not in allowed:
            continue
        if ccy in rates_row and not pd.isna(rates_row[ccy]):
            pair_scores[pair] = r_usd - float(rates_row[ccy])

    if len(pair_scores) < 2 * k:
        return {}

    return signed_topk_weights(pd.Series(pair_scores), k=k, gross=gross)


class SignedFxCarryBot(Bot):
    """
    Dollar-neutral long/short FX carry strategy across 9 G10 spot pairs vs USD.

    Ranks spot pairs by interest rate carry differential (with inverted pairs properly sign-flipped).
    Holds the top-k pairs long and bottom-k pairs short, rebalanced monthly.
    Accrues carry on held positions and credits cash collateral the US 3m rate.
    """

    PAIRS: ClassVar[tuple[str, ...]] = ALL_FX_PAIRS
    DIRECT_PAIRS: ClassVar[dict[str, str]] = DIRECT_PAIRS
    INVERT_PAIRS: ClassVar[dict[str, str]] = INVERT_PAIRS

    ALLOW_SHORT: ClassVar[bool] = True
    SHORTABLE: ClassVar[tuple[str, ...]] = ALL_FX_PAIRS
    MAX_GROSS: ClassVar[float] = 2.0
    CARRY_ACCRUAL: ClassVar[bool] = True
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "k": [2, 3, 4],
        "gross": [1.0, 1.5],
        "markup": [0.0, 0.005, 0.01],
    }

    def __init__(
        self,
        k: int = 3,
        gross: float = 1.0,
        markup: float = 0.0,
        lag_days: int = 25,
        rates: pd.DataFrame | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            "SignedFxCarryBot",
            tickers=list(self.PAIRS),
            interval="1d",
            period="max",
            k=k,
            gross=gross,
            markup=markup,
            lag_days=lag_days,
            **kwargs,
        )
        self.k = int(k)
        self.gross = float(gross)
        self.markup = float(markup)
        self.lag_days = int(lag_days)
        self._rates: pd.DataFrame | None = rates
        self._current_weights: dict[str, float] = {}

    def _get_rates(self) -> pd.DataFrame:
        """Return the rates DataFrame, loading cached FRED rates if not provided."""
        if self._rates is None:
            self._rates = load_fred_rates(lag_days=self.lag_days)
        return self._rates

    def _get_rates_row(self, ts: Any) -> pd.Series | None:
        """Look up the most recent lagged rates row as of normalized timestamp `ts`."""
        try:
            ts_dt = pd.Timestamp(ts).tz_localize(None).normalize()
        except Exception:
            return None

        rates_df = self._get_rates()
        if rates_df.empty:
            return None

        if ts_dt < rates_df.index[0]:
            return None

        row = rates_df.asof(ts_dt)
        if row is None or row.isna().all():
            return None

        return row

    def targetWeights(self, rows: dict[str, pd.Series] | None = None) -> dict[str, float] | None:
        """
        Return target weights for the 9-pair FX carry long/short portfolio.

        Rebalances on the first bar of each calendar month or when the portfolio is flat live.
        Returns None mid-month when holdings are already present.
        Returns {} if history has fewer than 2 bars or fewer than 2*k valid differentials.
        """
        closes = closes_frame(self.datas, self.tickers)
        if closes.empty or len(closes) < 2 or len(closes.columns) < 2 * self.k:
            return {}

        holdings = live_holdings(self, rows or {})
        is_flat = holdings is not None and not holdings
        month_rolled = period_rolled(closes.index, "M")

        # Monthly rebalance gate: only rebalance on first bar of month or when flat live
        if not month_rolled and not is_flat:
            return None

        current_ts = closes.index[-1]
        rates_row = self._get_rates_row(current_ts)
        if rates_row is None:
            return {}

        weights = compute_signed_fx_carry_weights(
            rates_row=rates_row,
            k=self.k,
            gross=self.gross,
            available_tickers=list(closes.columns),
        )

        if weights:
            self._current_weights = dict(weights)

        return weights

    def carry_rates(self, ts: Any = None) -> dict[str, float]:
        """
        Return annual carry rate per held spot FX symbol, plus 'USD' cash collateral credit.

        For direct pairs: diff = r_foreign - r_usd.
        For inverted pairs: diff = r_usd - r_foreign (reversed sign).
        Swap markup is deducted from earned return:
        - Long position (holding > 0): rate = diff - markup.
        - Short position (holding < 0): rate = diff + markup.
          (In backtest, holding * price * rate yields net interest (|diff| - markup) * notional).
        - 'USD': US 3m short rate (decimal) to credit idle/collateral cash.
        """
        if ts is None:
            ts = pd.Timestamp.now().normalize()

        rates_row = self._get_rates_row(ts)
        if rates_row is None or "USD" not in rates_row or pd.isna(rates_row["USD"]):
            return {}

        r_usd = float(rates_row["USD"])
        rates: dict[str, float] = {"USD": r_usd}

        # Resolve held weights from instance cache or live dbBot portfolio
        held_weights = dict(self._current_weights)
        if not held_weights and hasattr(self, "dbBot") and self.dbBot and getattr(self.dbBot, "portfolio", None):
            port = self.dbBot.portfolio
            if isinstance(port, dict):
                for sym, qty in port.items():
                    if sym != "USD" and qty:
                        held_weights[sym] = 1.0 if float(qty) > 0 else -1.0

        for pair in self.tickers:
            if pair in self.DIRECT_PAIRS:
                ccy = self.DIRECT_PAIRS[pair]
                if ccy not in rates_row or pd.isna(rates_row[ccy]):
                    continue
                diff = float(rates_row[ccy]) - r_usd
            elif pair in self.INVERT_PAIRS:
                ccy = self.INVERT_PAIRS[pair]
                if ccy not in rates_row or pd.isna(rates_row[ccy]):
                    continue
                diff = r_usd - float(rates_row[ccy])
            else:
                continue

            w = held_weights.get(pair, 0.0)
            if w > 0:
                rate = diff - self.markup
            elif w < 0:
                rate = diff + self.markup
            else:
                rate = (diff - self.markup) if diff >= 0 else (diff + self.markup)

            rates[pair] = float(rate)

        return rates


if __name__ == "__main__":
    run_bot(SignedFxCarryBot)
