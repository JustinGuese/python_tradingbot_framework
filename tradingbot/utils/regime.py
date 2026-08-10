"""
Regime classification and regime-specific weight tilting.

Inputs: VIX series or value; index close series (e.g. QQQ) for trend; Fear & Greed value.
No Bot or db dependency; pure functions.
"""

import pandas as pd


def vix_series_from_long_df(data_long: pd.DataFrame) -> pd.Series | None:
    """
    Extract a VIX close-price series from a long-format DataFrame.

    Expects columns: symbol, timestamp, close.
    Returns a Series indexed by timestamp, sorted ascending, or None if no VIX data.
    """
    if data_long is None or data_long.empty:
        return None
    required = {"symbol", "timestamp", "close"}
    if not required.issubset(set(data_long.columns)):
        return None
    vix_df = data_long[data_long["symbol"] == "^VIX"]
    if vix_df.empty:
        return None
    return vix_df.set_index("timestamp")["close"].sort_index()


def index_close_series_from_wide(wide_close_df: pd.DataFrame, index_symbol: str = "QQQ") -> pd.Series | None:
    """
    Extract an index close-price series (e.g. QQQ) from a wide-format DataFrame.

    Expects timestamp index and symbols as columns. Returns the column as a Series
    or None if the symbol is not present.
    """
    if wide_close_df is None or wide_close_df.empty:
        return None
    if index_symbol not in wide_close_df.columns:
        return None
    return wide_close_df[index_symbol]


def classify_regime(
    vix_series_or_value: pd.Series | float | None,
    index_close_series: pd.Series | None,
    fear_greed_value: int | None,
    *,
    vix_high: float = 25,
    fg_fear: int = 25,
    fg_greed: int = 75,
) -> str:
    """
    Classify regime from vol, trend, and sentiment.

    Returns one of "defensive", "momentum", "mean_reversion".
    Rules:
    - High vol (VIX > vix_high) OR downtrend (index close < 50d MA) OR extreme fear (fg < fg_fear) → "defensive".
    - Uptrend + greed (fg >= fg_greed) → "momentum".
    - Sideways + low vol → "mean_reversion".
    Default to "defensive" if data missing.

    Args:
        vix_series_or_value: VIX level(s); float or Series (last value used if Series).
        index_close_series: Index close prices (e.g. QQQ), timestamp index.
        fear_greed_value: Fear & Greed index 0–100.
        vix_high: VIX threshold for high vol (default 25).
        fg_fear: Fear threshold (default 25).
        fg_greed: Greed threshold (default 75).

    Returns:
        "defensive" | "momentum" | "mean_reversion"
    """
    # Resolve VIX to single value
    vix_val: float | None = None
    if vix_series_or_value is not None:
        if isinstance(vix_series_or_value, pd.Series) and not vix_series_or_value.empty:
            vix_val = float(vix_series_or_value.iloc[-1])
        elif isinstance(vix_series_or_value, (int, float)):
            vix_val = float(vix_series_or_value)

    # High vol
    if vix_val is not None and vix_val > vix_high:
        return "defensive"

    # Extreme fear
    if fear_greed_value is not None and fear_greed_value < fg_fear:
        return "defensive"

    # Trend from index: 50d MA
    uptrend = None
    if index_close_series is not None and isinstance(index_close_series, pd.Series) and len(index_close_series) >= 50:
        ma50 = index_close_series.rolling(50, min_periods=50).mean()
        last_close = index_close_series.iloc[-1]
        last_ma = ma50.iloc[-1]
        uptrend = None if pd.isna(last_ma) else last_close > last_ma
        # Downtrend → defensive
        if uptrend is False:
            return "defensive"

    # Uptrend + greed → momentum
    if uptrend is True and fear_greed_value is not None and fear_greed_value >= fg_greed:
        return "momentum"

    # Sideways + low vol → mean_reversion (or default when unclear)
    if uptrend is None and (vix_val is None or vix_val <= vix_high):
        return "mean_reversion"
    if uptrend is True and (fear_greed_value is None or fear_greed_value < fg_greed):
        return "mean_reversion"

    # Default
    return "defensive"


def apply_regime_tilt(
    regime: str,
    base_weights: dict[str, float],
    wide_close_df: pd.DataFrame,
    *,
    defensive_cash_weight: float = 0.5,
    defensive_cash_symbol: str = "SHV",
) -> dict[str, float]:
    """
    Apply regime-specific tilt to base weights.

    - defensive: set defensive_cash_symbol to defensive_cash_weight, scale rest so total = 1 - defensive_cash_weight, merge.
    - momentum: last close > 50d MA (or 20d) → 1.2x base, else 0.8x; normalize.
    - mean_reversion: oversold (last close vs 20d low) → 1.2x, overbought vs 20d high → 0.8x; normalize.

    wide_close_df: timestamp index, symbols as columns (close per symbol), same as convertToWideFormat.

    Returns dict symbol → weight, sum 1.0. Empty wide_close_df for tilt → return base_weights normalized.
    """
    if not base_weights:
        return {}

    total_base = sum(base_weights.values())
    if total_base <= 0:
        return {s: 1.0 / len(base_weights) for s in base_weights}

    if regime == "defensive":
        cash_w = defensive_cash_weight
        rest_w = 1.0 - cash_w
        # Scale non-cash base weights to sum to rest_w
        non_cash = {s: w for s, w in base_weights.items() if s != defensive_cash_symbol}
        if not non_cash:
            return {defensive_cash_symbol: 1.0}
        sum_non = sum(non_cash.values())
        if sum_non <= 0:
            out = {defensive_cash_symbol: cash_w}
            n = len(non_cash)
            for s in non_cash:
                out[s] = rest_w / n
            return out
        out = {s: (w / sum_non) * rest_w for s, w in non_cash.items()}
        out[defensive_cash_symbol] = cash_w
        return out

    if wide_close_df.empty or len(wide_close_df) < 20:
        return {s: w / total_base for s, w in base_weights.items()}

    # Use 20d for momentum (plan said 50d or 20d) to work with shorter history
    ma_period = min(20, len(wide_close_df))
    last_row = wide_close_df.iloc[-1]
    rolling = wide_close_df.rolling(ma_period, min_periods=ma_period)

    if regime == "momentum":
        ma_last = rolling.mean().iloc[-1]
        mult = {}
        for s in base_weights:
            if s not in wide_close_df.columns:
                mult[s] = 1.0
                continue
            close = last_row.get(s)
            ma = ma_last.get(s)
            if pd.isna(close) or pd.isna(ma) or ma == 0:
                mult[s] = 1.0
            else:
                mult[s] = 1.2 if close > ma else 0.8
        tilted = {s: base_weights[s] * mult.get(s, 1.0) for s in base_weights}
    elif regime == "mean_reversion":
        low_20 = rolling.min().iloc[-1]
        high_20 = rolling.max().iloc[-1]
        mult = {}
        for s in base_weights:
            if s not in wide_close_df.columns:
                mult[s] = 1.0
                continue
            close = last_row.get(s)
            lo = low_20.get(s)
            hi = high_20.get(s)
            if pd.isna(close) or pd.isna(lo) or pd.isna(hi) or hi == lo:
                mult[s] = 1.0
            else:
                # Oversold: close near 20d low → 1.2; overbought: close near 20d high → 0.8
                pct = (close - lo) / (hi - lo)
                mult[s] = 1.2 if pct <= 0.33 else (0.8 if pct >= 0.67 else 1.0)
        tilted = {s: base_weights[s] * mult.get(s, 1.0) for s in base_weights}
    else:
        return {s: w / total_base for s, w in base_weights.items()}

    total_tilted = sum(tilted.values())
    if total_tilted <= 0:
        return {sym: 1.0 / len(base_weights) for sym in base_weights}
    return {sym: v / total_tilted for sym, v in tilted.items()}


def regime_compute_weights(
    symbols: list[str],
    vix_series_or_value: pd.Series | float | None,
    index_close_series: pd.Series | None,
    wide_close_df: pd.DataFrame,
    fear_greed_value: int | None,
) -> dict[str, float]:
    """
    Compute regime-tilted weights for a symbol universe.

    This mirrors the default behaviour of RegimeAdaptiveBot as a pure function:
    equal-weight base → classify_regime → apply_regime_tilt.
    """
    if not symbols:
        return {}
    n = len(symbols)
    base_weights = dict.fromkeys(symbols, 1.0 / n)
    reg = classify_regime(vix_series_or_value, index_close_series, fear_greed_value)
    return apply_regime_tilt(reg, base_weights, wide_close_df)
