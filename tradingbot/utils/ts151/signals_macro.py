"""Pure signal functions for Real Estate, Crypto, and Macro TS151 bots (PLAN-ts151.md S9).

Covers:
- 16.2 Real Estate Diversity (Mean-Variance with weights & vol cap)
- 16.3 Intra-Asset Diversification (Equal Risk Contribution)
- 16.4 Real Estate Momentum (Multi-horizon return average)
- 16.5 Real Estate Inflation Hedge (CPI YoY trend switch: VNQ vs IEF)
- 18.2 Crypto ANN (Multi-layer Perceptron on lagged returns)
- 18.3 Crypto Sentiment (Alternative.me Fear & Greed index)
- 19.2 Fundamental Macro (Four-quadrant Growth & Inflation regime allocation)
- 19.3 Global Inflation (Rolling inflation beta allocation vs 60/40 benchmark)
- 19.5 Economic Announcement (FOMC pre-announcement drift)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable, Mapping
import urllib.request

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.neural_network import MLPClassifier

from tradingbot.utils.quantsmd_common import erc_weights
from tradingbot.utils.ts151 import fred

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# 16.2 Real Estate Diversity (Mean-Variance Optimization)
# -------------------------------------------------------------------------


def real_estate_diversity_weights(
    closes: pd.DataFrame,
    window: int = 252,
    min_weight: float = 0.05,
    max_weight: float = 0.5,
    max_vol: float = 0.15,
) -> dict[str, float]:
    """Compute constrained minimum-variance weights scaled by a volatility cap.

    §16.2: Real estate portfolio diversification using mean-variance optimization.
    Minimizes w^T Cov w subject to sum(w) = 1, min_weight <= w_i <= max_weight.
    If the annualized portfolio volatility exceeds max_vol, weights are scaled down
    proportional to max_vol / port_vol (cash buffer).
    """
    if len(closes) < window + 1:
        return {}

    rets = closes.iloc[-window - 1 :].pct_change().dropna(how="all")
    valid_cols = [c for c in closes.columns if c in rets.columns and rets[c].notna().sum() >= window * 0.8]
    n = len(valid_cols)
    if n == 0:
        return {}

    rets = rets[valid_cols].fillna(0.0)
    cov = rets.cov().to_numpy()

    # If min_weight * n > 1.0 or max_weight * n < 1.0, adjust bounds safely
    eff_min = min(min_weight, 1.0 / n)
    eff_max = max(max_weight, 1.0 / n)

    w0 = np.full(n, 1.0 / n)
    bounds = [(eff_min, eff_max) for _ in range(n)]
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

    def objective(w: np.ndarray) -> float:
        return float(0.5 * w @ cov @ w)

    res = minimize(objective, w0, bounds=bounds, constraints=constraints, method="SLSQP")
    if not res.success or np.any(np.isnan(res.x)):
        w_opt = w0
    else:
        w_opt = res.x

    # Normalize sum to 1.0
    w_sum = np.sum(w_opt)
    if w_sum > 0:
        w_opt = w_opt / w_sum

    # Annualized portfolio volatility (252 trading days)
    port_var = float(w_opt @ cov @ w_opt)
    port_vol = float(np.sqrt(max(0.0, port_var)) * np.sqrt(252))

    if port_vol > max_vol and port_vol > 0:
        scale = max_vol / port_vol
        w_opt = w_opt * scale

    return {valid_cols[i]: float(w_opt[i]) for i in range(n) if w_opt[i] > 1e-6}


# -------------------------------------------------------------------------
# 16.3 Intra-Asset Diversification (Equal Risk Contribution)
# -------------------------------------------------------------------------


def intra_asset_div_weights(
    closes: pd.DataFrame,
    window: int = 252,
) -> dict[str, float]:
    """Compute Equal Risk Contribution (ERC) portfolio weights across assets.

    §16.3: Intra-asset class diversification allocating risk equally among REITs.
    """
    if len(closes) < window + 1:
        return {}

    rets = closes.iloc[-window - 1 :].pct_change().dropna(how="all")
    valid_cols = [c for c in closes.columns if c in rets.columns and rets[c].notna().sum() >= window * 0.8]
    n = len(valid_cols)
    if n == 0:
        return {}

    rets = rets[valid_cols].fillna(0.0)
    cov = rets.cov().to_numpy()

    try:
        w = erc_weights(cov)
        if w is None or len(w) != n or np.any(np.isnan(w)):
            return {c: 1.0 / n for c in valid_cols}
        w_sum = np.sum(w)
        if w_sum > 0:
            w = w / w_sum
        return {valid_cols[i]: float(w[i]) for i in range(n) if w[i] > 1e-6}
    except Exception as exc:
        logger.warning("erc_weights failed: %s; falling back to 1/N", exc)
        return {c: 1.0 / n for c in valid_cols}


# -------------------------------------------------------------------------
# 16.4 Real Estate Momentum
# -------------------------------------------------------------------------


def real_estate_momentum_weights(
    closes: pd.DataFrame,
    lookbacks: tuple[int, ...] = (21, 63, 126, 252),
    k: int = 2,
    gross: float = 1.0,
) -> dict[str, float]:
    """Compute multi-horizon momentum scores and select top k assets.

    §16.4: Score = average of returns over lookbacks (default: 21, 63, 126, 252 bars).
    Long top k names equal weight.
    """
    max_lookback = max(lookbacks)
    if len(closes) < max_lookback + 1:
        return {}

    scores: dict[str, float] = {}
    for col in closes.columns:
        s = closes[col].dropna()
        if len(s) < max_lookback + 1:
            continue
        rets = [float((s.iloc[-1] / s.iloc[-1 - lb]) - 1.0) for lb in lookbacks]
        scores[col] = float(np.mean(rets))

    if not scores:
        return {}

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = ranked[:k]
    if not top:
        return {}

    w_each = gross / len(top)
    return {ticker: float(w_each) for ticker, _ in top}


# -------------------------------------------------------------------------
# 16.5 Real Estate Inflation Hedge
# -------------------------------------------------------------------------


def real_estate_inflation_weights(
    as_of: pd.Timestamp | Any,
    cpi_series: pd.Series | None = None,
    window: int = 3,
    asset_rising: str = "VNQ",
    asset_falling: str = "IEF",
) -> dict[str, float]:
    """Switch between REITs (VNQ) and Treasuries (IEF) based on CPI YoY trend.

    §16.5: When CPI YoY is rising over `window` months, allocate 100% to VNQ (inflation hedge),
    else 100% to IEF.
    """
    if cpi_series is None or cpi_series.empty:
        return {asset_rising: 1.0}

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    cpi_pit = cpi_series[cpi_series.index <= as_of_ts].dropna()
    if len(cpi_pit) < 12 + window:
        return {asset_rising: 1.0}

    # CPI YoY: percentage change over 12 months (or ~12 observation steps)
    cpi_yoy = (cpi_pit / cpi_pit.shift(12) - 1.0) * 100.0
    cpi_yoy = cpi_yoy.dropna()
    if len(cpi_yoy) < window + 1:
        return {asset_rising: 1.0}

    # Check if rising over window months
    yoy_change = float(cpi_yoy.iloc[-1] - cpi_yoy.iloc[-1 - window])
    if yoy_change > 0:
        return {asset_rising: 1.0}
    return {asset_falling: 1.0}


# -------------------------------------------------------------------------
# 18.2 Crypto ANN
# -------------------------------------------------------------------------


def crypto_ann_weights(
    closes: pd.DataFrame,
    as_of: pd.Timestamp | Any,
    lags: tuple[int, ...] = (1, 3, 7, 14, 30),
    horizon: int = 1,
    train: int = 730,
    threshold: float = 0.55,
    hidden_layer_sizes: tuple[int, ...] = (16, 8),
    random_state: int = 42,
    gross: float = 1.0,
) -> dict[str, float]:
    """Multi-layer Perceptron (ANN) on lagged returns for crypto trend forecasting.

    §18.2: Per coin, MLPClassifier on lagged returns (1, 3, 7, 14, 30).
    Trained on the last `train` available historical bars without lookahead leakage.
    Long if predicted probability of positive forward return > threshold.
    """
    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    closes_pit = closes[closes.index <= as_of_ts]

    long_coins: list[str] = []

    for col in closes_pit.columns:
        s = closes_pit[col].dropna()
        if len(s) < max(lags) + horizon + 20:
            continue

        feat_df = pd.DataFrame(index=s.index)
        for lag in lags:
            feat_df[f"ret_{lag}"] = s.pct_change(lag)

        fwd_ret = s.pct_change(horizon).shift(-horizon)
        target = (fwd_ret > 0).astype(int)

        valid_mask = feat_df.notna().all(axis=1) & target.notna()
        # Strictly enforce no look-ahead: bar t label uses price at t+horizon,
        # so bar t cannot be in the training set if t >= as_of_pos - horizon + 1
        as_of_iloc = len(s) - 1
        train_eligible_mask = valid_mask.copy()
        # The last `horizon` bars up to as_of cannot be used for training
        train_eligible_mask.iloc[max(0, as_of_iloc - horizon + 1) :] = False

        X_train_full = feat_df[train_eligible_mask]
        y_train_full = target[train_eligible_mask]

        if len(X_train_full) < 30:
            continue

        # Use the last `train` eligible bars
        X_train = X_train_full.iloc[-train:]
        y_train = y_train_full.iloc[-train:]

        # Must have at least two classes to train classifier
        if len(np.unique(y_train)) < 2:
            continue

        # Feature vector at as_of (the current bar)
        x_eval = feat_df.iloc[as_of_iloc : as_of_iloc + 1]
        if x_eval.isna().any(axis=1).iloc[0]:
            continue

        try:
            mlp = MLPClassifier(
                hidden_layer_sizes=hidden_layer_sizes,
                activation="relu",
                max_iter=200,
                random_state=random_state,
                solver="adam",
            )
            mlp.fit(X_train.to_numpy(), y_train.to_numpy())
            probs = mlp.predict_proba(x_eval.to_numpy())[0]
            # Prob of class 1 (up)
            classes = list(mlp.classes_)
            idx_up = classes.index(1) if 1 in classes else -1
            p_up = float(probs[idx_up]) if idx_up >= 0 else 0.0

            if p_up > threshold:
                long_coins.append(col)
        except Exception as exc:
            logger.warning("ANN training failed for %s: %s", col, exc)

    if not long_coins:
        return {}

    w_each = gross / len(long_coins)
    return {c: float(w_each) for c in long_coins}


# -------------------------------------------------------------------------
# 18.3 Crypto Sentiment (Alternative.me Fear & Greed)
# -------------------------------------------------------------------------


def fetch_crypto_fng(
    cache_dir: Path | str | None = None,
    refresh: bool = False,
) -> pd.Series:
    """Fetch and cache crypto Fear & Greed index from alternative.me.

    The index date D is point-in-time and can only be used from D+1 onwards.
    """
    cache_base = Path(cache_dir or os.environ.get("CACHE_DIR", "/tmp"))
    cache_file = cache_base / "crypto_fng.pkl"

    if not refresh and cache_file.exists():
        try:
            cached = pd.read_pickle(cache_file)
            if isinstance(cached, pd.Series) and not cached.empty:
                return cached
        except Exception as exc:
            logger.warning("Failed reading crypto F&G cache from %s: %s", cache_file, exc)

    url = "https://api.alternative.me/fng/?limit=0"
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"Failed fetching crypto Fear & Greed: {exc}") from exc

    raw_items = data.get("data", [])
    if not raw_items:
        raise ValueError("Crypto Fear & Greed returned empty payload")

    records = []
    for item in raw_items:
        ts = pd.to_datetime(int(item["timestamp"]), unit="s", utc=True).tz_localize(None).normalize()
        val = float(item["value"])
        records.append({"date": ts, "fng": val})

    df = pd.DataFrame(records).sort_values("date").drop_duplicates(subset=["date"]).set_index("date")
    series = df["fng"].astype(float)

    try:
        cache_base.mkdir(parents=True, exist_ok=True)
        series.to_pickle(cache_file)
    except Exception as exc:
        logger.warning("Could not write crypto F&G cache to %s: %s", cache_file, exc)

    return series


def crypto_sentiment_weights(
    as_of: pd.Timestamp | Any,
    fng_series: pd.Series | None,
    asset: str = "BTC-USD",
    long_above: float = 50.0,
    buy_below: float = 25.0,
    contrarian: bool = False,
) -> dict[str, float]:
    """Calculate sentiment weights based on Crypto Fear & Greed index.

    §18.3:
    The value for date D is used from D+1 (point-in-time strictly strictly < as_of).
    - If contrarian=False (momentum): long when F&G > long_above (default: 50.0).
    - If contrarian=True: long when F&G < buy_below (default: 25.0).
    """
    if fng_series is None or fng_series.empty:
        return {}

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    # Strictest point-in-time: value for date D is available from D+1
    pit_fng = fng_series[fng_series.index < as_of_ts].dropna()
    if pit_fng.empty:
        return {}

    latest_fng = float(pit_fng.iloc[-1])

    if contrarian:
        if latest_fng < buy_below:
            return {asset: 1.0}
        return {}
    else:
        if latest_fng > long_above:
            return {asset: 1.0}
        return {}


# -------------------------------------------------------------------------
# 19.2 Fundamental Macro (Regime Quadrant Allocation)
# -------------------------------------------------------------------------

DEFAULT_MACRO_ALLOCATIONS: dict[str, tuple[str, ...]] = {
    "growth_up_infl_down": ("SPY", "EFA"),
    "growth_up_infl_up": ("DBC", "GLD", "EEM"),
    "growth_down_infl_up": ("GLD", "TIP", "IEF"),
    "growth_down_infl_down": ("TLT", "IEF"),
}


def fundamental_macro_weights(
    as_of: pd.Timestamp | Any,
    indpro_series: pd.Series | None,
    cpi_series: pd.Series | None,
    window: int = 3,
    allocations: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, float]:
    """Four-quadrant macroeconomic growth and inflation asset allocation.

    §19.2:
    - Growth = INDPRO YoY rising/falling over `window` months.
    - Inflation = CPI YoY rising/falling over `window` months.
    Quadrant mapping:
      - growth↑ infl↓: SPY / EFA
      - growth↑ infl↑: DBC / GLD / EEM
      - growth↓ infl↑: GLD / TIP / IEF
      - growth↓ infl↓: TLT / IEF
    Weights within the chosen quadrant are equal-weighted.
    """
    allocs = allocations or DEFAULT_MACRO_ALLOCATIONS

    if indpro_series is None or indpro_series.empty or cpi_series is None or cpi_series.empty:
        # Fallback to balanced multi-asset or growth_up_infl_down
        assets = allocs.get("growth_up_infl_down", ("SPY", "EFA"))
        return {a: 1.0 / len(assets) for a in assets}

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()

    # Point-in-time filtering (FRED series already shifted by publication lag)
    indpro_pit = indpro_series[indpro_series.index <= as_of_ts].dropna()
    cpi_pit = cpi_series[cpi_series.index <= as_of_ts].dropna()

    if len(indpro_pit) < 12 + window or len(cpi_pit) < 12 + window:
        assets = allocs.get("growth_up_infl_down", ("SPY", "EFA"))
        return {a: 1.0 / len(assets) for a in assets}

    indpro_yoy = (indpro_pit / indpro_pit.shift(12) - 1.0) * 100.0
    cpi_yoy = (cpi_pit / cpi_pit.shift(12) - 1.0) * 100.0

    indpro_yoy = indpro_yoy.dropna()
    cpi_yoy = cpi_yoy.dropna()

    if len(indpro_yoy) < window + 1 or len(cpi_yoy) < window + 1:
        assets = allocs.get("growth_up_infl_down", ("SPY", "EFA"))
        return {a: 1.0 / len(assets) for a in assets}

    growth_rising = float(indpro_yoy.iloc[-1] - indpro_yoy.iloc[-1 - window]) > 0.0
    infl_rising = float(cpi_yoy.iloc[-1] - cpi_yoy.iloc[-1 - window]) > 0.0

    if growth_rising and not infl_rising:
        quadrant = "growth_up_infl_down"
    elif growth_rising and infl_rising:
        quadrant = "growth_up_infl_up"
    elif not growth_rising and infl_rising:
        quadrant = "growth_down_infl_up"
    else:
        quadrant = "growth_down_infl_down"

    selected_assets = allocs.get(quadrant, ())
    if not selected_assets:
        return {}

    w_each = 1.0 / len(selected_assets)
    return {a: float(w_each) for a in selected_assets}


# -------------------------------------------------------------------------
# 19.3 Global Inflation (Rolling Inflation Beta Allocation)
# -------------------------------------------------------------------------


def global_inflation_weights(
    closes: pd.DataFrame,
    as_of: pd.Timestamp | Any,
    cpi_series: pd.Series | None,
    threshold: float = 3.0,
    window: int = 60,
    trend_window: int = 3,
    hedge_universe: tuple[str, ...] = ("TIP", "GLD", "DBC", "VNQ", "XLE"),
    benchmark_weights: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Inflation regime tactical allocation based on rolling inflation beta.

    §19.3:
    When CPI YoY > threshold or rising over `trend_window` months:
      weights proportional to each hedge asset's rolling inflation beta
      (regression of monthly asset returns on CPI change over `window` months, clipped >= 0).
    Otherwise:
      benchmark 60/40 allocation (SPY: 0.60, IEF: 0.40).
    """
    bm = benchmark_weights or {"SPY": 0.60, "IEF": 0.40}

    if cpi_series is None or cpi_series.empty:
        return dict(bm)

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    cpi_pit = cpi_series[cpi_series.index <= as_of_ts].dropna()

    if len(cpi_pit) < 12 + trend_window:
        return dict(bm)

    cpi_yoy = (cpi_pit / cpi_pit.shift(12) - 1.0) * 100.0
    cpi_yoy = cpi_yoy.dropna()

    if len(cpi_yoy) < trend_window + 1:
        return dict(bm)

    latest_yoy = float(cpi_yoy.iloc[-1])
    is_rising = float(cpi_yoy.iloc[-1] - cpi_yoy.iloc[-1 - trend_window]) > 0.0

    # High or rising inflation regime trigger
    if not (latest_yoy > threshold or is_rising):
        return dict(bm)

    # Calculate monthly asset returns and monthly CPI changes
    closes_pit = closes[closes.index <= as_of_ts]
    # Resample closes to monthly
    m_closes = closes_pit.resample("ME").last().dropna(how="all")
    m_rets = m_closes.pct_change().dropna(how="all")

    m_cpi = cpi_pit.resample("ME").last().dropna()
    m_cpi_diff = m_cpi.pct_change().dropna()

    # Align monthly returns with CPI changes
    aligned_idx = m_rets.index.intersection(m_cpi_diff.index)
    if len(aligned_idx) < min(12, window // 2):
        return dict(bm)

    sub_rets = m_rets.loc[aligned_idx].iloc[-window:]
    sub_cpi = m_cpi_diff.loc[aligned_idx].iloc[-window:]

    cpi_var = float(sub_cpi.var())
    if cpi_var <= 1e-8 or np.isnan(cpi_var):
        return dict(bm)

    betas: dict[str, float] = {}
    for col in hedge_universe:
        if col in sub_rets.columns and sub_rets[col].notna().sum() >= 6:
            cov = float(np.cov(sub_rets[col].fillna(0.0), sub_cpi)[0, 1])
            beta = cov / cpi_var
            betas[col] = max(0.0, beta)
        else:
            betas[col] = 0.0

    total_beta = sum(betas.values())
    if total_beta <= 1e-6:
        # Fallback to equal weight hedge universe if all betas are 0
        return {col: 1.0 / len(hedge_universe) for col in hedge_universe}

    return {k: float(v / total_beta) for k, v in betas.items() if v > 1e-6}


# -------------------------------------------------------------------------
# 19.5 Economic Announcement (FOMC Announcement Drift)
# -------------------------------------------------------------------------


def econ_announcement_weights(
    as_of: pd.Timestamp | Any,
    event_dates: Iterable[Any],
    days_before: int = 1,
    days_after: int = 0,
    asset: str = "SPY",
    gross: float = 1.0,
) -> dict[str, float]:
    """Hold asset during FOMC / economic announcement window.

    §19.5: Pre-announcement drift:
    Long asset on `days_before` business days prior to and through `days_after` of each announcement.
    Outside the window, portfolio is flat (cash).
    """
    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    if not event_dates:
        return {}

    # Convert all event dates to normalized timestamps
    evt_ts_set = {pd.to_datetime(d).tz_localize(None).normalize() for d in event_dates}

    is_active = False
    for evt_ts in evt_ts_set:
        # Business days between as_of and evt_ts
        # np.busday_count(d1, d2) > 0 if d1 < d2
        d_asof = as_of_ts.strftime("%Y-%m-%d")
        d_evt = evt_ts.strftime("%Y-%m-%d")

        try:
            if as_of_ts <= evt_ts:
                # Before or on event
                bdays_to_event = int(np.busday_count(d_asof, d_evt))
                if bdays_to_event <= days_before:
                    is_active = True
                    break
            else:
                # After event
                bdays_past_event = int(np.busday_count(d_evt, d_asof))
                if bdays_past_event <= days_after:
                    is_active = True
                    break
        except Exception:
            continue

    if is_active:
        return {asset: float(gross)}
    return {}
