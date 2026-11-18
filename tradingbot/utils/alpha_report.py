"""
Alpha vs QQQ: one formula for backtests and the weekly live report.

CLAUDE.md judges every bot by annualised alpha against QQQ and its t-stat,
never by raw return:

    beta = cov(r_bot, r_qqq) / var(r_qqq)
    resid = r_bot - beta * r_qqq
    alpha = resid.mean() * 252
    t = resid.mean() / resid.std() * sqrt(n)

alpha_stats() is that formula. backtest._compute_alpha_metrics calls it, and so
does the weekly `alphareport` job, which applies it to each bot's live
portfolio_worth series.

The live series needs two cleanups first (AGENTS.md "PortfolioWorth Model"):
  * The recorder writes every calendar day, so a weekend row repeats Friday.
    Weekend rows are dropped.
  * A gap of more than 4 days is a recorder outage, not a return. Those day
    pairs are dropped.
QQQ comes from the Benchmark_QQQ rows in the same table, so both series share
the recorder's gaps.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from .db import BotAlphaReport, PortfolioWorth, get_db_session

logger = logging.getLogger(__name__)

BENCHMARK = "Benchmark_QQQ"
BENCHMARK_PREFIX = "Benchmark_"
MAX_GAP_DAYS = 4
MIN_OBS = 60  # below this a t-stat is noise whatever it says
ACTIVE_DAYS = 7  # bots with no row this recent are gone, not reported
MAX_BENCHMARK_AGE_DAYS = 4
DEFAULT_RISK_FREE_RATE = 0.04  # 4% fallback


@dataclass(frozen=True)
class AlphaStats:
    alpha: float  # annualised
    t: float
    beta: float
    corr: float
    n: int


def load_irx(start: date | str | None = None) -> pd.Series:
    """Download daily ^IRX closes from yfinance, or empty Series on failure."""
    try:
        import yfinance as yf

        ticker = yf.Ticker("^IRX")
        hist = ticker.history(period="5y" if not start else None, start=start)
        if not hist.empty and "Close" in hist:
            s = hist["Close"].dropna()
            s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
            return s
    except Exception as e:
        logger.warning("Could not download ^IRX (%s); using default rate", e)
    return pd.Series(dtype=float)


def cash_adjusted_stats(
    r_bot: pd.Series,
    r_bench: pd.Series,
    usd_share: pd.Series | float | None = None,
    irx: pd.Series | float | None = None,
    *,
    holdings: pd.Series | None = None,
    portfolio_worth: pd.Series | None = None,
    periods_per_year: float = 252,
) -> AlphaStats | None:
    """Alpha of `r_bot` over `r_bench` after crediting ^IRX/252 on daily USD share.

    USD share is `holdings["USD"] / portfolio_worth` on each date.
    When USD share is 0%, identical to plain alpha_stats.
    """
    if usd_share is None:
        if holdings is not None and portfolio_worth is not None:
            shares = {}
            for dt in r_bot.index:
                norm_dt = pd.to_datetime(dt).normalize() if isinstance(dt, (date, datetime, pd.Timestamp)) else dt
                h = holdings.get(dt) if hasattr(holdings, "get") else None
                if h is None and hasattr(holdings, "get"):
                    h = holdings.get(norm_dt)
                w = portfolio_worth.get(dt) if hasattr(portfolio_worth, "get") else None
                if w is None and hasattr(portfolio_worth, "get"):
                    w = portfolio_worth.get(norm_dt)
                usd = h.get("USD", 0.0) if isinstance(h, dict) else 0.0
                shares[dt] = float(usd) / float(w) if (w is not None and float(w) > 0) else 0.0
            usd_share_series = pd.Series(shares, index=r_bot.index)
        else:
            usd_share_series = pd.Series(0.0, index=r_bot.index)
    elif isinstance(usd_share, (int, float, np.number)):
        usd_share_series = pd.Series(float(usd_share), index=r_bot.index)
    elif isinstance(usd_share, pd.Series):
        if len(usd_share) > 0 and isinstance(usd_share.iloc[0], dict):
            # usd_share passed as holdings series, irx passed as portfolio_worth
            h_series = usd_share
            w_series = irx if isinstance(irx, pd.Series) else None
            shares = {}
            for dt in r_bot.index:
                norm_dt = pd.to_datetime(dt).normalize() if isinstance(dt, (date, datetime, pd.Timestamp)) else dt
                h = h_series.get(dt)
                if h is None:
                    h = h_series.get(norm_dt)
                w = w_series.get(dt) if w_series is not None else None
                if w is None and w_series is not None:
                    w = w_series.get(norm_dt)
                usd = h.get("USD", 0.0) if isinstance(h, dict) else 0.0
                shares[dt] = float(usd) / float(w) if (w is not None and float(w) > 0) else 0.0
            usd_share_series = pd.Series(shares, index=r_bot.index)
            irx = None
        else:
            s = usd_share.copy()
            if isinstance(s.index, pd.DatetimeIndex):
                s.index = s.index.normalize()
            bot_idx = r_bot.index
            if isinstance(bot_idx, pd.DatetimeIndex):
                bot_idx = bot_idx.normalize()
            s = s[~s.index.duplicated(keep="last")]
            usd_share_series = s.reindex(bot_idx).ffill().bfill().fillna(0.0)
            usd_share_series.index = r_bot.index
    else:
        usd_share_series = pd.Series(0.0, index=r_bot.index)

    if irx is None:
        irx_download = load_irx()
        irx = irx_download if not irx_download.empty else DEFAULT_RISK_FREE_RATE

    if isinstance(irx, (int, float, np.number)):
        rate = float(irx)
        if rate > 0.5:
            rate = rate / 100.0
        daily_yield = pd.Series(rate / periods_per_year, index=r_bot.index)
    elif isinstance(irx, pd.Series):
        s = irx.copy()
        if isinstance(s.index, pd.DatetimeIndex):
            s.index = s.index.normalize()
        s = s[~s.index.duplicated(keep="last")]
        if len(s.dropna()) and float(s.dropna().mean()) > 0.5:
            s = s / 100.0
        daily_rate = s / periods_per_year
        bot_idx = r_bot.index
        if isinstance(bot_idx, pd.DatetimeIndex):
            bot_idx = bot_idx.normalize()
        daily_yield = daily_rate.reindex(bot_idx).ffill().bfill().fillna(0.0)
        daily_yield.index = r_bot.index
    else:
        daily_yield = pd.Series(0.0, index=r_bot.index)

    credit = usd_share_series.clip(lower=0.0) * daily_yield
    r_adj = r_bot + credit
    return alpha_stats(r_adj, r_bench, periods_per_year=periods_per_year)


def alpha_stats(r_bot: pd.Series, r_bench: pd.Series, periods_per_year: float = 252) -> AlphaStats | None:
    """
    Alpha of `r_bot` over `r_bench`, per-period returns joined on their index.
    None when fewer than 3 shared returns or a flat benchmark leave nothing to
    measure. That is deliberately not zero, which would read as "no edge".
    """
    rets = pd.concat({"p": r_bot, "b": r_bench}, axis=1, join="inner")
    rets = rets.replace([np.inf, -np.inf], np.nan).dropna()
    if len(rets) < 3:
        return None
    var_b = float(rets["b"].var())
    if not np.isfinite(var_b) or var_b <= 0:
        return None
    beta = float(rets["p"].cov(rets["b"]) / var_b)
    resid = rets["p"] - beta * rets["b"]
    resid_std = float(resid.std())
    t = float(resid.mean() / resid_std * np.sqrt(len(resid))) if resid_std > 0 else 0.0
    # A flat (all-cash) curve has no correlation to define.
    corr = float(rets["p"].corr(rets["b"])) if rets["p"].std() > 0 else 0.0
    return AlphaStats(
        alpha=float(resid.mean() * periods_per_year),
        t=t if np.isfinite(t) else 0.0,
        beta=beta,
        corr=corr if np.isfinite(corr) else 0.0,
        n=len(rets),
    )


def weekday_levels(worth: pd.Series) -> pd.Series:
    """
    A daily worth series with its weekend rows dropped, one row per date, oldest first.
    A worth of 0 or less is a failed valuation, not a wipe-out (TelegramSignalsBankBot
    has one 0 row on 2026-05-06), so it is dropped like a missing day.
    """
    s = pd.Series(worth.to_numpy(dtype=float), index=pd.DatetimeIndex(pd.to_datetime(worth.index)).normalize())
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[(s.index.dayofweek < 5) & (s > 0)].dropna()


def joint_returns(bot: pd.Series, bench: pd.Series) -> pd.DataFrame:
    """
    Daily returns of bot ("p") and benchmark ("b") over the dates both have,
    weekends dropped, and without the returns that span more than
    MAX_GAP_DAYS (a recorder outage, not a day's move).
    """
    levels = pd.concat({"p": weekday_levels(bot), "b": weekday_levels(bench)}, axis=1, join="inner")
    rets = levels.pct_change()
    gap = levels.index.to_series().diff().dt.days
    return rets[gap <= MAX_GAP_DAYS].replace([np.inf, -np.inf], np.nan).dropna()


def max_drawdown(worth: pd.Series) -> float:
    """Worst peak-to-trough fall, as a negative fraction (0.0 for a curve that never fell)."""
    if worth.empty:
        return 0.0
    return float((worth / worth.cummax() - 1.0).min())


def verdict(stats: AlphaStats | None) -> str:
    """The CLAUDE.md bar, in one word or two."""
    if stats is None or stats.n < MIN_OBS:
        return "too short"
    if stats.t >= 2:
        return "edge"
    if stats.t <= -2:
        return "pause candidate"
    if stats.corr >= 0.8 and stats.beta >= 1.5:
        return "levered QQQ"
    if stats.beta >= 0.8 and stats.corr >= 0.8:
        return "QQQ clone"
    return "unproven"


@dataclass(frozen=True)
class ReportRow:
    bot_name: str
    window_start: date
    window_end: date
    stats: AlphaStats | None
    max_dd: float
    bot_return: float
    qqq_return: float
    verdict: str
    cash_stats: AlphaStats | None = None


def build_report(
    worth_by_bot: dict[str, pd.Series],
    today: date,
    benchmark: str = BENCHMARK,
    holdings_by_bot: dict[str, pd.Series] | None = None,
    irx: pd.Series | float | None = None,
) -> list[ReportRow]:
    """
    One row per active bot (a worth row within ACTIVE_DAYS), measured from its
    first row against the benchmark over the same dates. Ranked by t.
    Raises if the benchmark series is missing or stale, because then every
    number would be wrong.
    """
    bench = worth_by_bot.get(benchmark)
    if bench is None or bench.empty:
        raise LookupError(f"No {benchmark} rows in portfolio_worth")
    bench_levels = weekday_levels(bench)
    if bench_levels.index[-1].date() < today - timedelta(days=MAX_BENCHMARK_AGE_DAYS):
        raise LookupError(f"{benchmark} is stale: last row {bench_levels.index[-1].date()}")
    rows = []
    for name, worth in sorted(worth_by_bot.items()):
        if name.startswith(BENCHMARK_PREFIX) or worth.empty:
            continue
        levels = weekday_levels(worth)
        if levels.empty or levels.index[-1].date() < today - timedelta(days=ACTIVE_DAYS):
            continue
        shared = pd.concat({"p": levels, "b": bench_levels}, axis=1, join="inner").dropna()
        if len(shared) < 2:
            continue
        rets = joint_returns(worth, bench)
        stats = alpha_stats(rets["p"], rets["b"]) if len(rets) else None
        cash_stats = None
        if len(rets) and holdings_by_bot and name in holdings_by_bot:
            h_series = holdings_by_bot[name]
            shares = {}
            for dt, h in h_series.items():
                norm_dt = pd.to_datetime(dt).normalize()
                usd = h.get("USD", 0.0) if isinstance(h, dict) else 0.0
                w = worth.get(dt)
                if w is None:
                    w = worth.get(norm_dt)
                shares[norm_dt] = float(usd) / float(w) if (w is not None and float(w) > 0) else 0.0
            usd_share_series = pd.Series(shares)
            cash_stats = cash_adjusted_stats(rets["p"], rets["b"], usd_share=usd_share_series, irx=irx)
        rows.append(
            ReportRow(
                bot_name=name,
                window_start=shared.index[0].date(),
                window_end=shared.index[-1].date(),
                stats=stats,
                max_dd=max_drawdown(shared["p"]),
                bot_return=float(shared["p"].iloc[-1] / shared["p"].iloc[0] - 1.0),
                qqq_return=float(shared["b"].iloc[-1] / shared["b"].iloc[0] - 1.0),
                verdict=verdict(stats),
                cash_stats=cash_stats,
            )
        )
    return sorted(rows, key=lambda r: -(r.stats.t if r.stats else -np.inf))


def load_worth(session) -> dict[str, pd.Series]:
    """Every bot's portfolio_worth series, keyed by bot name."""
    rows = session.query(PortfolioWorth.bot_name, PortfolioWorth.date, PortfolioWorth.portfolio_worth).all()
    frame = pd.DataFrame(rows, columns=["bot", "date", "worth"])
    return {name: g.set_index("date")["worth"].astype(float) for name, g in frame.groupby("bot")}


def load_holdings(session) -> dict[str, pd.Series]:
    """Every bot's holdings series, keyed by bot name."""
    rows = session.query(PortfolioWorth.bot_name, PortfolioWorth.date, PortfolioWorth.holdings).all()
    frame = pd.DataFrame(rows, columns=["bot", "date", "holdings"])
    return {name: g.set_index("date")["holdings"] for name, g in frame.groupby("bot")}


def write_report(session, rows: list[ReportRow], report_date: date) -> int:
    """Upsert one bot_alpha_report row per bot for report_date. Returns rows written."""
    existing = {r.bot_name: r for r in session.query(BotAlphaReport).filter(BotAlphaReport.report_date == report_date)}
    for row in rows:
        rec = existing.get(row.bot_name) or BotAlphaReport(report_date=report_date, bot_name=row.bot_name)
        s = row.stats
        rec.window_start, rec.window_end = row.window_start, row.window_end
        rec.n_obs = s.n if s else 0
        rec.alpha = s.alpha if s else None
        rec.alpha_t = s.t if s else None
        rec.beta = s.beta if s else None
        rec.corr = s.corr if s else None
        rec.max_dd = row.max_dd
        rec.bot_return = row.bot_return
        rec.qqq_return = row.qqq_return
        rec.verdict = row.verdict
        session.add(rec)
    return len(rows)


def format_report(rows: list[ReportRow]) -> str:
    """A fixed-width ranked table for the job log."""
    has_cash_stats = any(r.cash_stats is not None for r in rows)
    if has_cash_stats:
        head = f"{'bot':<34}{'since':>11}{'n':>5}{'alpha':>9}{'t':>7}{'beta':>7}{'corr':>7}{'cAlpha':>9}{'ct':>7}{'maxDD':>8}{'ret':>8}{'QQQ':>8}  verdict"
    else:
        head = f"{'bot':<34}{'since':>11}{'n':>5}{'alpha':>9}{'t':>7}{'beta':>7}{'corr':>7}{'maxDD':>8}{'ret':>8}{'QQQ':>8}  verdict"
    lines = [head, "-" * len(head)]
    for r in rows:
        s = r.stats
        nums = (
            f"{s.n:>5}{s.alpha:>+9.1%}{s.t:>7.2f}{s.beta:>7.2f}{s.corr:>7.2f}"
            if s
            else f"{0:>5}{'':>9}{'':>7}{'':>7}{'':>7}"
        )
        c_nums = ""
        if has_cash_stats:
            cs = r.cash_stats
            c_nums = f"{cs.alpha:>+9.1%}{cs.t:>7.2f}" if cs else f"{'':>9}{'':>7}"
        lines.append(
            f"{r.bot_name[:33]:<34}{r.window_start!s:>11}{nums}{c_nums}{r.max_dd:>8.1%}{r.bot_return:>+8.1%}"
            f"{r.qqq_return:>+8.1%}  {r.verdict}"
        )
    return "\n".join(lines)


def run_weekly_report(today: date | None = None) -> list[ReportRow]:
    """Build, store and log this week's report. Raises if the benchmark is missing or stale."""
    today = today or datetime.now(UTC).date()
    with get_db_session() as session:
        worth = load_worth(session)
        holdings = load_holdings(session)
        irx = load_irx()
        rows = build_report(worth, today, holdings_by_bot=holdings, irx=irx if not irx.empty else None)
        write_report(session, rows, today)
    logger.info("Alpha vs QQQ, live windows, as of %s:\n%s", today, format_report(rows))
    for r in rows:
        if r.verdict == "pause candidate":
            logger.warning(
                "%s: alpha %+.1f%%/yr at t %.2f since %s — significantly negative; consider pausing it (values.yaml)",
                r.bot_name,
                r.stats.alpha * 100,
                r.stats.t,
                r.window_start,
            )
    return rows
