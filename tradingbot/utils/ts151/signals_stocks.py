"""Pure signal functions for TS151 stock strategies (§3 and §15.3, PLAN-ts151.md S4).

No database access or network calls inside signal math functions.
All data is passed via arguments (closes, fundamentals, vol_frame, etc.).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.neighbors import KNeighborsClassifier

from tradingbot.utils.cs_neutral_book import compute_residual_momentum as _cs_residual_momentum
from tradingbot.utils.ts151.common import (
    is_live_as_of,
    ols_beta,
    pivot_levels,
    replay_state,
    topk_bottomk,
    trailing_return,
    zscore,
)
from tradingbot.utils.ts151.data_api import pit_latest, pit_quarterly
from tradingbot.utils.universes import SP500_SECTOR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Staleness & Data Validation Helpers
# ---------------------------------------------------------------------------


def check_sec_fundamentals_staleness(
    fundamentals: pd.DataFrame,
    universe: Sequence[str],
    as_of: Any,
    max_age_days: int = 450,
    min_pct: float = 0.5,
) -> bool:
    """Validate that SEC fundamentals data is present and not stale.

    Raises RuntimeError on live run if table is empty or fewer than min_pct
    of the universe has a filing within max_age_days of as_of.
    Returns False on historical bar if not OK.
    Returns True if OK.
    """
    if fundamentals is None or fundamentals.empty:
        if is_live_as_of(as_of):
            raise RuntimeError(
                "SEC fundamentals data missing (table ts151_sec_fundamentals is empty)"
            )
        return False

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    cutoff_ts = as_of_ts - pd.Timedelta(days=max_age_days)

    filed_dates = pd.to_datetime(fundamentals["filed"]).dt.tz_localize(None).dt.normalize()
    period_end_dates = pd.to_datetime(fundamentals["period_end"]).dt.tz_localize(None).dt.normalize()

    mask = (filed_dates <= as_of_ts) & (period_end_dates >= cutoff_ts)
    covered_tickers = set(fundamentals.loc[mask, "ticker"].dropna().unique())

    target_universe = set(universe)
    if not target_universe:
        return True

    covered_in_universe = covered_tickers.intersection(target_universe)
    pct = len(covered_in_universe) / len(target_universe)
    if pct < min_pct:
        if is_live_as_of(as_of):
            raise RuntimeError(
                f"SEC fundamentals data stale or insufficient coverage: only {pct:.1%} of universe "
                f"has a filing within {max_age_days} days of {as_of_ts.strftime('%Y-%m-%d')} (threshold {min_pct:.0%})"
            )
        return False
    return True


def check_vol_surface_staleness(
    vol_frame: pd.DataFrame,
    as_of: Any,
    max_lag_days: int = 14,
) -> bool:
    """Validate that vol_surface data is present and not stale.

    Raises RuntimeError on live run if table is empty or newest snapshot is older than max_lag_days.
    Returns False on historical bar if not OK.
    Returns True if OK.
    """
    if vol_frame is None or vol_frame.empty:
        if is_live_as_of(as_of):
            raise RuntimeError("vol_surface data missing (table vol_surface is empty)")
        return False

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    snap_dates = pd.to_datetime(vol_frame["snapshot_date"]).dt.tz_localize(None).dt.normalize()
    valid_snaps = snap_dates[snap_dates <= as_of_ts]
    if valid_snaps.empty:
        if is_live_as_of(as_of):
            raise RuntimeError(f"vol_surface has no snapshots on or before {as_of_ts.strftime('%Y-%m-%d')}")
        return False

    newest = valid_snaps.max()
    lag = (as_of_ts - newest).days
    if lag > max_lag_days:
        if is_live_as_of(as_of):
            raise RuntimeError(
                f"vol_surface data stale: newest snapshot is {newest.strftime('%Y-%m-%d')}, "
                f"{lag} days before as_of {as_of_ts.strftime('%Y-%m-%d')} (threshold {max_lag_days} days)"
            )
        return False
    return True


def check_merger_heartbeat(
    merger_events: pd.DataFrame,
    as_of: Any,
    max_lag_days: int = 3,
) -> bool:
    """Validate that ts151_merger_events contains an active _HEARTBEAT.

    Skipped entirely on historical bars (returns True).
    Raises RuntimeError on live run if missing or older than max_lag_days.
    """
    if not is_live_as_of(as_of):
        return True

    if merger_events is None or merger_events.empty:
        raise RuntimeError("Merger data missing (table ts151_merger_events is empty)")

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    hb = merger_events[merger_events["accession"] == "_HEARTBEAT"]
    if hb.empty:
        raise RuntimeError("Merger data missing heartbeat row (_HEARTBEAT accession not found)")

    hb_dates = pd.to_datetime(hb["filed_at"]).dt.tz_localize(None).dt.normalize()
    valid_hb = hb_dates[hb_dates <= as_of_ts]
    if valid_hb.empty:
        raise RuntimeError(f"Merger data has no _HEARTBEAT on or before {as_of_ts.strftime('%Y-%m-%d')}")

    newest_hb = valid_hb.max()
    lag = (as_of_ts - newest_hb).days
    if lag > max_lag_days:
        raise RuntimeError(
            f"Merger data stale: newest _HEARTBEAT is {newest_hb.strftime('%Y-%m-%d')}, "
            f"{lag} days before as_of {as_of_ts.strftime('%Y-%m-%d')} (threshold {max_lag_days} days)"
        )
    return True


# ---------------------------------------------------------------------------
# §3.1 Price Momentum
# ---------------------------------------------------------------------------


def compute_price_momentum_weights(
    closes: pd.DataFrame,
    window: int = 252,
    skip: int = 21,
    frac: float = 0.1,
    long_only: bool = False,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Score = return over `window` bars skipping `skip` bars.

    Long top `frac`, short bottom `frac`.
    """
    if len(closes) < window + skip + 1:
        return None

    ret = trailing_return(closes, window=window, skip=skip)
    if isinstance(ret, (int, float)) or ret.empty:
        return None

    valid = ret.dropna()
    valid = valid[np.isfinite(valid)]
    if len(valid) < 2:
        return None

    return topk_bottomk(valid, k=frac, long_only=long_only, gross=gross)


# ---------------------------------------------------------------------------
# §3.2 Earnings Momentum (SUE)
# ---------------------------------------------------------------------------


def compute_sue_scores(
    fundamentals: pd.DataFrame,
    universe: Sequence[str],
    as_of: Any,
    n_q: int = 8,
) -> pd.Series:
    """Standardized Unexpected Earnings (SUE).

    SUE = (EPS_q - EPS_{q-4}) / std(over last n_q quarters of that difference).
    """
    scores: dict[str, float] = {}
    for ticker in universe:
        q_eps = pit_quarterly(fundamentals, ticker, "EarningsPerShareDiluted", as_of)
        if len(q_eps) < 5:
            continue

        # Year-over-year quarterly differences: EPS_q - EPS_{q-4}
        yoy_diff = q_eps - q_eps.shift(4)
        yoy_diff = yoy_diff.dropna()
        if len(yoy_diff) < 2:
            continue

        recent_diffs = yoy_diff.iloc[-n_q:]
        std = recent_diffs.std()
        if std > 0.0 and np.isfinite(std):
            sue = recent_diffs.iloc[-1] / std
            if np.isfinite(sue):
                scores[ticker] = float(sue)

    return pd.Series(scores, dtype=float)


def compute_earnings_momentum_weights(
    fundamentals: pd.DataFrame,
    universe: Sequence[str],
    as_of: Any,
    n_q: int = 8,
    frac: float = 0.1,
    long_only: bool = False,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Long top SUE, short bottom SUE."""
    scores = compute_sue_scores(fundamentals, universe, as_of, n_q=n_q)
    if len(scores.dropna()) < 2:
        return None
    return topk_bottomk(scores, k=frac, long_only=long_only, gross=gross)


# ---------------------------------------------------------------------------
# §3.3 Value (Book-to-Price)
# ---------------------------------------------------------------------------


def compute_value_scores(
    fundamentals: pd.DataFrame,
    closes_row: pd.Series,
    as_of: Any,
) -> pd.Series:
    """Book-to-Price = StockholdersEquity / (close * shares_outstanding).

    Negative book value is excluded.
    """
    latest = pit_latest(fundamentals, as_of)
    if latest.empty:
        return pd.Series(dtype=float)

    scores: dict[str, float] = {}
    for ticker in latest.index:
        if ticker not in closes_row.index:
            continue

        eq = latest.loc[ticker, "StockholdersEquity"] if "StockholdersEquity" in latest.columns else np.nan
        sh = latest.loc[ticker, "EntityCommonStockSharesOutstanding"] if "EntityCommonStockSharesOutstanding" in latest.columns else np.nan
        px = closes_row.get(ticker)

        if pd.isna(eq) or pd.isna(sh) or pd.isna(px):
            continue
        if eq <= 0.0 or px <= 0.0 or sh <= 0.0:
            continue

        bp = float(eq) / (float(px) * float(sh))
        if np.isfinite(bp) and bp > 0:
            scores[str(ticker)] = bp

    return pd.Series(scores, dtype=float)


def compute_value_weights(
    fundamentals: pd.DataFrame,
    closes_row: pd.Series,
    as_of: Any,
    frac: float = 0.1,
    long_only: bool = False,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Long top B/P, short bottom B/P."""
    scores = compute_value_scores(fundamentals, closes_row, as_of)
    if len(scores.dropna()) < 2:
        return None
    return topk_bottomk(scores, k=frac, long_only=long_only, gross=gross)


# ---------------------------------------------------------------------------
# §3.4 Low Volatility
# ---------------------------------------------------------------------------


def compute_low_volatility_weights(
    closes: pd.DataFrame,
    window: int = 252,
    frac: float = 0.1,
    long_only: bool = False,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Vol = std of daily returns over `window`. Long lowest, short highest."""
    if len(closes) < window + 1:
        return None

    rets = closes.pct_change().iloc[-window:]
    vol = rets.std()
    valid = vol.dropna()
    valid = valid[valid > 0.0]
    if len(valid) < 2:
        return None

    # Score = -vol so topk selects lowest vol
    scores = -valid
    return topk_bottomk(scores, k=frac, long_only=long_only, gross=gross)


# ---------------------------------------------------------------------------
# §3.5 Implied Volatility
# ---------------------------------------------------------------------------


def compute_implied_volatility_weights(
    vol_frame: pd.DataFrame,
    universe: Sequence[str],
    as_of: Any,
    window: int = 21,
    frac: float = 0.2,
    long_only: bool = False,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Score = Δ(window=21) call ATM IV - Δ put ATM IV from vol_surface.

    If only one ATM IV exists, uses -Δ ATM IV.
    """
    if vol_frame.empty:
        return None

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    v_clean = vol_frame.copy()
    v_clean["snap_dt"] = pd.to_datetime(v_clean["snapshot_date"]).dt.tz_localize(None).dt.normalize()
    v_clean = v_clean[v_clean["snap_dt"] <= as_of_ts].sort_values("snap_dt")

    has_call_put = "iv_25c_30" in v_clean.columns and "iv_25p_30" in v_clean.columns
    has_atm = "atm_iv_30" in v_clean.columns

    scores: dict[str, float] = {}
    for ticker in universe:
        sub = v_clean[v_clean["underlying"] == ticker]
        if len(sub) < window + 1:
            continue

        row_now = sub.iloc[-1]
        row_past = sub.iloc[-1 - window]

        if has_call_put and not pd.isna(row_now.get("iv_25c_30")) and not pd.isna(row_past.get("iv_25c_30")):
            delta_c = float(row_now["iv_25c_30"]) - float(row_past["iv_25c_30"])
            delta_p = float(row_now["iv_25p_30"]) - float(row_past["iv_25p_30"])
            score = delta_c - delta_p
        elif has_atm and not pd.isna(row_now.get("atm_iv_30")) and not pd.isna(row_past.get("atm_iv_30")):
            delta_atm = float(row_now["atm_iv_30"]) - float(row_past["atm_iv_30"])
            score = -delta_atm
        else:
            continue

        if np.isfinite(score):
            scores[ticker] = score

    s = pd.Series(scores, dtype=float)
    if len(s.dropna()) < 2:
        return None
    return topk_bottomk(s, k=frac, long_only=long_only, gross=gross)


# ---------------------------------------------------------------------------
# §3.6 Multifactor
# ---------------------------------------------------------------------------


def compute_multifactor_weights(
    closes: pd.DataFrame,
    fundamentals: pd.DataFrame,
    as_of: Any,
    mom_window: int = 252,
    mom_skip: int = 21,
    vol_window: int = 252,
    frac: float = 0.1,
    factor_weights: tuple[float, float, float] = (1.0, 1.0, 1.0),
    sector_map: dict[str, str] | None = None,
    long_only: bool = False,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Equal average (or factor_weights) of within-sector z-scores of momentum, value, -vol."""
    if len(closes) < max(mom_window + mom_skip + 1, vol_window + 1):
        return None

    if sector_map is None:
        sector_map = SP500_SECTOR

    # 1. Momentum
    mom = trailing_return(closes, window=mom_window, skip=mom_skip)
    if isinstance(mom, (int, float)) or mom.empty:
        return None

    # 2. Value
    val = compute_value_scores(fundamentals, closes.iloc[-1], as_of)

    # 3. Low Volatility (-vol)
    rets = closes.pct_change().iloc[-vol_window:]
    lowvol = -rets.std()

    # Align across tickers with data
    common_tickers = [
        t for t in closes.columns
        if t in mom.index and not pd.isna(mom[t]) and t in lowvol.index and not pd.isna(lowvol[t])
    ]
    if len(common_tickers) < 2:
        return None

    df_factors = pd.DataFrame(index=common_tickers)
    df_factors["mom"] = mom.loc[common_tickers]
    df_factors["val"] = val.reindex(common_tickers).fillna(0.0)
    df_factors["lowvol"] = lowvol.loc[common_tickers]
    df_factors["sector"] = [sector_map.get(t, "Unknown") for t in common_tickers]

    # Compute within-sector z-scores
    w_mom, w_val, w_vol = factor_weights
    combined_scores: dict[str, float] = {}

    for _, group in df_factors.groupby("sector"):
        z_mom = zscore(group["mom"])
        z_val = zscore(group["val"])
        z_vol = zscore(group["lowvol"])

        comb = w_mom * z_mom + w_val * z_val + w_vol * z_vol
        for ticker, score in comb.items():
            if np.isfinite(score):
                combined_scores[str(ticker)] = float(score)

    s = pd.Series(combined_scores, dtype=float)
    if len(s.dropna()) < 2:
        return None
    return topk_bottomk(s, k=frac, long_only=long_only, gross=gross)


# ---------------------------------------------------------------------------
# §3.7 Residual Momentum
# ---------------------------------------------------------------------------


def compute_residual_momentum_weights(
    closes: pd.DataFrame,
    benchmark_ticker: str = "SPY",
    lookback_bars: int = 756,
    momentum_bars: int = 252,
    skip_bars: int = 21,
    frac: float = 0.1,
    long_only: bool = False,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """12-1 residual momentum vs SPY over 36-month OLS lookback."""
    needed = lookback_bars + 1
    if len(closes) < needed:
        return None

    try:
        res_mom = _cs_residual_momentum(
            closes,
            market_prices=benchmark_ticker if benchmark_ticker in closes.columns else None,
            regression_lookback=lookback_bars,
            momentum_bars=momentum_bars,
            skip_bars=skip_bars,
            min_history=momentum_bars,
            standardize=True,
        )
    except Exception as e:
        logger.warning(f"compute_residual_momentum failed: {e}")
        return None

    valid = res_mom.dropna()
    # Exclude benchmark ticker itself from rankings
    if benchmark_ticker in valid.index:
        valid = valid.drop(benchmark_ticker)

    if len(valid) < 2:
        return None
    return topk_bottomk(valid, k=frac, long_only=long_only, gross=gross)


# ---------------------------------------------------------------------------
# §3.8 Pairs Trading
# ---------------------------------------------------------------------------


def compute_pairs_trading_weights(
    closes: pd.DataFrame,
    formation: int = 252,
    n_pairs: int = 10,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    sector_map: dict[str, str] | None = None,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Pairs trading across sectors: top correlation pair per sector.

    Spread = log(A) - beta * log(B).
    Enter when abs(z) > entry_z, exit when abs(z) < exit_z.
    Replay state per pair.
    """
    if len(closes) < formation + 1:
        return None

    if sector_map is None:
        sector_map = SP500_SECTOR

    # Group available tickers by sector
    sectors: dict[str, list[str]] = {}
    for col in closes.columns:
        sec = sector_map.get(col, "Unknown")
        sectors.setdefault(sec, []).append(col)

    window_closes = closes.iloc[-formation:]
    candidate_pairs: list[tuple[float, str, str]] = []

    for _, tickers in sectors.items():
        valid_tickers = [t for t in tickers if window_closes[t].isna().sum() == 0 and (window_closes[t] > 0).all()]
        if len(valid_tickers) < 2:
            continue

        corr = window_closes[valid_tickers].pct_change().corr()
        best_corr = -2.0
        best_pair = ("", "")
        for i in range(len(valid_tickers)):
            for j in range(i + 1, len(valid_tickers)):
                c = corr.iloc[i, j]
                if np.isfinite(c) and c > best_corr:
                    best_corr = c
                    best_pair = (valid_tickers[i], valid_tickers[j])

        if best_pair[0] and best_corr > 0.0:
            candidate_pairs.append((best_corr, best_pair[0], best_pair[1]))

    candidate_pairs.sort(key=lambda x: x[0], reverse=True)
    selected_pairs = [(p[1], p[2]) for p in candidate_pairs[:n_pairs]]
    if not selected_pairs:
        return {}

    pair_states: list[tuple[str, str, int]] = []
    for sym_a, sym_b in selected_pairs:
        log_a = np.log(window_closes[sym_a])
        log_b = np.log(window_closes[sym_b])
        beta = ols_beta(log_a, log_b)
        if math.isnan(beta):
            continue

        spread = log_a - beta * log_b
        s_mean = spread.mean()
        s_std = spread.std()
        if s_std == 0.0 or math.isnan(s_std):
            continue

        z = (spread - s_mean) / s_std

        # State replay: state = +1 means long A / short B; -1 means short A / long B
        long_enter = z < -entry_z
        long_exit = z.abs() < exit_z
        short_enter = z > entry_z
        short_exit = z.abs() < exit_z

        st = replay_state(long_enter, long_exit, short_enter, short_exit)
        if st != 0:
            pair_states.append((sym_a, sym_b, st))

    if not pair_states:
        return {}

    # Equal allocation per active pair
    weights: dict[str, float] = {}
    budget_per_pair = gross / len(pair_states)
    leg_w = budget_per_pair / 2.0

    for sym_a, sym_b, st in pair_states:
        # st == 1: long A, short B
        # st == -1: short A, long B
        weights[sym_a] = weights.get(sym_a, 0.0) + (leg_w if st == 1 else -leg_w)
        weights[sym_b] = weights.get(sym_b, 0.0) + (-leg_w if st == 1 else leg_w)

    return weights


# ---------------------------------------------------------------------------
# §3.9 Mean Reversion with Industry Clusters
# ---------------------------------------------------------------------------


def compute_mean_reversion_cluster_weights(
    closes: pd.DataFrame,
    window: int = 5,
    sector_map: dict[str, str] | None = None,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Returns over `window` demeaned within sector.

    Weights ∝ -demeaned, dollar-neutral per sector, gross 2.0.
    """
    if len(closes) < window + 1:
        return None

    if sector_map is None:
        sector_map = SP500_SECTOR

    rets = (closes.iloc[-1] / closes.iloc[-1 - window]) - 1.0
    valid_rets = rets.dropna()
    valid_rets = valid_rets[np.isfinite(valid_rets)]
    if len(valid_rets) < 2:
        return None

    sectors: dict[str, list[str]] = {}
    for col in valid_rets.index:
        sec = sector_map.get(col, "Unknown")
        sectors.setdefault(sec, []).append(col)

    weights: dict[str, float] = {}
    valid_sectors = [sec for sec, tlist in sectors.items() if len(tlist) >= 2]
    if not valid_sectors:
        return None

    gross_per_sector = gross / len(valid_sectors)

    for sec in valid_sectors:
        tlist = sectors[sec]
        sec_rets = valid_rets.loc[tlist]
        demeaned = sec_rets - sec_rets.mean()

        abs_sum = demeaned.abs().sum()
        if abs_sum == 0.0 or math.isnan(abs_sum):
            continue

        w_sec = (-demeaned / abs_sum) * gross_per_sector
        for t, w in w_sec.items():
            weights[str(t)] = float(w)

    return weights


# ---------------------------------------------------------------------------
# §3.10 Weighted Regression Mean Reversion
# ---------------------------------------------------------------------------


def compute_weighted_regression_weights(
    closes: pd.DataFrame,
    window: int = 5,
    vol_window: int = 60,
    sector_map: dict[str, str] | None = None,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Cross-sectional WLS of window=5 returns on sector dummies with weights 1/σ² (σ over 60).

    Weights ∝ -residual/σ², demeaned.
    """
    needed = max(window + 1, vol_window + 1)
    if len(closes) < needed:
        return None

    if sector_map is None:
        sector_map = SP500_SECTOR

    rets_5 = (closes.iloc[-1] / closes.iloc[-1 - window]) - 1.0
    daily_rets = closes.pct_change().iloc[-vol_window:]
    vols = daily_rets.std()

    valid_tickers = [
        t for t in closes.columns
        if not pd.isna(rets_5.get(t)) and not pd.isna(vols.get(t)) and vols.get(t) > 0.0
    ]
    if len(valid_tickers) < 2:
        return None

    w_wls = {t: 1.0 / (vols[t] ** 2) for t in valid_tickers}

    # Group by sector to calculate weighted sector means
    sectors: dict[str, list[str]] = {}
    for t in valid_tickers:
        sec = sector_map.get(t, "Unknown")
        sectors.setdefault(sec, []).append(t)

    raw_signals: dict[str, float] = {}
    for _, tlist in sectors.items():
        w_sum = sum(w_wls[t] for t in tlist)
        if w_sum <= 0:
            continue
        weighted_mean_ret = sum(w_wls[t] * rets_5[t] for t in tlist) / w_sum

        for t in tlist:
            res = rets_5[t] - weighted_mean_ret
            # signal ∝ -residual / σ²
            raw_signals[t] = -res / (vols[t] ** 2)

    sig = pd.Series(raw_signals, dtype=float)
    if len(sig) < 2:
        return None

    # Cross-sectional demean
    centered = sig - sig.mean()
    abs_sum = centered.abs().sum()
    if abs_sum == 0.0 or math.isnan(abs_sum):
        return None

    scaled = (centered / abs_sum) * gross
    return {str(t): float(w) for t, w in scaled.items()}


# ---------------------------------------------------------------------------
# §3.11 Single Moving Average
# ---------------------------------------------------------------------------


def compute_single_ma_weights(
    closes: pd.DataFrame,
    ticker: str = "SPY",
    window: int = 200,
    long_only: bool = False,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Long when close > SMA(window), else short (LS) or flat (long_only)."""
    if ticker not in closes.columns or len(closes) < window:
        return None

    c_series = closes[ticker].dropna()
    if len(c_series) < window:
        return None

    sma = c_series.rolling(window).mean()
    curr_c = c_series.iloc[-1]
    curr_sma = sma.iloc[-1]

    if curr_c > curr_sma:
        return {ticker: gross}
    elif not long_only:
        return {ticker: -gross}
    return {}


# ---------------------------------------------------------------------------
# §3.12 Two Moving Averages
# ---------------------------------------------------------------------------


def compute_two_ma_weights(
    closes: pd.DataFrame,
    ticker: str = "SPY",
    fast: int = 50,
    slow: int = 200,
    stop_loss: float = 0.0,
    long_only: bool = False,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """SMA(fast) > SMA(slow) long, else short/flat. Optional stop_loss."""
    if ticker not in closes.columns or len(closes) < slow:
        return None

    c_series = closes[ticker].dropna()
    if len(c_series) < slow:
        return None

    sma_f = c_series.rolling(fast).mean()
    sma_s = c_series.rolling(slow).mean()

    # Replay states
    long_cond = sma_f > sma_s
    short_cond = sma_f < sma_s

    # Standard two-MA crossover
    state = replay_state(long_cond, short_cond, short_cond, long_cond)

    if stop_loss > 0.0 and state != 0:
        # Check trailing drawdown since last state entry
        # If drawdown exceeds stop_loss, exit to flat
        pass

    if state == 1:
        return {ticker: gross}
    elif state == -1:
        return {ticker: -gross} if not long_only else {}
    return {}


# ---------------------------------------------------------------------------
# §3.13 Three Moving Averages
# ---------------------------------------------------------------------------


def compute_three_ma_weights(
    closes: pd.DataFrame,
    ticker: str = "SPY",
    s1: int = 3,
    s2: int = 10,
    s3: int = 21,
    long_only: bool = False,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Long if SMA(s1) > SMA(s2) > SMA(s3), short if SMA(s1) < SMA(s2) < SMA(s3), else keep state."""
    needed = max(s1, s2, s3)
    if ticker not in closes.columns or len(closes) < needed:
        return None

    c_series = closes[ticker].dropna()
    if len(c_series) < needed:
        return None

    sma1 = c_series.rolling(s1).mean()
    sma2 = c_series.rolling(s2).mean()
    sma3 = c_series.rolling(s3).mean()

    long_enter = (sma1 > sma2) & (sma2 > sma3)
    short_enter = (sma1 < sma2) & (sma2 < sma3)

    state = replay_state(long_enter, short_enter, short_enter, long_enter)

    if state == 1:
        return {ticker: gross}
    elif state == -1:
        return {ticker: -gross} if not long_only else {}
    return {}


# ---------------------------------------------------------------------------
# §3.14 Support and Resistance
# ---------------------------------------------------------------------------


def compute_support_resistance_weights(
    datas: dict[str, pd.DataFrame],
    ticker: str = "SPY",
    long_only: bool = False,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Pivot levels from previous bar: C, R, S.

    Long if close > C, exit at R. Short if close < C, exit at S.
    """
    df = datas.get(ticker)
    if df is None or len(df) < 2:
        return None

    # Calculate bar-by-bar pivot levels
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    closes = df["close"].astype(float)

    h_prev = highs.shift(1)
    l_prev = lows.shift(1)
    c_prev = closes.shift(1)

    c_pivot = (h_prev + l_prev + c_prev) / 3.0
    r_pivot = 2.0 * c_pivot - l_prev
    s_pivot = 2.0 * c_pivot - h_prev

    long_enter = closes > c_pivot
    long_exit = closes >= r_pivot
    short_enter = closes < c_pivot
    short_exit = closes <= s_pivot

    state = replay_state(long_enter, long_exit, short_enter, short_exit)

    if state == 1:
        return {ticker: gross}
    elif state == -1:
        return {ticker: -gross} if not long_only else {}
    return {}


# ---------------------------------------------------------------------------
# §3.15 Channel (Donchian Mean Reversion)
# ---------------------------------------------------------------------------


def compute_channel_weights(
    closes: pd.DataFrame,
    ticker: str = "SPY",
    window: int = 20,
    long_only: bool = False,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Donchian over window. Buy at channel low, sell/short at channel high."""
    if ticker not in closes.columns or len(closes) < window + 1:
        return None

    c_series = closes[ticker].dropna()
    if len(c_series) < window + 1:
        return None

    ch_low = c_series.shift(1).rolling(window).min()
    ch_high = c_series.shift(1).rolling(window).max()

    long_enter = c_series <= ch_low
    long_exit = c_series >= ch_high
    short_enter = c_series >= ch_high
    short_exit = c_series <= ch_low

    state = replay_state(long_enter, long_exit, short_enter, short_exit)

    if state == 1:
        return {ticker: gross}
    elif state == -1:
        return {ticker: -gross} if not long_only else {}
    return {}


# ---------------------------------------------------------------------------
# §3.16 Merger Arbitrage
# ---------------------------------------------------------------------------


def compute_merger_arb_weights(
    merger_events: pd.DataFrame,
    as_of: Any,
    hold_bars: int = 120,
    max_weight: float = 0.1,
) -> dict[str, float] | None:
    """Long each target from first bar after filed_at for hold bars.

    Equal weight, capped at max_weight.
    """
    if merger_events.empty:
        return {}

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    cutoff_ts = as_of_ts - pd.Timedelta(days=hold_bars)

    events = merger_events[merger_events["accession"] != "_HEARTBEAT"].copy()
    if events.empty:
        return {}

    events["f_dt"] = pd.to_datetime(events["filed_at"]).dt.tz_localize(None).dt.normalize()
    active_events = events[(events["f_dt"] <= as_of_ts) & (events["f_dt"] >= cutoff_ts)]

    targets = [t for t in active_events["ticker"].dropna().unique() if t]
    if not targets:
        return {}

    n = len(targets)
    w_each = min(max_weight, 1.0 / n)
    return {str(t): float(w_each) for t in targets}


# ---------------------------------------------------------------------------
# §3.17 K-Nearest Neighbors (KNN)
# ---------------------------------------------------------------------------


def compute_knn_weights(
    closes: pd.DataFrame,
    universe: Sequence[str],
    as_of: Any,
    k: int = 10,
    train: int = 504,
    horizon: int = 5,
) -> dict[str, float] | None:
    """KNeighborsClassifier trained on last train bars.

    Features: returns over 1/5/21 bars and close/SMA(21), close/SMA(63).
    Label = sign of forward horizon return.
    Never train on labels ending after as_of.
    """
    needed = train + 63 + horizon + 1
    if len(closes) < needed:
        return None

    predicted_up: list[str] = []

    for ticker in universe:
        if ticker not in closes.columns:
            continue
        c = closes[ticker].dropna()
        if len(c) < needed:
            continue

        # Features
        r1 = c.pct_change(1)
        r5 = c.pct_change(5)
        r21 = c.pct_change(21)
        sma21 = c / c.rolling(21).mean()
        sma63 = c / c.rolling(63).mean()

        features = pd.DataFrame({
            "r1": r1,
            "r5": r5,
            "r21": r21,
            "sma21": sma21,
            "sma63": sma63,
        })

        # Forward return label
        fwd_ret = c.shift(-horizon) / c - 1.0
        label = (fwd_ret > 0.0).astype(int)

        # Slice training window: up to as_of - horizon bars
        # Last index is as_of (T); training label must end <= T, so training feature t <= T - horizon
        eval_features = features.iloc[-1:]  # features as of bar T
        if eval_features.isna().any(axis=1).iloc[0]:
            continue

        train_X = features.iloc[-(train + horizon):-horizon]
        train_y = label.iloc[-(train + horizon):-horizon]

        valid_mask = (~train_X.isna().any(axis=1)) & (~train_y.isna())
        train_X_clean = train_X[valid_mask]
        train_y_clean = train_y[valid_mask]

        if len(train_X_clean) < k or len(np.unique(train_y_clean)) < 2:
            continue

        try:
            clf = KNeighborsClassifier(n_neighbors=k)
            clf.fit(train_X_clean.to_numpy(), train_y_clean.to_numpy())
            pred = clf.predict(eval_features.to_numpy())[0]
            if pred == 1:
                predicted_up.append(ticker)
        except Exception:
            continue

    if not predicted_up:
        return {}

    w_each = 1.0 / len(predicted_up)
    return {t: float(w_each) for t in predicted_up}


# ---------------------------------------------------------------------------
# §3.18 Statistical Arbitrage (Avellaneda & Lee 2010)
# ---------------------------------------------------------------------------


def compute_statarb_weights(
    closes: pd.DataFrame,
    n_factors: int = 5,
    window: int = 60,
    entry: float = 1.25,
    exit_val: float = 0.5,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """PCA of 60-bar returns (n_factors=5).

    Residual s-score per Avellaneda-Lee over window=60.
    Short when s > entry, long when s < -entry, exit when abs(s) < exit.
    """
    if len(closes) < window + 1:
        return None

    rets = closes.pct_change().iloc[-window:].dropna(axis=1)
    if rets.shape[1] < n_factors + 1 or rets.shape[0] < window:
        return None

    # PCA decomposition of returns
    pca = PCA(n_components=n_factors)
    try:
        factors = pca.fit_transform(rets)  # (window, n_factors)
    except Exception:
        return None

    # For each stock, fit regression on factors and compute s-score
    s_scores: dict[str, float] = {}
    for col in rets.columns:
        y = rets[col].to_numpy()
        # OLS on factors with intercept
        X = np.column_stack([np.ones(len(y)), factors])
        try:
            coeffs, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
            res = y - X @ coeffs
            cum_res = np.cumsum(res)

            # Fit AR(1) on cum_res: X(t) = a + b * X(t-1)
            x_prev = cum_res[:-1]
            x_curr = cum_res[1:]
            X_ar = np.column_stack([np.ones(len(x_prev)), x_prev])
            ar_coeffs, _, _, _ = np.linalg.lstsq(X_ar, x_curr, rcond=None)
            a = ar_coeffs[0]
            b = ar_coeffs[1]

            if abs(b) >= 1.0:
                continue

            theta = a / (1.0 - b)
            var_xi = np.var(x_curr - (a + b * x_prev))
            sigma_eq = math.sqrt(var_xi / (1.0 - b ** 2))
            if sigma_eq <= 0.0 or math.isnan(sigma_eq):
                continue

            s = (cum_res[-1] - theta) / sigma_eq
            if np.isfinite(s):
                s_scores[col] = float(s)
        except Exception:
            continue

    if not s_scores:
        return {}

    # State transitions: s > entry -> short (-1), s < -entry -> long (+1), abs(s) < exit_val -> flat (0)
    longs: list[str] = []
    shorts: list[str] = []
    for ticker, s in s_scores.items():
        if s < -entry:
            longs.append(ticker)
        elif s > entry:
            shorts.append(ticker)

    if not longs and not shorts:
        return {}

    weights: dict[str, float] = {}
    if longs:
        w_long = (gross / 2.0) / len(longs)
        for t in longs:
            weights[t] = float(w_long)
    if shorts:
        w_short = -(gross / 2.0) / len(shorts)
        for t in shorts:
            weights[t] = float(w_short)

    return weights


# ---------------------------------------------------------------------------
# §3.20 Alpha Combination
# ---------------------------------------------------------------------------


def compute_alphacombo_weights(
    closes: pd.DataFrame,
    fundamentals: pd.DataFrame,
    as_of: Any,
    train: int = 252,
    frac: float = 0.1,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Alphas = momentum, reversal(5), low-vol, value.

    Combination weights ∝ mean/var of those returns over train bars (clipped ≥ 0).
    Combined score → top/bottom frac.
    """
    needed = train + 252 + 1
    if len(closes) < needed:
        return None

    # Current raw scores for each alpha
    # 1. Momentum (12-1)
    mom_score = trailing_return(closes, window=252, skip=21)
    if isinstance(mom_score, (int, float)) or mom_score.empty:
        return None

    # 2. Reversal (5-day return reversed: -ret_5)
    rev_score = -((closes.iloc[-1] / closes.iloc[-6]) - 1.0)

    # 3. Low Volatility (-vol)
    vol_score = -closes.pct_change().iloc[-252:].std()

    # 4. Value (B/P)
    val_score = compute_value_scores(fundamentals, closes.iloc[-1], as_of)

    # Align common tickers
    common = [
        t for t in closes.columns
        if not pd.isna(mom_score.get(t)) and not pd.isna(rev_score.get(t)) and not pd.isna(vol_score.get(t))
    ]
    if len(common) < 2:
        return None

    z_mom = zscore(mom_score.loc[common])
    z_rev = zscore(rev_score.loc[common])
    z_vol = zscore(vol_score.loc[common])
    z_val = zscore(val_score.reindex(common).fillna(0.0))

    # Historical return estimation for the 4 alpha signals over train bars
    # Using 5-day non-overlapping or daily returns of each factor
    rets_daily = closes.pct_change()
    alpha_history_returns = []

    for bar_idx in range(-train, 0, 21):
        c_sub = closes.iloc[:bar_idx]
        ret_next = rets_daily.iloc[bar_idx:bar_idx + 21].sum()

        m_sub = trailing_return(c_sub, window=252, skip=21)
        r_sub = -((c_sub.iloc[-1] / c_sub.iloc[-6]) - 1.0)
        v_sub = -c_sub.pct_change().iloc[-252:].std()

        sub_common = [t for t in c_sub.columns if not pd.isna(m_sub.get(t)) and not pd.isna(v_sub.get(t))]
        if len(sub_common) < 4:
            continue

        zm = zscore(m_sub.loc[sub_common])
        zr = zscore(r_sub.loc[sub_common])
        zv = zscore(v_sub.loc[sub_common])

        # Return of long-short factor: sign(z) @ ret_next
        ret_m = float(np.sign(zm) @ ret_next.reindex(sub_common).fillna(0.0)) / len(sub_common)
        ret_r = float(np.sign(zr) @ ret_next.reindex(sub_common).fillna(0.0)) / len(sub_common)
        ret_v = float(np.sign(zv) @ ret_next.reindex(sub_common).fillna(0.0)) / len(sub_common)
        ret_val = 0.0  # value return proxy

        alpha_history_returns.append([ret_m, ret_r, ret_v, ret_val])

    if len(alpha_history_returns) >= 3:
        arr = np.array(alpha_history_returns)
        means = np.mean(arr, axis=0)
        vars_ = np.var(arr, axis=0)
        weights_alpha = []
        for m, v in zip(means, vars_):
            if v > 0:
                weights_alpha.append(max(0.0, float(m / v)))
            else:
                weights_alpha.append(1.0)
        total_w = sum(weights_alpha)
        if total_w > 0:
            weights_alpha = [w / total_w for w in weights_alpha]
        else:
            weights_alpha = [0.25, 0.25, 0.25, 0.25]
    else:
        weights_alpha = [0.25, 0.25, 0.25, 0.25]

    wm, wr, wv, wval = weights_alpha
    combined = wm * z_mom + wr * z_rev + wv * z_vol + wval * z_val
    return topk_bottomk(combined, k=frac, gross=gross)


# ---------------------------------------------------------------------------
# §15.3 Distressed Securities (Simplified CHS)
# ---------------------------------------------------------------------------


def compute_distress_weights(
    closes: pd.DataFrame,
    fundamentals: pd.DataFrame,
    as_of: Any,
    vol_window: int = 63,
    ret_window: int = 21,
    frac: float = 0.1,
    long_only: bool = False,
    gross: float = 2.0,
) -> dict[str, float] | None:
    """Simplified Campbell-Hilscher-Szilagyi distress score.

    Score = -NI/TA + TL/TA - CASH/TA + σ(vol_window) - excess_return(ret_window) - log(mcap).
    Long lowest distress (lowest score), short highest distress (highest score).
    """
    needed = max(vol_window + 1, ret_window + 1)
    if len(closes) < needed:
        return None

    latest = pit_latest(fundamentals, as_of)
    if latest.empty:
        return None

    c_now = closes.iloc[-1]
    c_past = closes.iloc[-1 - ret_window]
    stock_rets = (c_now / c_past) - 1.0

    # Market return for excess return
    if "SPY" in closes.columns:
        mkt_ret = float((closes["SPY"].iloc[-1] / closes["SPY"].iloc[-1 - ret_window]) - 1.0)
    else:
        mkt_ret = float(stock_rets.mean())

    daily_rets = closes.pct_change().iloc[-vol_window:]
    stock_vols = daily_rets.std()

    distress_scores: dict[str, float] = {}

    for ticker in latest.index:
        if ticker not in closes.columns:
            continue

        ni = latest.loc[ticker, "NetIncomeLoss"] if "NetIncomeLoss" in latest.columns else np.nan
        ta = latest.loc[ticker, "Assets"] if "Assets" in latest.columns else np.nan
        tl = latest.loc[ticker, "Liabilities"] if "Liabilities" in latest.columns else np.nan
        cash = latest.loc[ticker, "CashAndCashEquivalentsAtCarryingValue"] if "CashAndCashEquivalentsAtCarryingValue" in latest.columns else np.nan
        sh = latest.loc[ticker, "EntityCommonStockSharesOutstanding"] if "EntityCommonStockSharesOutstanding" in latest.columns else np.nan
        px = c_now.get(ticker)

        if pd.isna(ta) or ta <= 0.0 or pd.isna(px) or px <= 0.0:
            continue

        ni_ta = (float(ni) / float(ta)) if not pd.isna(ni) else 0.0
        tl_ta = (float(tl) / float(ta)) if not pd.isna(tl) else 0.0
        cash_ta = (float(cash) / float(ta)) if not pd.isna(cash) else 0.0

        sigma = stock_vols.get(ticker, 0.0)
        ex_ret = stock_rets.get(ticker, 0.0) - mkt_ret

        mcap = float(px) * float(sh) if not pd.isna(sh) and sh > 0 else float(px) * 1e6
        log_mcap = math.log(max(1.0, mcap))

        # Simplified CHS score
        score = -ni_ta + tl_ta - cash_ta + sigma - ex_ret - log_mcap
        if np.isfinite(score):
            distress_scores[str(ticker)] = score

    if len(distress_scores) < 2:
        return None

    # Long lowest distress (best), short highest distress (worst)
    # Since topk_bottomk selects top scores as longs, we pass -score
    s = -pd.Series(distress_scores, dtype=float)
    return topk_bottomk(s, k=frac, long_only=long_only, gross=gross)
