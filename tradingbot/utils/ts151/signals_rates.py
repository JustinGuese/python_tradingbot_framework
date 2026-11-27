"""Pure signal functions for TS151 rates, credit, and spread strategies.

Covers:
- 5.2 Bullets (bullet_weights)
- 5.3 Barbells (barbell_weights)
- 5.4 Ladders (ladder_weights)
- 5.5 Cash Flow Immunization (immunization_weights)
- 5.6 Dollar-Duration Butterfly (dd_butterfly_signal)
- 5.7 Fifty-Fifty Butterfly (fifty_fifty_butterfly_signal)
- 5.8 Regression-Weighted Butterfly (regression_butterfly_signal)
- 5.9 Low-Risk Bond (low_risk_bond_weights)
- 5.10 Bond Value (bond_value_weights)
- 5.11 Bond Carry and Roll-Down (bond_carry_weights)
- 5.12 Riding the Yield Curve (rolling_down_weights)
- 5.13 Yield Curve Slope / Spread (curve_spread_signal)
- 11.7 MBS Arbitrage (mbs_signal)
- 12.1 Convertible Arbitrage (convertible_arb_weights)
- 13.1 Municipal Bond Spread (muni_signal)
- 14.1 Inflation Breakeven Arbitrage (breakeven_signal)
- 15.1 Distressed Debt (distressed_debt_signal)
- 19.4 Global Fixed Income (global_fixed_income_weights)

All mathematical functions here are pure and require no network or database calls.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from tradingbot.utils.ts151 import universes
from tradingbot.utils.ts151.common import dv01_neutral, ols_beta, replay_state, topk_bottomk, trailing_return, zscore

# ------------------------------------------------------------------
# §5.2 Bullet Portfolio
# ------------------------------------------------------------------


def bullet_weights(
    universe: Sequence[str] | None = None,
    target_maturity: float = 8.5,
    gross: float = 1.0,
    maturities: dict[str, float] | None = None,
) -> dict[str, float]:
    """TS151 §5.2: 100% in the Treasury ETF whose maturity is nearest target_maturity."""
    m_dict = maturities or universes.TREASURY_MATURITY
    u = list(universe) if universe is not None else list(universes.TREASURY_CURVE)
    candidates = [sym for sym in u if sym in m_dict]
    if not candidates:
        return {}

    best = min(candidates, key=lambda sym: abs(m_dict[sym] - target_maturity))
    return {best: float(gross)}


# ------------------------------------------------------------------
# §5.3 Barbell Portfolio
# ------------------------------------------------------------------


def barbell_weights(
    target_duration: float = 7.5,
    short_asset: str = "SHY",
    long_asset: str = "TLT",
    gross: float = 1.0,
    durations: dict[str, float] | None = None,
) -> dict[str, float]:
    """TS151 §5.3: SHY + TLT weighted so total duration equals target_duration."""
    d_dict = durations or universes.TREASURY_DURATION
    d_short = float(d_dict.get(short_asset, 1.9))
    d_long = float(d_dict.get(long_asset, 17.0))

    if d_long == d_short:
        return {short_asset: float(gross / 2.0), long_asset: float(gross / 2.0)}

    # w_short * d_short + (1 - w_short) * d_long = target_duration
    w_short = (d_long - target_duration) / (d_long - d_short)
    w_short = max(0.0, min(1.0, w_short))
    w_long = 1.0 - w_short

    return {
        short_asset: float(w_short * gross),
        long_asset: float(w_long * gross),
    }


# ------------------------------------------------------------------
# §5.4 Ladder Portfolio
# ------------------------------------------------------------------


def ladder_weights(
    universe: Sequence[str] | None = None,
    gross: float = 1.0,
) -> dict[str, float]:
    """TS151 §5.4: Equal weight across all curve ETFs."""
    u = list(universe) if universe is not None else list(universes.TREASURY_CURVE)
    if not u:
        return {}
    w = float(gross / len(u))
    return dict.fromkeys(u, w)


# ------------------------------------------------------------------
# §5.5 Cash Flow Immunization
# ------------------------------------------------------------------


def immunization_weights(
    as_of: pd.Timestamp | Any,
    start: pd.Timestamp | Any | None = None,
    horizon_years: float = 7.0,
    universe: Sequence[str] | None = None,
    durations: dict[str, float] | None = None,
    gross: float = 1.0,
) -> dict[str, float]:
    """TS151 §5.5: Two ETFs bracketing the remaining horizon duration.

    The horizon declines from horizon_years by elapsed time since start.
    When horizon <= shortest duration, 100% goes to SHY.
    """
    d_dict = durations or universes.TREASURY_DURATION
    u = list(universe) if universe is not None else list(universes.TREASURY_CURVE)
    items = sorted([(sym, float(d_dict[sym])) for sym in u if sym in d_dict], key=lambda x: x[1])
    if not items:
        return {}

    as_of_ts = pd.to_datetime(as_of)
    start_ts = pd.to_datetime(start) if start is not None else as_of_ts

    elapsed_days = max(0.0, float((as_of_ts - start_ts).days))
    elapsed_years = elapsed_days / 365.25
    remaining_horizon = max(0.0, float(horizon_years - elapsed_years))

    min_sym, min_dur = items[0]
    max_sym, max_dur = items[-1]

    if remaining_horizon <= min_dur:
        return {min_sym: float(gross)}
    if remaining_horizon >= max_dur:
        return {max_sym: float(gross)}

    # Find bracketing pair
    for i in range(len(items) - 1):
        sym_low, d_low = items[i]
        sym_high, d_high = items[i + 1]
        if d_low <= remaining_horizon <= d_high:
            span = d_high - d_low
            if span == 0:
                return {sym_low: float(gross)}
            w_low = (d_high - remaining_horizon) / span
            w_high = 1.0 - w_low
            return {
                sym_low: float(w_low * gross),
                sym_high: float(w_high * gross),
            }

    return {min_sym: float(gross)}


# ------------------------------------------------------------------
# Butterfly Helpers (§5.6, §5.7, §5.8)
# ------------------------------------------------------------------


def compute_butterfly_weights(
    body: str = "IEF",
    wings: tuple[str, str] = ("SHY", "TLT"),
    durations: dict[str, float] | None = None,
    maturities: dict[str, float] | None = None,
    weighting: str = "fifty_fifty",
    gross: float = 1.0,
) -> dict[str, float]:
    """Compute normalized butterfly weights (positive body, negative wings) summing to gross.

    weighting:
    - "fifty_fifty": wings each get half the body DV01 (common.dv01_neutral).
    - "dollar_duration": wings weighted by maturity distance to immunize curve slope.
    """
    d_dict = durations or universes.TREASURY_DURATION
    d_body = float(d_dict[body])
    d_w0 = float(d_dict[wings[0]])
    d_w1 = float(d_dict[wings[1]])

    if weighting == "dollar_duration" and maturities:
        m_body = float(maturities.get(body, 8.5))
        m_w0 = float(maturities.get(wings[0], 2.0))
        m_w1 = float(maturities.get(wings[1], 25.0))
        denom = m_w1 - m_w0
        if denom != 0:
            frac0 = (m_w1 - m_body) / denom
            frac1 = (m_body - m_w0) / denom
        else:
            frac0 = 0.5
            frac1 = 0.5
        w0 = -frac0 * (d_body / d_w0)
        w1 = -frac1 * (d_body / d_w1)
        raw = {body: 1.0, wings[0]: w0, wings[1]: w1}
    else:
        raw = dv01_neutral(body, wings, d_dict)

    abs_sum = sum(abs(v) for v in raw.values())
    if abs_sum == 0.0:
        return {}
    scale = float(gross / abs_sum)
    return {k: float(v * scale) for k, v in raw.items()}


def butterfly_state_signal(
    spread_series: pd.Series,
    window: int = 252,
    entry: float = 1.0,
    exit_level: float = 0.25,
) -> int:
    """Replay hysteresis state on rolling z-score of yield spread.

    Signal = body yield - wing yield.
    z > entry -> state 1 (long body, short wings).
    z < -entry -> state -1 (short body, long wings).
    abs(z) < exit_level -> state 0 (flat).
    """
    if len(spread_series) < window + 1:
        return 0

    roll = spread_series.rolling(window=window, min_periods=window)
    mean = roll.mean()
    std = roll.std()

    z = (spread_series - mean) / std.replace(0.0, np.nan)
    valid_z = z.dropna()
    if valid_z.empty:
        return 0

    long_enter = valid_z > entry
    long_exit = valid_z.abs() < exit_level
    short_enter = valid_z < -entry
    short_exit = valid_z.abs() < exit_level

    return replay_state(long_enter, long_exit, short_enter, short_exit)


# ------------------------------------------------------------------
# §5.6 Dollar-Duration Butterfly
# ------------------------------------------------------------------


def dd_butterfly_signal(
    spread_series: pd.Series,
    window: int = 252,
    entry: float = 1.0,
    exit_level: float = 0.25,
    gross: float = 1.0,
    body: str = "IEF",
    wings: tuple[str, str] = ("SHY", "TLT"),
    durations: dict[str, float] | None = None,
    maturities: dict[str, float] | None = None,
) -> dict[str, float] | None:
    """TS151 §5.6: Dollar-duration neutral butterfly."""
    if len(spread_series) < window + 1:
        return None

    state = butterfly_state_signal(spread_series, window=window, entry=entry, exit_level=exit_level)
    if state == 0:
        return {}

    base = compute_butterfly_weights(
        body=body,
        wings=wings,
        durations=durations,
        maturities=maturities or universes.TREASURY_MATURITY,
        weighting="dollar_duration",
        gross=gross,
    )
    if state == 1:
        return base
    return {k: -v for k, v in base.items()}


# ------------------------------------------------------------------
# §5.7 Fifty-Fifty Butterfly
# ------------------------------------------------------------------


def fifty_fifty_butterfly_signal(
    spread_series: pd.Series,
    window: int = 252,
    entry: float = 1.0,
    exit_level: float = 0.25,
    gross: float = 1.0,
    body: str = "IEF",
    wings: tuple[str, str] = ("SHY", "TLT"),
    durations: dict[str, float] | None = None,
) -> dict[str, float] | None:
    """TS151 §5.7: Fifty-fifty butterfly (each wing has 50% of body DV01)."""
    if len(spread_series) < window + 1:
        return None

    state = butterfly_state_signal(spread_series, window=window, entry=entry, exit_level=exit_level)
    if state == 0:
        return {}

    base = compute_butterfly_weights(
        body=body,
        wings=wings,
        durations=durations,
        weighting="fifty_fifty",
        gross=gross,
    )
    if state == 1:
        return base
    return {k: -v for k, v in base.items()}


# ------------------------------------------------------------------
# §5.8 Regression-Weighted Butterfly
# ------------------------------------------------------------------


def regression_butterfly_weights(
    body_yield: pd.Series,
    w0_yield: pd.Series,
    w1_yield: pd.Series,
    window: int = 252,
    durations: dict[str, float] | None = None,
    body: str = "IEF",
    wings: tuple[str, str] = ("SHY", "TLT"),
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Compute wing weights from regressing Δbody yield on Δwing yields over window."""
    d_body_y = body_yield.diff().dropna()
    d_w0_y = w0_yield.diff().dropna()
    d_w1_y = w1_yield.diff().dropna()

    df = pd.concat([d_body_y, d_w0_y, d_w1_y], axis=1).dropna()
    if len(df) < window:
        return None

    recent = df.iloc[-window:]
    y = recent.iloc[:, 0].to_numpy()
    x = recent.iloc[:, 1:3].to_numpy()

    # OLS multiple regression of y on x without intercept
    # x^T x beta = x^T y
    try:
        beta, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
        b0, b1 = float(beta[0]), float(beta[1])
    except Exception:
        b0, b1 = 0.5, 0.5

    d_dict = durations or universes.TREASURY_DURATION
    d_b = float(d_dict[body])
    d_0 = float(d_dict[wings[0]])
    d_1 = float(d_dict[wings[1]])

    # Wing positions in duration-adjusted terms
    raw = {
        body: 1.0,
        wings[0]: -b0 * (d_b / d_0),
        wings[1]: -b1 * (d_b / d_1),
    }
    abs_sum = sum(abs(v) for v in raw.values())
    if abs_sum == 0.0:
        return {}
    scale = float(gross / abs_sum)
    return {k: float(v * scale) for k, v in raw.items()}


def regression_butterfly_signal(
    spread_series: pd.Series,
    body_yield: pd.Series,
    w0_yield: pd.Series,
    w1_yield: pd.Series,
    window: int = 252,
    entry: float = 1.0,
    exit_level: float = 0.25,
    gross: float = 1.0,
    body: str = "IEF",
    wings: tuple[str, str] = ("SHY", "TLT"),
    durations: dict[str, float] | None = None,
) -> dict[str, float] | None:
    """TS151 §5.8: Regression-weighted butterfly."""
    if len(spread_series) < window + 1:
        return None

    state = butterfly_state_signal(spread_series, window=window, entry=entry, exit_level=exit_level)
    if state == 0:
        return {}

    base = regression_butterfly_weights(
        body_yield=body_yield,
        w0_yield=w0_yield,
        w1_yield=w1_yield,
        window=window,
        durations=durations,
        body=body,
        wings=wings,
        gross=gross,
    )
    if not base:
        return None

    if state == 1:
        return base
    return {k: -v for k, v in base.items()}


# ------------------------------------------------------------------
# §5.9 Low-Risk Bond Strategy
# ------------------------------------------------------------------


def low_risk_bond_weights(
    closes: pd.DataFrame,
    frac: float = 0.33,
    vol_window: int = 63,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """TS151 §5.9: Long lowest-vol frac and short highest-vol frac, scaled to equal vol."""
    if len(closes) < vol_window + 1 or len(closes.columns) < 2:
        return None

    returns = closes.pct_change().iloc[-vol_window:]
    vols = returns.std().dropna()
    vols = vols[vols > 0.0]
    if len(vols) < 2:
        return None

    num_select = max(1, round(len(vols) * frac))
    if len(vols) < 2 * num_select:
        return None

    sorted_vols = vols.sort_values(ascending=True)
    long_syms = sorted_vols.index[:num_select]
    short_syms = sorted_vols.index[-num_select:]

    # Weight each side by inverse volatility so each asset contributes equally to risk
    inv_long = 1.0 / vols[long_syms]
    inv_short = 1.0 / vols[short_syms]

    w_long_unit = inv_long / inv_long.sum()
    w_short_unit = inv_short / inv_short.sum()

    half_gross = float(gross / 2.0)
    out: dict[str, float] = {}
    for sym in long_syms:
        out[str(sym)] = float(w_long_unit[sym] * half_gross)
    for sym in short_syms:
        out[str(sym)] = float(-w_short_unit[sym] * half_gross)

    return out


# ------------------------------------------------------------------
# §5.10 Bond Value Strategy
# ------------------------------------------------------------------


def bond_value_weights(
    yields_df: pd.DataFrame,
    window: int = 1260,
    k: int = 2,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §5.10: Value = current yield − window average yield. Long top k, short bottom k."""
    if len(yields_df) < window + 1:
        return None

    valid_cols = [c for c in yields_df.columns if yields_df[c].notna().sum() >= window]
    if len(valid_cols) < 2 * k:
        return None

    roll_mean = yields_df[valid_cols].iloc[-window:].mean()
    now_yield = yields_df[valid_cols].iloc[-1]
    value_scores = now_yield - roll_mean

    return topk_bottomk(value_scores, k=k, long_only=False, gross=gross)


# ------------------------------------------------------------------
# §5.11 Bond Carry and Roll-Down
# ------------------------------------------------------------------


def compute_bond_carry_scores(
    yields_dict: dict[str, float],
    maturities: dict[str, float] | None = None,
    durations: dict[str, float] | None = None,
    horizon: float = 1.0,
) -> pd.Series:
    """Compute Carry_i = (y_i + roll-down_i) / duration_i."""
    m_dict = maturities or universes.TREASURY_MATURITY
    d_dict = durations or universes.TREASURY_DURATION

    # Sort available ETFs by maturity ascending
    available = [sym for sym in yields_dict if sym in m_dict and sym in d_dict and not math.isnan(yields_dict[sym])]
    available.sort(key=lambda s: m_dict[s])

    scores: dict[str, float] = {}
    for i, sym in enumerate(available):
        y_i = float(yields_dict[sym])
        mat_i = float(m_dict[sym])
        d_i = float(d_dict[sym])
        if d_i <= 0.0:
            continue

        if i == 0:
            # Shortest maturity: roll-down toward 0 maturity at zero yield or short-rate
            # roll-down = (y_i - 0) / mat_i * horizon
            roll_down = (y_i / mat_i) * horizon
        else:
            prev_sym = available[i - 1]
            y_prev = float(yields_dict[prev_sym])
            mat_prev = float(m_dict[prev_sym])
            denom = mat_i - mat_prev
            roll_down = ((y_i - y_prev) / denom) * horizon if denom > 0 else 0.0

        carry = (y_i + roll_down) / d_i
        scores[sym] = float(carry)

    return pd.Series(scores, dtype=float)


def bond_carry_weights(
    yields_dict: dict[str, float],
    maturities: dict[str, float] | None = None,
    durations: dict[str, float] | None = None,
    horizon: float = 1.0,
    k: int = 2,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §5.11: Long top k, short bottom k by carry and roll-down."""
    scores = compute_bond_carry_scores(yields_dict, maturities, durations, horizon)
    if len(scores) < 2 * k:
        return None
    return topk_bottomk(scores, k=k, long_only=False, gross=gross)


# ------------------------------------------------------------------
# §5.12 Riding the Yield Curve (Rolling Down)
# ------------------------------------------------------------------


def rolling_down_weights(
    yields_dict: dict[str, float],
    maturities: dict[str, float] | None = None,
    durations: dict[str, float] | None = None,
    horizon: float = 1.0,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §5.12: Hold the single ETF with highest roll-down per unit duration."""
    m_dict = maturities or universes.TREASURY_MATURITY
    d_dict = durations or universes.TREASURY_DURATION

    available = [sym for sym in yields_dict if sym in m_dict and sym in d_dict and not math.isnan(yields_dict[sym])]
    available.sort(key=lambda s: m_dict[s])

    scores: dict[str, float] = {}
    for i, sym in enumerate(available):
        y_i = float(yields_dict[sym])
        mat_i = float(m_dict[sym])
        d_i = float(d_dict[sym])
        if d_i <= 0.0:
            continue

        if i == 0:
            roll_down = (y_i / mat_i) * horizon
        else:
            prev_sym = available[i - 1]
            y_prev = float(yields_dict[prev_sym])
            mat_prev = float(m_dict[prev_sym])
            denom = mat_i - mat_prev
            roll_down = ((y_i - y_prev) / denom) * horizon if denom > 0 else 0.0

        scores[sym] = float(roll_down / d_i)

    if not scores:
        return None

    best = max(scores, key=lambda s: scores[s])
    return {best: float(gross)}


# ------------------------------------------------------------------
# §5.13 Yield Curve Slope / Curve Spread
# ------------------------------------------------------------------


def curve_spread_signal(
    slope_series: pd.Series,
    window: int = 252,
    entry: float = 1.0,
    exit_level: float = 0.25,
    short_asset: str = "SHY",
    long_asset: str = "TLT",
    durations: dict[str, float] | None = None,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §5.13: Slope = DGS30 - DGS2.

    If z < -entry: steepener (long SHY, short TLT, DV01-neutral).
    If z > entry: flattener (short SHY, long TLT).
    Exit when abs(z) < exit_level.
    """
    if len(slope_series) < window + 1:
        return None

    roll = slope_series.rolling(window=window, min_periods=window)
    mean = roll.mean()
    std = roll.std()

    z = (slope_series - mean) / std.replace(0.0, np.nan)
    valid_z = z.dropna()
    if valid_z.empty:
        return None

    # Steepener when z < -entry (inverted curve -> bet on steepening)
    # Flattener when z > entry (steep curve -> bet on flattening)
    steepener_enter = valid_z < -entry
    steepener_exit = valid_z.abs() < exit_level
    flattener_enter = valid_z > entry
    flattener_exit = valid_z.abs() < exit_level

    state = replay_state(steepener_enter, steepener_exit, flattener_enter, flattener_exit)
    if state == 0:
        return {}

    d_dict = durations or universes.TREASURY_DURATION
    d_s = float(d_dict.get(short_asset, 1.9))
    d_l = float(d_dict.get(long_asset, 17.0))

    # DV01 neutral: w_s * d_s + w_l * d_l = 0 => w_l = -w_s * (d_s / d_l)
    ratio = d_s / d_l
    w_s_unit = 1.0 / (1.0 + ratio)
    w_l_unit = -ratio / (1.0 + ratio)

    if state == 1:
        # Steepener: long SHY, short TLT
        return {
            short_asset: float(w_s_unit * gross),
            long_asset: float(w_l_unit * gross),
        }
    else:
        # Flattener: short SHY, long TLT
        return {
            short_asset: float(-w_s_unit * gross),
            long_asset: float(-w_l_unit * gross),
        }


# ------------------------------------------------------------------
# §11.7 MBS Arbitrage
# ------------------------------------------------------------------


def mbs_signal(
    closes: pd.DataFrame,
    window: int = 252,
    entry: float = 1.0,
    exit_level: float = 0.0,
    duration_mbb: float = 6.0,
    duration_ief: float = 7.5,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §11.7: Long MBB / short IEF (duration-matched) when log(MBB/IEF) z < -entry; exit at exit_level."""
    if len(closes) < window + 1 or "MBB" not in closes.columns or "IEF" not in closes.columns:
        return None

    ratio = closes["MBB"] / closes["IEF"]
    log_spread = np.log(ratio.replace(0.0, np.nan)).dropna()
    if len(log_spread) < window + 1:
        return None

    roll = log_spread.rolling(window=window, min_periods=window)
    mean = roll.mean()
    std = roll.std()

    z = (log_spread - mean) / std.replace(0.0, np.nan)
    valid_z = z.dropna()
    if valid_z.empty:
        return None

    long_enter = valid_z < -entry
    long_exit = valid_z >= exit_level

    state = replay_state(long_enter, long_exit)
    if state == 0:
        return {}

    # Duration matched: w_mbb * d_mbb + w_ief * d_ief = 0
    ratio_dur = duration_mbb / duration_ief
    w_mbb = 1.0 / (1.0 + ratio_dur)
    w_ief = -ratio_dur / (1.0 + ratio_dur)

    return {
        "MBB": float(w_mbb * gross),
        "IEF": float(w_ief * gross),
    }


# ------------------------------------------------------------------
# §12.1 Convertible Arbitrage
# ------------------------------------------------------------------


def convertible_arb_weights(
    closes: pd.DataFrame,
    window: int = 126,
    asset: str = "CWB",
    hedge: str = "SPY",
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §12.1: Long CWB, short SPY × rolling beta(window=126)."""
    if len(closes) < window + 1 or asset not in closes.columns or hedge not in closes.columns:
        return None

    ret_asset = closes[asset].pct_change().dropna().iloc[-window:]
    ret_hedge = closes[hedge].pct_change().dropna().iloc[-window:]

    beta = ols_beta(ret_asset, ret_hedge)
    if math.isnan(beta):
        return None

    # Beta-neutral: 1 dollar of CWB hedged by beta dollars of SPY
    denom = 1.0 + abs(beta)
    if denom == 0.0:
        return {}

    w_asset = float(gross / denom)
    w_hedge = float(-beta * gross / denom)

    return {asset: w_asset, hedge: w_hedge}


# ------------------------------------------------------------------
# §13.1 Municipal Bond Spread
# ------------------------------------------------------------------


def muni_signal(
    closes: pd.DataFrame,
    window: int = 252,
    entry: float = 0.5,
    duration_mub: float = 6.5,
    duration_ief: float = 7.5,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §13.1: Long MUB / short IEF duration-neutral when yield proxy z > entry; else flat."""
    if len(closes) < window + 1 or "MUB" not in closes.columns or "IEF" not in closes.columns:
        return None

    # Yield proxy: -log(MUB / IEF) = log(IEF / MUB)
    ratio = closes["MUB"] / closes["IEF"]
    proxy = -np.log(ratio.replace(0.0, np.nan)).dropna()
    if len(proxy) < window + 1:
        return None

    roll = proxy.rolling(window=window, min_periods=window)
    z = (proxy - roll.mean()) / roll.std().replace(0.0, np.nan)
    last_z = z.iloc[-1]

    if math.isnan(last_z) or last_z <= entry:
        return {}

    ratio_dur = duration_mub / duration_ief
    w_mub = 1.0 / (1.0 + ratio_dur)
    w_ief = -ratio_dur / (1.0 + ratio_dur)

    return {
        "MUB": float(w_mub * gross),
        "IEF": float(w_ief * gross),
    }


# ------------------------------------------------------------------
# §14.1 Inflation Breakeven Arbitrage
# ------------------------------------------------------------------


def breakeven_signal(
    t10yie_series: pd.Series,
    mode: str = "breakeven",
    window: int = 252,
    entry: float = 1.0,
    exit_level: float = 0.25,
    fair: float = 2.0,
    duration_tip: float = 7.0,
    duration_ief: float = 7.5,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §14.1: Long TIP / short IEF duration-matched based on 10Y breakeven inflation rate."""
    ratio_dur = duration_tip / duration_ief
    w_tip_unit = 1.0 / (1.0 + ratio_dur)
    w_ief_unit = -ratio_dur / (1.0 + ratio_dur)

    if mode == "tips_treasury":
        if t10yie_series.empty:
            return None
        last_val = float(t10yie_series.dropna().iloc[-1])
        if last_val < fair:
            return {
                "TIP": float(w_tip_unit * gross),
                "IEF": float(w_ief_unit * gross),
            }
        return {}

    # mode == "breakeven"
    if len(t10yie_series) < window + 1:
        return None

    roll = t10yie_series.rolling(window=window, min_periods=window)
    mean = roll.mean()
    std = roll.std()

    z = (t10yie_series - mean) / std.replace(0.0, np.nan)
    valid_z = z.dropna()
    if valid_z.empty:
        return None

    # When breakeven is low (z < -entry), inflation is underpriced -> Long TIP, short IEF
    # When breakeven is high (z > entry), inflation is overpriced -> Short TIP, long IEF
    long_enter = valid_z < -entry
    long_exit = valid_z.abs() < exit_level
    short_enter = valid_z > entry
    short_exit = valid_z.abs() < exit_level

    state = replay_state(long_enter, long_exit, short_enter, short_exit)
    if state == 0:
        return {}

    if state == 1:
        return {
            "TIP": float(w_tip_unit * gross),
            "IEF": float(w_ief_unit * gross),
        }
    else:
        return {
            "TIP": float(-w_tip_unit * gross),
            "IEF": float(-w_ief_unit * gross),
        }


# ------------------------------------------------------------------
# §15.1 Distressed Debt Strategy
# ------------------------------------------------------------------


def distressed_debt_signal(
    closes: pd.DataFrame,
    entry: float = 0.08,
    exit_level: float = 0.02,
    window: int = 252,
    asset: str = "HYG",
    benchmark: str = "IEF",
    cash_asset: str = "SHV",
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §15.1: Long HYG when HYG/IEF drawdown from 252-bar high < -entry, hold until -exit; else SHV."""
    if len(closes) < window + 1 or asset not in closes.columns or benchmark not in closes.columns:
        return None

    ratio = closes[asset] / closes[benchmark]
    ratio_clean = ratio.dropna()
    if len(ratio_clean) < window + 1:
        return None

    roll_max = ratio_clean.rolling(window=window, min_periods=window).max()
    dd = (ratio_clean / roll_max) - 1.0
    valid_dd = dd.dropna()
    if valid_dd.empty:
        return None

    long_enter = valid_dd < -entry
    long_exit = valid_dd >= -exit_level

    state = replay_state(long_enter, long_exit)
    if state == 1:
        return {asset: float(gross)}
    return {cash_asset: float(gross)}


# ------------------------------------------------------------------
# §19.4 Global Fixed Income Strategy
# ------------------------------------------------------------------


def global_fixed_income_weights(
    closes: pd.DataFrame,
    mom_window: int = 126,
    rev_window: int = 1260,
    k: int = 2,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """TS151 §19.4: Score = z(momentum) + z(-reversal). Long top k country bonds."""
    needed = mom_window + 1
    if len(closes) < needed or len(closes.columns) < k:
        return None

    mom_ret = trailing_return(closes, window=mom_window)
    if isinstance(mom_ret, float) or mom_ret.isna().all():
        return None

    z_mom = zscore(mom_ret)

    # Reversal if sufficient history is available
    if len(closes) >= rev_window + 1:
        rev_ret = trailing_return(closes, window=rev_window)
        if isinstance(rev_ret, pd.Series) and not rev_ret.isna().all():
            z_rev = zscore(-rev_ret)
            score = z_mom + z_rev
        else:
            score = z_mom
    else:
        score = z_mom

    return topk_bottomk(score, k=k, long_only=True, gross=gross)


# ------------------------------------------------------------------
# FRED Data Loaders for Rates Bots
# ------------------------------------------------------------------


def load_fred_treasury_yields(index: pd.Index) -> pd.DataFrame:
    """Fetch and align FRED yields for Treasury curve ETFs to the given index."""
    from tradingbot.utils.ts151.fred import fred_series

    yields: dict[str, pd.Series] = {}
    for sym, spec in universes.TREASURY_FRED.items():
        if isinstance(spec, tuple):
            s1 = fred_series(spec[0])
            s2 = fred_series(spec[1])
            combined = 0.5 * (s1 + s2)
        else:
            combined = fred_series(spec)
        yields[sym] = combined.reindex(index, method="ffill")
    return pd.DataFrame(yields, index=index)


def load_fred_spread_series(
    index: pd.Index,
    body: str = "IEF",
    wings: tuple[str, str] = ("SHY", "TLT"),
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Fetch and align yields for body and wings, returning (spread, body_yield, w0_yield, w1_yield)."""
    df = load_fred_treasury_yields(index)
    body_y = df[body]
    w0_y = df[wings[0]]
    w1_y = df[wings[1]]
    avg_wing = 0.5 * (w0_y + w1_y)
    spread = body_y - avg_wing
    return spread, body_y, w0_y, w1_y


def load_fred_curve_slope(
    index: pd.Index,
    long_series: str = "DGS30",
    short_series: str = "DGS2",
) -> pd.Series:
    """Fetch and align 30Y - 2Y slope from FRED to the given index."""
    from tradingbot.utils.ts151.fred import fred_series

    s_long = fred_series(long_series).reindex(index, method="ffill")
    s_short = fred_series(short_series).reindex(index, method="ffill")
    return s_long - s_short


def load_fred_breakeven_series(
    index: pd.Index,
    series_id: str = "T10YIE",
) -> pd.Series:
    """Fetch and align 10Y breakeven inflation rate from FRED to the given index."""
    from tradingbot.utils.ts151.fred import fred_series

    return fred_series(series_id).reindex(index, method="ffill")
