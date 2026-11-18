"""Idle cash parking utility for target portfolio weights."""

from __future__ import annotations


def park_idle_cash(
    weights: dict[str, float],
    proxy: str = "SHV",
    max_gross: float = 1.0,
    min_weight: float = 0.01,
) -> dict[str, float]:
    """Park uninvested cash into a cash proxy ETF (e.g. SHV).

    Adds proxy weight = min(1.0 - signed net weight, max_gross - gross)
    when that is >= min_weight.
    Never shorts the proxy.
    Leaves weights unchanged otherwise (preserving a dictionary copy).
    """
    out = dict(weights)
    if not weights:
        net = 0.0
        gross = 0.0
    else:
        net = sum(weights.values())
        gross = sum(abs(w) for w in weights.values())

    idle = min(1.0 - net, max_gross - gross)
    if idle >= min_weight and idle > 0:
        target = out.get(proxy, 0.0) + idle
        if target >= 0:
            out[proxy] = target
    return out
