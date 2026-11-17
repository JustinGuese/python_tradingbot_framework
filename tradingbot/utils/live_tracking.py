"""
Live-vs-paper tracking for live-trading execution monitoring.

PLAN.md Step S1c:
From `live_equity` per broker and the paper blend's `portfolio_worth`
(weekday + gap rules from AGENTS.md), compute:
  * daily tracking error: std(r_live - r_paper)
  * cumulative drift: prod(1 + r_live) - prod(1 + r_paper)
  * beta of live to paper: cov(r_live, r_paper) / var(r_paper)
  * share of days the broker was flat while the blend was invested.

Prints a formatted table. This is the live "does it match the backtest" check.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradingbot.livetrade.registry import REGISTRY
from tradingbot.utils.db import LiveEquity, PortfolioWorth

logger = logging.getLogger(__name__)

MAX_GAP_DAYS = 4
ANNUALIZATION_FACTOR = 252.0


@dataclass(frozen=True)
class TrackingMetrics:
    """Metrics comparing a broker's live execution curve against its paper blend."""

    broker: str
    account_id: str | None
    n_days: int
    start_date: date | None
    end_date: date | None
    live_cumulative_return: float
    paper_cumulative_return: float
    cumulative_drift: float
    daily_tracking_error: float
    annualized_tracking_error: float
    beta: float
    flat_days_while_invested: int
    invested_days: int
    flat_share: float
    total_flat_days: int
    verdict: str


# --------------------------------------------------------------------------- #
# Filtering and returns (AGENTS.md / alpha_report.py rules)
# --------------------------------------------------------------------------- #
def weekday_levels(series: pd.Series) -> pd.Series:
    """
    A daily level series with weekend rows dropped, one row per date, oldest first.
    Values <= 0 are treated as valuation or recording failures and dropped.
    """
    if series.empty:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(pd.to_datetime(series.index)).normalize()
    s = pd.Series(series.to_numpy(dtype=float), index=idx)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[(s.index.dayofweek < 5) & (s > 0)].dropna()


def joint_returns(
    live: pd.Series,
    paper: pd.Series,
    max_gap_days: int = MAX_GAP_DAYS,
) -> pd.DataFrame:
    """
    Daily returns of live ("live") and paper blend ("paper") over common weekday dates,
    excluding return pairs spanning more than `max_gap_days` (recorder outages).
    """
    clean_live = weekday_levels(live)
    clean_paper = weekday_levels(paper)
    levels = pd.concat({"live": clean_live, "paper": clean_paper}, axis=1, join="inner")
    if len(levels) < 2:
        return pd.DataFrame(columns=["live", "paper"], index=pd.DatetimeIndex([]))

    rets = levels.pct_change()
    gaps = levels.index.to_series().diff().dt.days
    valid = rets[gaps <= max_gap_days].replace([np.inf, -np.inf], np.nan).dropna()
    return valid


# --------------------------------------------------------------------------- #
# Flat and invested detection
# --------------------------------------------------------------------------- #
def is_broker_flat(
    positions: dict[str, Any] | None,
    live_return: float | None = None,
) -> bool:
    """
    Determine whether a broker held no active positions on a given day.

    A broker is flat if:
      - positions is None or empty dict, OR
      - every non-cash position has signed quantity near 0 (abs < 1e-6).
    If positions is None and live_return is provided, return near 0 indicates flat.
    """
    if positions is not None:
        if not positions:
            return True
        for sym, qty in positions.items():
            if sym.upper() in ("USD", "CASH", "USDT", "USDC"):
                continue
            try:
                if abs(float(qty or 0)) > 1e-6:
                    return False
            except (ValueError, TypeError):
                continue
        return True

    if live_return is not None:
        return abs(float(live_return)) < 1e-9

    return True


def is_paper_invested(
    holdings: dict[str, Any] | None,
    paper_return: float | None = None,
) -> bool:
    """
    Determine whether the paper bot/blend was invested in market assets on a given day.

    Paper bots hold 0%-yield USD when not invested.
    If holdings dict is provided:
      - Invested if any non-USD/CASH symbol has abs(qty) > 1e-6.
    If holdings is None and paper_return is provided:
      - Non-zero return indicates market exposure (since cash earns 0.0%).
    """
    if holdings is not None:
        if not holdings:
            return False
        for sym, qty in holdings.items():
            if sym.upper() in ("USD", "CASH", "USDT", "USDC"):
                continue
            try:
                if abs(float(qty or 0)) > 1e-6:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    if paper_return is not None:
        return abs(float(paper_return)) > 1e-7

    return False


# --------------------------------------------------------------------------- #
# Blend series builder
# --------------------------------------------------------------------------- #
def build_paper_blend_series(
    worth_by_bot: dict[str, pd.Series],
    weights: dict[str, float],
    holdings_by_bot: dict[str, pd.Series] | None = None,
    max_gap_days: int = MAX_GAP_DAYS,
) -> tuple[pd.Series, pd.Series]:
    """
    Build a blended paper portfolio worth series and an invested boolean series
    from component bots' individual portfolio_worth series.

    Weights are normalized to sum to 1.0 (if total weight > 0).
    Blended return on day t is sum(w_i * r_{i, t}).
    Blended invested flag is True if any bot with weight > 0 is invested.
    """
    if not weights or not worth_by_bot:
        return pd.Series(dtype=float), pd.Series(dtype=bool)

    active_bots = [b for b, w in weights.items() if w > 0 and b in worth_by_bot and not worth_by_bot[b].empty]
    if not active_bots:
        return pd.Series(dtype=float), pd.Series(dtype=bool)

    total_w = sum(weights[b] for b in active_bots)
    norm_w = {b: weights[b] / total_w for b in active_bots} if total_w > 0 else {b: 0.0 for b in active_bots}

    # Clean levels per bot
    levels = {b: weekday_levels(worth_by_bot[b]) for b in active_bots}
    df_levels = pd.DataFrame(levels).dropna(how="all")
    if len(df_levels) < 2:
        return pd.Series(dtype=float), pd.Series(dtype=bool)

    # Compute daily returns respecting max gap days
    df_rets = df_levels.pct_change()
    gaps = df_levels.index.to_series().diff().dt.days
    valid_mask = gaps <= max_gap_days
    valid_rets = df_rets[valid_mask].replace([np.inf, -np.inf], np.nan)

    # Weighted blend return
    weighted_rets = pd.Series(0.0, index=df_levels.index)
    for b in active_bots:
        if b in valid_rets.columns:
            r = valid_rets[b].fillna(0.0)
            weighted_rets += norm_w[b] * r

    # Cumulative worth indexed starting at 10,000.0 from the first date (D0)
    blend_worth = pd.Series(index=df_levels.index, dtype=float)
    blend_worth.iloc[0] = 10_000.0
    for i in range(1, len(df_levels)):
        dt = df_levels.index[i]
        prev_dt = df_levels.index[i - 1]
        gap = (dt - prev_dt).days
        if gap <= max_gap_days:
            blend_worth.loc[dt] = blend_worth.loc[prev_dt] * (1.0 + weighted_rets.loc[dt])
        else:
            blend_worth.loc[dt] = blend_worth.loc[prev_dt]

    # Blend invested flag
    invested_series = pd.Series(False, index=df_levels.index)
    if holdings_by_bot:
        for b in active_bots:
            if b in holdings_by_bot:
                h_series = holdings_by_bot[b].reindex(df_levels.index)
                bot_invested = h_series.apply(lambda h: is_paper_invested(h if isinstance(h, dict) else None))
                invested_series = invested_series | bot_invested
    else:
        # Fallback to non-zero returns
        for b in active_bots:
            if b in df_rets.columns:
                invested_series = invested_series | (df_rets[b].abs() > 1e-7)

    return blend_worth, invested_series


# --------------------------------------------------------------------------- #
# Tracking Metrics Computation
# --------------------------------------------------------------------------- #
def tracking_verdict(
    n_days: int,
    flat_share: float,
    invested_days: int,
    daily_tracking_error: float,
    cumulative_drift: float,
) -> str:
    """Classify the tracking quality into a human-readable verdict."""
    if n_days < 5:
        return "TOO SHORT"
    if invested_days >= 3 and flat_share >= 0.8:
        return "FLAT (NO TRADES)"
    if abs(cumulative_drift) > 0.05 or daily_tracking_error > 0.02:
        return "DRIFTING"
    if daily_tracking_error > 0.01:
        return "LOOSE TRACKING"
    return "TRACKING"


def compute_tracking_metrics(
    live_series: pd.Series,
    paper_series: pd.Series,
    live_positions: pd.Series | None = None,
    paper_holdings: pd.Series | None = None,
    paper_invested: pd.Series | None = None,
    broker_name: str = "broker",
    account_id: str | None = None,
    max_gap_days: int = MAX_GAP_DAYS,
) -> TrackingMetrics:
    """
    Compute daily tracking error, cumulative drift, beta of live to paper,
    and the share of days the broker was flat while the paper blend was invested.
    """
    rets = joint_returns(live_series, paper_series, max_gap_days=max_gap_days)
    n = len(rets)

    if n == 0:
        return TrackingMetrics(
            broker=broker_name,
            account_id=account_id,
            n_days=0,
            start_date=None,
            end_date=None,
            live_cumulative_return=0.0,
            paper_cumulative_return=0.0,
            cumulative_drift=0.0,
            daily_tracking_error=0.0,
            annualized_tracking_error=0.0,
            beta=0.0,
            flat_days_while_invested=0,
            invested_days=0,
            flat_share=0.0,
            total_flat_days=0,
            verdict="NO COMMON DATA",
        )

    start_date = rets.index[0].date()
    end_date = rets.index[-1].date()

    r_live = rets["live"]
    r_paper = rets["paper"]

    live_cum = float((1.0 + r_live).prod() - 1.0)
    paper_cum = float((1.0 + r_paper).prod() - 1.0)
    cum_drift = float(live_cum - paper_cum)

    diff_rets = r_live - r_paper
    daily_te = float(diff_rets.std(ddof=1)) if n >= 2 else 0.0
    if not np.isfinite(daily_te):
        daily_te = 0.0
    ann_te = float(daily_te * np.sqrt(ANNUALIZATION_FACTOR))

    var_paper = float(r_paper.var())
    if np.isfinite(var_paper) and var_paper > 0:
        cov_lp = float(r_live.cov(r_paper))
        beta = float(cov_lp / var_paper)
    else:
        beta = 0.0
    if not np.isfinite(beta):
        beta = 0.0

    # Flat and invested day accounting across common dates
    flat_days_while_invested = 0
    invested_days = 0
    total_flat_days = 0

    norm_live_pos = None
    if live_positions is not None and not live_positions.empty:
        pos_idx = pd.DatetimeIndex(pd.to_datetime(live_positions.index)).normalize()
        norm_live_pos = pd.Series(live_positions.values, index=pos_idx)

    norm_paper_hold = None
    if paper_holdings is not None and not paper_holdings.empty:
        hold_idx = pd.DatetimeIndex(pd.to_datetime(paper_holdings.index)).normalize()
        norm_paper_hold = pd.Series(paper_holdings.values, index=hold_idx)

    norm_paper_inv = None
    if paper_invested is not None and not paper_invested.empty:
        inv_idx = pd.DatetimeIndex(pd.to_datetime(paper_invested.index)).normalize()
        norm_paper_inv = pd.Series(paper_invested.values, index=inv_idx)

    for dt in rets.index:
        ret_l = float(r_live.loc[dt])
        ret_p = float(r_paper.loc[dt])

        # Broker flat check
        pos_entry = norm_live_pos.get(dt) if norm_live_pos is not None else None
        broker_flat = is_broker_flat(
            pos_entry if isinstance(pos_entry, dict) else None,
            live_return=ret_l,
        )
        if broker_flat:
            total_flat_days += 1

        # Blend invested check
        if norm_paper_inv is not None and dt in norm_paper_inv:
            blend_inv = bool(norm_paper_inv.loc[dt])
        elif norm_paper_hold is not None:
            hold_entry = norm_paper_hold.get(dt)
            blend_inv = is_paper_invested(
                hold_entry if isinstance(hold_entry, dict) else None,
                paper_return=ret_p,
            )
        else:
            blend_inv = is_paper_invested(None, paper_return=ret_p)

        if blend_inv:
            invested_days += 1
            if broker_flat:
                flat_days_while_invested += 1

    flat_share = float(flat_days_while_invested / invested_days) if invested_days > 0 else 0.0

    verdict = tracking_verdict(
        n_days=n,
        flat_share=flat_share,
        invested_days=invested_days,
        daily_tracking_error=daily_te,
        cumulative_drift=cum_drift,
    )

    return TrackingMetrics(
        broker=broker_name,
        account_id=account_id,
        n_days=n,
        start_date=start_date,
        end_date=end_date,
        live_cumulative_return=live_cum,
        paper_cumulative_return=paper_cum,
        cumulative_drift=cum_drift,
        daily_tracking_error=daily_te,
        annualized_tracking_error=ann_te,
        beta=beta,
        flat_days_while_invested=flat_days_while_invested,
        invested_days=invested_days,
        flat_share=flat_share,
        total_flat_days=total_flat_days,
        verdict=verdict,
    )


# --------------------------------------------------------------------------- #
# Database Loaders & Tracking Execution
# --------------------------------------------------------------------------- #
def load_live_equity_df(
    session: Session,
    broker: str,
    account_id: str | None = None,
    include_testnet: bool = False,
) -> pd.DataFrame:
    """Load LiveEquity records for a broker from database."""
    stmt = select(
        LiveEquity.date,
        LiveEquity.account_id,
        LiveEquity.equity,
        LiveEquity.cash,
        LiveEquity.positions,
        LiveEquity.bot_weights,
        LiveEquity.is_testnet,
    ).where(LiveEquity.broker == broker)

    if account_id:
        stmt = stmt.where(LiveEquity.account_id == account_id)
    if not include_testnet:
        stmt = stmt.where(LiveEquity.is_testnet == False)  # noqa: E712

    stmt = stmt.order_by(LiveEquity.date.asc())
    rows = session.execute(stmt).fetchall()
    if not rows:
        return pd.DataFrame(columns=["date", "account_id", "equity", "cash", "positions", "bot_weights", "is_testnet"])

    data = [
        {
            "date": r.date,
            "account_id": r.account_id,
            "equity": float(r.equity),
            "cash": float(r.cash) if r.cash is not None else None,
            "positions": r.positions or {},
            "bot_weights": r.bot_weights,
            "is_testnet": r.is_testnet,
        }
        for r in rows
    ]
    return pd.DataFrame(data)


def load_portfolio_worth_df(
    session: Session,
    bot_names: list[str],
) -> pd.DataFrame:
    """Load PortfolioWorth history for a list of bot names from database."""
    if not bot_names:
        return pd.DataFrame(columns=["bot_name", "date", "portfolio_worth", "holdings"])

    stmt = (
        select(
            PortfolioWorth.bot_name,
            PortfolioWorth.date,
            PortfolioWorth.portfolio_worth,
            PortfolioWorth.holdings,
        )
        .where(PortfolioWorth.bot_name.in_(bot_names))
        .order_by(PortfolioWorth.date.asc())
    )
    rows = session.execute(stmt).fetchall()
    if not rows:
        return pd.DataFrame(columns=["bot_name", "date", "portfolio_worth", "holdings"])

    data = [
        {
            "bot_name": r.bot_name,
            "date": r.date,
            "portfolio_worth": float(r.portfolio_worth),
            "holdings": r.holdings or {},
        }
        for r in rows
    ]
    return pd.DataFrame(data)


def parse_weights(raw: str | dict | None) -> dict[str, float]:
    """Parse weights JSON string or dict into dict[str, float]."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {k: float(v) for k, v in raw.items()}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {k: float(v) for k, v in parsed.items()}
        except Exception:
            return {}
    return {}


def track_broker(
    session: Session,
    broker: str,
    account_id: str | None = None,
    bot_weights: dict[str, float] | None = None,
    include_testnet: bool = False,
) -> TrackingMetrics | None:
    """
    Run live-vs-paper tracking for a broker against PostgreSQL.
    """
    df_live = load_live_equity_df(session, broker=broker, account_id=account_id, include_testnet=include_testnet)
    if df_live.empty:
        logger.warning(f"No live_equity rows found for broker '{broker}'")
        return None

    # Resolve bot weights
    weights = bot_weights
    if not weights:
        # Check latest row with bot_weights populated
        rows_with_weights = df_live[df_live["bot_weights"].notna()]
        if not rows_with_weights.empty:
            weights = parse_weights(rows_with_weights.iloc[-1]["bot_weights"])
    if not weights and broker in REGISTRY:
        weights = parse_weights(REGISTRY[broker].default_bot_weights)
    if not weights:
        logger.warning(f"Could not determine bot weights for broker '{broker}'")
        return None

    # Load paper bot history
    bot_names = list(weights.keys())
    df_pw = load_portfolio_worth_df(session, bot_names)
    if df_pw.empty:
        logger.warning(f"No portfolio_worth records for bots: {bot_names}")
        return None

    worth_by_bot: dict[str, pd.Series] = {}
    holdings_by_bot: dict[str, pd.Series] = {}
    for bot in bot_names:
        bot_df = df_pw[df_pw["bot_name"] == bot].set_index("date")
        if not bot_df.empty:
            worth_by_bot[bot] = bot_df["portfolio_worth"]
            holdings_by_bot[bot] = bot_df["holdings"]

    # Build paper blend series
    paper_worth, paper_invested = build_paper_blend_series(
        worth_by_bot=worth_by_bot,
        weights=weights,
        holdings_by_bot=holdings_by_bot,
    )

    # Prepare live series
    live_series = df_live.set_index("date")["equity"]
    live_positions = df_live.set_index("date")["positions"]
    acc_id = df_live.iloc[-1]["account_id"] if account_id is None else account_id

    return compute_tracking_metrics(
        live_series=live_series,
        paper_series=paper_worth,
        live_positions=live_positions,
        paper_invested=paper_invested,
        broker_name=broker,
        account_id=acc_id,
    )


def track_all_brokers(
    session: Session,
    brokers: list[str] | None = None,
    include_testnet: bool = False,
) -> list[TrackingMetrics]:
    """
    Run tracking for all brokers present in live_equity or REGISTRY.
    """
    if brokers is None:
        stmt = select(LiveEquity.broker).distinct()
        if not include_testnet:
            stmt = stmt.where(LiveEquity.is_testnet == False)  # noqa: E712
        brokers_in_db = [r[0] for r in session.execute(stmt).fetchall()]
        brokers = brokers_in_db if brokers_in_db else list(REGISTRY.keys())

    results: list[TrackingMetrics] = []
    for b in brokers:
        metrics = track_broker(session, broker=b, include_testnet=include_testnet)
        if metrics is not None:
            results.append(metrics)
    return results


# --------------------------------------------------------------------------- #
# Table Formatting
# --------------------------------------------------------------------------- #
def format_tracking_table(metrics: list[TrackingMetrics]) -> str:
    """Format a list of TrackingMetrics into a markdown table."""
    if not metrics:
        return "No tracking metrics available."

    headers = [
        "Broker",
        "Days",
        "Window",
        "Live Ret",
        "Paper Ret",
        "Cum Drift",
        "Daily TE",
        "Ann TE",
        "Beta",
        "Flat/Inv (Share)",
        "Verdict",
    ]
    sep = ["---"] * len(headers)

    rows = []
    for m in metrics:
        window_str = f"{m.start_date} .. {m.end_date}" if m.start_date and m.end_date else "n/a"
        flat_str = (
            f"{m.flat_days_while_invested}/{m.invested_days} ({m.flat_share * 100:.1f}%)"
            if m.invested_days > 0
            else f"0/0 (0.0%)"
        )
        row = [
            m.broker,
            str(m.n_days),
            window_str,
            f"{m.live_cumulative_return * 100:+.2f}%",
            f"{m.paper_cumulative_return * 100:+.2f}%",
            f"{m.cumulative_drift * 100:+.2f}%",
            f"{m.daily_tracking_error * 100:.2f}%",
            f"{m.annualized_tracking_error * 100:.2f}%",
            f"{m.beta:.2f}",
            flat_str,
            m.verdict,
        ]
        rows.append(row)

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")

    return "\n".join(lines)
