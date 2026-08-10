"""
Earnings and insider-trade scoring and weight tilting.

Uses DB: StockEarnings (symbol, report_date, eps_estimate, reported_eps, surprise_pct),
StockInsiderTrade (symbol, transaction_date, transaction_type, shares, value).
No Bot dependency.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc

from .db import StockEarnings, StockInsiderTrade, get_db_session


def score_symbols_earnings_insider(
    symbols: list[str],
    earnings_limit_per_symbol: int = 2,
    insider_days: int = 90,
) -> dict[str, float]:
    """
    Score symbols from DB using earnings surprise and insider net activity.

    Per symbol:
    - Earnings score: from latest earnings (surprise_pct > 0 → +1, < 0 → -1, else 0).
    - Insider score: net Purchase vs Sale over last `insider_days` (by value) → +1 / -1 / 0.
    - Composite = earnings + insider. Symbols with no data get 0.0.

    Args:
        symbols: List of symbols to score.
        earnings_limit_per_symbol: Max number of earnings reports to consider per symbol (default 2).
        insider_days: Lookback days for insider trades (default 90).

    Returns:
        Dict mapping symbol to composite score (float).
    """
    if not symbols:
        return {}

    result: dict[str, float] = dict.fromkeys(symbols, 0.0)
    cutoff = datetime.now(UTC) - timedelta(days=insider_days)

    with get_db_session() as session:
        # Earnings: latest per symbol, surprise_pct > 0 → +1, < 0 → -1, else 0
        for sym in symbols:
            rows = (
                session.query(StockEarnings)
                .filter(StockEarnings.symbol == sym)
                .order_by(desc(StockEarnings.report_date))
                .limit(earnings_limit_per_symbol)
                .all()
            )
            for row in rows:
                if row.surprise_pct is None:
                    continue
                if row.surprise_pct > 0:
                    result[sym] += 1.0
                elif row.surprise_pct < 0:
                    result[sym] -= 1.0
                break  # use only latest

        # Insider: net value (Purchase - Sale) over last insider_days → +1 / -1 / 0
        for sym in symbols:
            insider_rows = (
                session.query(StockInsiderTrade)
                .filter(
                    StockInsiderTrade.symbol == sym,
                    StockInsiderTrade.transaction_date >= cutoff,
                )
                .all()
            )
            net = 0.0
            for insider_row in insider_rows:
                val = float(insider_row.value if insider_row.value is not None else (insider_row.shares or 0.0))
                if insider_row.transaction_type and "urchase" in insider_row.transaction_type.casefold():
                    net += val
                elif insider_row.transaction_type and "ale" in insider_row.transaction_type.casefold():
                    net -= val
            if net > 0:
                result[sym] += 1.0
            elif net < 0:
                result[sym] -= 1.0

    return result


def tilt_weights_by_scores(
    base_weights: dict[str, float],
    scores: dict[str, float],
    top_mult: float = 1.3,
    bottom_mult: float = 0.7,
) -> dict[str, float]:
    """
    Tilt base weights by score: top quintile gets top_mult, bottom quintile bottom_mult, then normalize.

    Only symbols in base_weights appear in output. Sum of output weights = 1.0.
    Missing symbol in scores is treated as 0. Empty scores → return base_weights normalized.

    Args:
        base_weights: Symbol -> weight (need not sum to 1).
        scores: Symbol -> composite score (missing → 0).
        top_mult: Multiplier for top quintile (default 1.3).
        bottom_mult: Multiplier for bottom quintile (default 0.7).

    Returns:
        Dict symbol -> weight, sum 1.0.
    """
    if not base_weights:
        return {}

    # Empty scores → return base_weights normalized
    if not scores:
        total = sum(base_weights.values())
        if total <= 0:
            return {s: 1.0 / len(base_weights) for s in base_weights}
        return {s: w / total for s, w in base_weights.items()}

    # Missing score → 0
    score_vals = [(s, scores.get(s, 0.0)) for s in base_weights]

    # Rank by score descending; quintiles
    score_vals.sort(key=lambda x: x[1], reverse=True)
    n = len(score_vals)
    top_n = max(1, n // 5)
    bottom_n = max(1, n // 5)
    top_set = {score_vals[i][0] for i in range(top_n)}
    bottom_set = {score_vals[n - 1 - i][0] for i in range(bottom_n)}

    tilted = {}
    for s, w in base_weights.items():
        if s in top_set:
            tilted[s] = w * top_mult
        elif s in bottom_set:
            tilted[s] = w * bottom_mult
        else:
            tilted[s] = w

    total = sum(tilted.values())
    if total <= 0:
        return {s: 1.0 / len(base_weights) for s in base_weights}
    return {s: v / total for s, v in tilted.items()}


def earnings_insider_compute_weights(
    symbols: list[str],
    *,
    earnings_limit_per_symbol: int = 2,
    insider_days: int = 90,
    top_mult: float = 1.3,
    bottom_mult: float = 0.7,
) -> dict[str, float]:
    """
    Convenience helper: equal-weight base tilted by earnings/insider scores.

    This encapsulates the default behaviour of EarningsInsiderTiltBot as a pure
    function, so it can be reused from bots, backtests, or notebooks.
    """
    if not symbols:
        return {}
    base_weights = {s: 1.0 / len(symbols) for s in symbols}
    scores = score_symbols_earnings_insider(
        symbols,
        earnings_limit_per_symbol=earnings_limit_per_symbol,
        insider_days=insider_days,
    )
    return tilt_weights_by_scores(
        base_weights,
        scores,
        top_mult=top_mult,
        bottom_mult=bottom_mult,
    )
