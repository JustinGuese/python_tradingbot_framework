"""
Shared indicator-row helpers used by decision functions across bots.

No Bot or db dependency — pure functions operating on a pandas row/Series.
"""

import math

import pandas as pd


def safe_get(row: pd.Series, key: str, default: float = 0.0, *, check_finite: bool = False) -> float:
    """Get a float value from a pandas row, tolerating NaN/missing/non-numeric values.

    Returns `default` when the key is missing, the value is NaN, or the value
    cannot be cast to float.

    When `check_finite=True`, +/-inf values are also treated as invalid and
    replaced with `default` — some indicator columns can produce inf from a
    division by zero upstream, and this feeds live trading decisions.
    `check_finite=False` (the default) matches the historical behaviour of
    the `safe_get` helpers this function consolidates, which let inf values
    pass through unchanged; keep it False when preserving that behaviour
    matters, and pass True for the stricter variant used by SqueezeMomentumBot.
    """
    value = row.get(key, default)
    if pd.isna(value):
        return default
    try:
        f = float(value)
    except (ValueError, TypeError):
        return default
    if check_finite and not math.isfinite(f):
        return default
    return f
