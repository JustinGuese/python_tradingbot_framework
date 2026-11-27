"""Pure signal functions for Index, Volatility, and FX strategies.

Kakushadze & Serur (2018) §6.5, 7.2, 7.3, 7.6, 8.1-8.4:
- 6.5 vol_target_signal: Target volatility on index (§6.5).
- 7.2 vix_basis_signal: VIX term structure basis (§7.2).
- 7.3 vol_carry_signal: Hedged short-vol carry trade (§7.3).
- 7.6 variance_swap_signal: Replicating variance swap / VRP (§7.6).
- 8.1 fx_ma_signal: FX moving average crossover (§8.1).
- 8.2 fx_carry_signal: FX interest rate carry trade (§8.2).
- 8.3 dollar_carry_signal: Dollar carry trade (§8.3).
- 8.4 fx_mom_carry_signal: FX momentum + carry blend (§8.4).

All mathematical operations are pure numpy/pandas functions.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from tradingbot.utils.fx_carry import load_fred_rates
from tradingbot.utils.ts151.base import Ctx
from tradingbot.utils.ts151.common import (
    ols_beta,
    replay_state,
    topk_bottomk,
    trailing_return,
    zscore,
)
from tradingbot.utils.ts151.universes import FX_ETF_TO_CCY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §6.5 Volatility Targeting
# ---------------------------------------------------------------------------


def vol_target_signal(
    ctx: Ctx,
    target: float = 0.15,
    window: int = 21,
    cap: float = 1.0,
    band: float = 0.1,
    annualization_factor: float = 252.0,
) -> dict[str, float] | None:
    """Volatility targeting strategy (§6.5).

    Formula (§6.5):
        Weight = min(cap, target / (realized_vol(window) * sqrt(252)))
    Rebalances only when the change in target leverage exceeds `band`.

    Returns None if history has fewer than `window + 1` bars or if realized vol
    cannot be computed.
    """
    symbol = ctx.universe[0] if ctx.universe else "SPY"
    if symbol not in ctx.closes.columns:
        return None

    series = ctx.closes[symbol].dropna()
    needed_bars = window + 1
    if len(series) < needed_bars:
        return None

    returns = series.pct_change().dropna()
    if len(returns) < window:
        return None

    rolling_std = returns.rolling(window).std()
    ann_vol = rolling_std * math.sqrt(annualization_factor)

    valid_mask = ann_vol.notna() & (ann_vol > 0)
    if not valid_mask.any():
        return None

    valid_vol = ann_vol[valid_mask]
    raw_weights = (target / valid_vol).clip(upper=cap)

    # Replay rebalance band sequentially
    current_w = float(raw_weights.iloc[0])
    for w_raw in raw_weights.iloc[1:]:
        val = float(w_raw)
        if abs(val - current_w) > band:
            current_w = val

    return {symbol: float(current_w)}


# ---------------------------------------------------------------------------
# §7.2 VIX Basis / Term Structure
# ---------------------------------------------------------------------------


def vix_basis_signal(
    ctx: Ctx,
    vix_ticker: str = "^VIX",
    vix3m_ticker: str = "^VIX3M",
    trade_ticker: str = "VIXY",
    inverse_ticker: str = "SVXY",
    contango: float = 0.95,
    backwardation: float = 1.05,
    weight: float = 0.3,
    long_only: bool = False,
) -> dict[str, float] | None:
    """VIX basis / term structure strategy (§7.2).

    If ^VIX / ^VIX3M < contango (0.95), short VIXY at weight=0.3 (or long SVXY if long_only).
    If ^VIX / ^VIX3M > backwardation (1.05), long VIXY at weight=0.3.
    Otherwise flat.

    Returns None if benchmark tickers are missing or have insufficient data.
    """
    if vix_ticker not in ctx.closes.columns or vix3m_ticker not in ctx.closes.columns:
        return None

    vix = ctx.closes[vix_ticker]
    vix3m = ctx.closes[vix3m_ticker]
    aligned = pd.concat([vix, vix3m], axis=1).dropna()
    if len(aligned) < 2:
        return None

    ratio = aligned.iloc[:, 0] / aligned.iloc[:, 1]
    ratio = ratio.dropna()
    if ratio.empty:
        return None

    long_enter = ratio > backwardation
    long_exit = ratio <= backwardation
    short_enter = ratio < contango
    short_exit = ratio >= contango

    state = replay_state(
        long_enter=long_enter,
        long_exit=long_exit,
        short_enter=short_enter,
        short_exit=short_exit,
    )

    if state == 1:
        return {trade_ticker: float(weight)}
    elif state == -1:
        if long_only:
            return {inverse_ticker: float(weight)}
        return {trade_ticker: -float(weight)}
    return {}


# ---------------------------------------------------------------------------
# §7.3 Hedged Volatility Carry Trade
# ---------------------------------------------------------------------------


def vol_carry_signal(
    ctx: Ctx,
    vix_ticker: str = "^VIX",
    vix3m_ticker: str = "^VIX3M",
    vixy_ticker: str = "VIXY",
    spy_ticker: str = "SPY",
    contango: float = 0.95,
    weight: float = 0.3,
    window: int = 63,
    max_hedge: float = 1.0,
) -> dict[str, float] | None:
    """Volatility carry trade with SPY equity hedge (§7.3).

    Short VIXY at weight=0.3 plus a short SPY hedge (beta of VIXY on SPY over
    window=63, so the hedge is long SPY when beta < 0), only while in contango
    (^VIX / ^VIX3M < 0.95).

    Returns None if data is insufficient, or {} when not in contango.
    """
    for sym in (vix_ticker, vix3m_ticker, vixy_ticker, spy_ticker):
        if sym not in ctx.closes.columns:
            return None

    needed_bars = window + 1
    if len(ctx.closes) < needed_bars:
        return None

    vix = ctx.closes[vix_ticker]
    vix3m = ctx.closes[vix3m_ticker]
    aligned_vix = pd.concat([vix, vix3m], axis=1).dropna()
    if len(aligned_vix) < 2:
        return None

    ratio = aligned_vix.iloc[:, 0] / aligned_vix.iloc[:, 1]
    short_enter = ratio < contango
    short_exit = ratio >= contango

    state = replay_state(
        long_enter=pd.Series(False, index=ratio.index),
        long_exit=pd.Series(False, index=ratio.index),
        short_enter=short_enter,
        short_exit=short_exit,
    )

    if state != -1:
        return {}

    # In contango: compute rolling beta of VIXY on SPY
    ret_vixy = ctx.closes[vixy_ticker].pct_change().dropna()
    ret_spy = ctx.closes[spy_ticker].pct_change().dropna()
    aligned_ret = pd.concat([ret_vixy, ret_spy], axis=1).dropna()
    if len(aligned_ret) < window:
        return None

    sub_ret = aligned_ret.iloc[-window:]
    beta = ols_beta(y=sub_ret.iloc[:, 0], x=sub_ret.iloc[:, 1])
    if math.isnan(beta):
        return None

    # Beta hedge: short VIXY (-weight) induces equity beta (-weight * beta).
    # To neutralize: w_spy = -beta * weight (long SPY when beta < 0).
    w_spy = float(np.clip(-beta * weight, -max_hedge, max_hedge))

    return {
        vixy_ticker: -float(weight),
        spy_ticker: w_spy,
    }


# ---------------------------------------------------------------------------
# §7.6 Replicating Variance Swap / Volatility Risk Premium (VRP)
# ---------------------------------------------------------------------------


def variance_swap_signal(
    ctx: Ctx,
    vix_ticker: str = "^VIX",
    spy_ticker: str = "SPY",
    vixy_ticker: str = "VIXY",
    entry: float = 0.01,
    exit: float = 0.0,
    scale: float = 0.04,
    cap: float = 0.5,
    window: int = 21,
    long_only: bool = False,
    inverse_ticker: str = "SVXY",
) -> dict[str, float] | None:
    """Replicating variance swap / VRP strategy (§7.6).

    Spread = (^VIX / 100)^2 - RV^2(SPY, window=21).
    If spread > entry=0.01, short VIXY sized by spread / scale=0.04 (capped at 0.5).
    If spread < 0, flat.

    Returns None if data is insufficient, or {} when flat.
    """
    for sym in (vix_ticker, spy_ticker):
        if sym not in ctx.closes.columns:
            return None

    needed_bars = window + 2
    if len(ctx.closes) < needed_bars:
        return None

    vix = ctx.closes[vix_ticker].dropna()
    spy = ctx.closes[spy_ticker].dropna()
    aligned = pd.concat([vix, spy], axis=1).dropna()
    if len(aligned) < needed_bars:
        return None

    vix_series = aligned.iloc[:, 0]
    spy_series = aligned.iloc[:, 1]

    spy_ret = spy_series.pct_change()
    rv2 = spy_ret.rolling(window).var() * 252.0
    iv2 = (vix_series / 100.0) ** 2.0

    spread = iv2 - rv2
    valid_spread = spread.dropna()
    if len(valid_spread) < 2:
        return None

    short_enter = valid_spread > entry
    short_exit = valid_spread < exit

    state = replay_state(
        long_enter=pd.Series(False, index=valid_spread.index),
        long_exit=pd.Series(False, index=valid_spread.index),
        short_enter=short_enter,
        short_exit=short_exit,
    )

    if state == -1:
        curr_spread = float(valid_spread.iloc[-1])
        raw_size = min(cap, max(0.0, curr_spread / scale))
        if raw_size <= 0.0:
            return {}
        if long_only:
            return {inverse_ticker: float(raw_size)}
        return {vixy_ticker: -float(raw_size)}

    return {}


# ---------------------------------------------------------------------------
# §8.1 FX Moving Average Crossover
# ---------------------------------------------------------------------------


def fx_ma_signal(
    ctx: Ctx,
    fast: int = 50,
    slow: int = 200,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """FX Moving Average crossover strategy (§8.1).

    Per FX ETF: SMA(fast=50) > SMA(slow=200) -> long, else short.
    Equal weight across universe, total gross leverage = gross (1.0).

    Returns None if history has fewer than `slow` bars for all symbols.
    """
    if len(ctx.closes) < slow:
        return None

    valid_symbols = [sym for sym in ctx.universe if sym in ctx.closes.columns and len(ctx.closes[sym].dropna()) >= slow]
    if not valid_symbols:
        return None

    w_each = gross / len(valid_symbols)
    weights: dict[str, float] = {}
    for sym in valid_symbols:
        series = ctx.closes[sym].dropna()
        sma_fast = float(series.iloc[-fast:].mean())
        sma_slow = float(series.iloc[-slow:].mean())
        if sma_fast > sma_slow:
            weights[sym] = float(w_each)
        else:
            weights[sym] = -float(w_each)

    return weights


# ---------------------------------------------------------------------------
# §8.2 FX Carry Trade
# ---------------------------------------------------------------------------


def fx_carry_signal(
    ctx: Ctx,
    k: int = 2,
    gross: float = 1.0,
    lag_days: int = 25,
    rates: pd.DataFrame | None = None,
) -> dict[str, float] | None:
    """FX Carry trade strategy (§8.2).

    Rate differential vs USD from FRED 3M short interest rates.
    Long top k=2, short bottom k=2.

    Returns None if rates cannot be loaded or fewer than 2*k assets are valid.
    """
    if rates is None:
        try:
            rates = load_fred_rates(lag_days=lag_days, daily_index=ctx.closes.index)
        except Exception as exc:
            raise RuntimeError(f"Failed loading FRED rates for fx_carry_signal: {exc}") from exc

    if rates.empty or "USD" not in rates.columns:
        return None

    if ctx.as_of is not None:
        sub_rates = rates.loc[: ctx.as_of]
        if sub_rates.empty:
            return None
        current_rates = sub_rates.iloc[-1]
    else:
        current_rates = rates.iloc[-1]

    usd_rate = float(current_rates["USD"])
    if math.isnan(usd_rate):
        return None

    scores: dict[str, float] = {}
    for etf in ctx.universe:
        ccy = FX_ETF_TO_CCY.get(etf)
        if ccy is not None and ccy in current_rates:
            r = float(current_rates[ccy])
            if not math.isnan(r):
                scores[etf] = r - usd_rate

    if len(scores) < 2 * k:
        return None

    return topk_bottomk(scores, k=k, long_only=False, gross=gross)


# ---------------------------------------------------------------------------
# §8.3 Dollar Carry Trade
# ---------------------------------------------------------------------------


def dollar_carry_signal(
    ctx: Ctx,
    gross: float = 1.0,
    lag_days: int = 25,
    rates: pd.DataFrame | None = None,
) -> dict[str, float] | None:
    """Dollar Carry trade strategy (§8.3).

    If the average (foreign - USD) short rate differential > 0, long all;
    otherwise short all. Equal weight across universe, total gross leverage = gross (1.0).

    Returns None if rates cannot be loaded or no valid differentials exist.
    """
    if rates is None:
        try:
            rates = load_fred_rates(lag_days=lag_days, daily_index=ctx.closes.index)
        except Exception as exc:
            raise RuntimeError(f"Failed loading FRED rates for dollar_carry_signal: {exc}") from exc

    if rates.empty or "USD" not in rates.columns:
        return None

    if ctx.as_of is not None:
        sub_rates = rates.loc[: ctx.as_of]
        if sub_rates.empty:
            return None
        current_rates = sub_rates.iloc[-1]
    else:
        current_rates = rates.iloc[-1]

    usd_rate = float(current_rates["USD"])
    if math.isnan(usd_rate):
        return None

    diffs: list[float] = []
    valid_etfs: list[str] = []
    for etf in ctx.universe:
        ccy = FX_ETF_TO_CCY.get(etf)
        if ccy is not None and ccy in current_rates:
            r = float(current_rates[ccy])
            if not math.isnan(r):
                diffs.append(r - usd_rate)
                valid_etfs.append(etf)

    if not diffs:
        return None

    avg_diff = float(np.mean(diffs))
    w_each = gross / len(valid_etfs)
    sign = 1.0 if avg_diff > 0 else -1.0

    return {etf: float(sign * w_each) for etf in valid_etfs}


# ---------------------------------------------------------------------------
# §8.4 FX Momentum + Carry Blend
# ---------------------------------------------------------------------------


def fx_mom_carry_signal(
    ctx: Ctx,
    window: int = 63,
    w_mom: float = 0.5,
    k: int = 2,
    gross: float = 1.0,
    lag_days: int = 25,
    rates: pd.DataFrame | None = None,
) -> dict[str, float] | None:
    """FX Momentum + Carry Blend strategy (§8.4).

    Score = w_mom * z(return over window=63) + (1 - w_mom) * z(carry).
    Long top k=2, short bottom k=2.

    Returns None if data or rates are insufficient.
    """
    needed_bars = window + 1
    if len(ctx.closes) < needed_bars:
        return None

    valid_etfs = [
        etf for etf in ctx.universe if etf in ctx.closes.columns and len(ctx.closes[etf].dropna()) >= needed_bars
    ]
    if len(valid_etfs) < 2 * k:
        return None

    # Momentum returns and z-score
    mom_ret = trailing_return(ctx.closes[valid_etfs], window=window)
    if isinstance(mom_ret, float) or mom_ret.isna().any():
        return None
    z_mom = zscore(mom_ret)

    # Carry rates and z-score
    if rates is None:
        try:
            rates = load_fred_rates(lag_days=lag_days, daily_index=ctx.closes.index)
        except Exception as exc:
            raise RuntimeError(f"Failed loading FRED rates for fx_mom_carry_signal: {exc}") from exc

    if rates.empty or "USD" not in rates.columns:
        return None

    if ctx.as_of is not None:
        sub_rates = rates.loc[: ctx.as_of]
        if sub_rates.empty:
            return None
        current_rates = sub_rates.iloc[-1]
    else:
        current_rates = rates.iloc[-1]

    usd_rate = float(current_rates["USD"])
    if math.isnan(usd_rate):
        return None

    carry_diffs: dict[str, float] = {}
    for etf in valid_etfs:
        ccy = FX_ETF_TO_CCY.get(etf)
        if ccy is not None and ccy in current_rates:
            r = float(current_rates[ccy])
            if not math.isnan(r):
                carry_diffs[etf] = r - usd_rate

    if len(carry_diffs) < 2 * k:
        return None

    s_carry = pd.Series(carry_diffs)
    z_carry = zscore(s_carry)

    combined = w_mom * z_mom[valid_etfs] + (1.0 - w_mom) * z_carry[valid_etfs]
    return topk_bottomk(combined, k=k, long_only=False, gross=gross)
