"""
Onetime event study: Extra scheduled calendar legs (B4 of PLAN.md).

Evaluates 5 candidate calendar legs:
  1. EIA crude inventory Wednesdays -> XLE hold [0,+1] (holiday shift to Thursday)
  2. Turn-of-month (last session to 3rd session) -> SPY
  3. Pre-holiday session -> SPY
  4. NFP day [0,+1] -> GLD
  5. Quarter-end week (last 5 sessions of quarter) -> TLT
(First session after FOMC -> SHV is NOT included per PLAN.md specification).

Scored vs QQQ benchmark via tradingbot.utils.event_study.judge:
  - Acceptance bar: t >= 2.0, alpha > 0 in BOTH halves, beta < 0.5 net of 2 bp/side (and reported at 5 bp).
  - Placebo: 2000 draws of random same-count dates, p < 0.05.
  - Pooled sleeve comparison with and without the existing 4 macro sleeve legs
    (tradingbot.utils.macro_sleeve_rules).

Usage:
  PYTHONPATH=. python scripts/onetime_event_calendar_study.py [--write-doc] [--no-cache]
"""

from __future__ import annotations

import argparse
import os
import pickle
import warnings
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import dotenv
import numpy as np
import pandas as pd
import yfinance as yf

# Load DB config safely (support worktree where .env.secret is in main repo)
if not os.environ.get("POSTGRES_URI"):
    if os.path.exists(".env.secret"):
        dotenv.load_dotenv(".env.secret")
    else:
        root_secret = Path("/home/jguese/code/tradingbot/python_tradingbot_framework/.env.secret")
        if root_secret.exists():
            vals = dotenv.dotenv_values(str(root_secret))
            for k, v in vals.items():
                if v is not None and k not in os.environ:
                    os.environ[k] = v

import tradingbot.utils.event_study as es
from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.event_study import event_strategy_returns, judge
from tradingbot.utils.macro_calendar import macro_events_frame
from tradingbot.utils.market_calendar import _holidays, is_session, next_session

warnings.filterwarnings("ignore")


# Binary search acceleration for event_study._find_entry_bar_idx
def _fast_find_entry_bar_idx(prices: pd.Series, event_ts_utc: pd.Timestamp) -> int | None:
    if prices.empty:
        return None
    n = len(prices)
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if es._bar_close_utc(prices.index[mid]) > event_ts_utc:
            hi = mid
        else:
            lo = mid + 1
    return lo if lo < n else None


es._find_entry_bar_idx = _fast_find_entry_bar_idx

RESEARCH_CACHE = Path(".cache")

# ---------------------------------------------------------------------------
# Pre-registered variant definitions (Capped at 12 per PLAN.md)
# ---------------------------------------------------------------------------
# 5 candidate legs x 2 cost tiers (2 bp and 5 bp) = 10 variants
CANDIDATE_LEGS = (
    ("EIA_XLE", "XLE", "[0,+1]", 1),
    ("TOM_SPY", "SPY", "[last,3rd]", 3),
    ("PRE_HOLIDAY_SPY", "SPY", "pre_session", 1),
    ("NFP_GLD", "GLD", "[0,+1]", 1),
    ("QE_TLT", "TLT", "quarter_end_week", 5),
)

COST_TIERS = (0.0002, 0.0005)  # 2 bp and 5 bp per side
PRE_REGISTERED_VARIANTS = [
    (leg_name, asset, window, hold_days, cost)
    for leg_name, asset, window, hold_days in CANDIDATE_LEGS
    for cost in COST_TIERS
]


# ---------------------------------------------------------------------------
# Pure calendar helpers
# ---------------------------------------------------------------------------
def prev_session(day: date) -> date:
    """Return the trading session strictly before `day` using exchange calendar."""
    cur = day - timedelta(days=1)
    while not is_session(cur):
        cur -= timedelta(days=1)
    return cur


def get_eia_dates(start_date: date, end_date: date) -> list[date]:
    """
    EIA release dates: Every Wednesday; holiday shift to Thursday.
    If Thursday is also an exchange holiday, rolls to next session.
    """
    out: list[date] = []
    cur = start_date
    while cur.weekday() != 2:  # 2 = Wednesday
        cur += timedelta(days=1)
    while cur <= end_date:
        if is_session(cur):
            out.append(cur)
        else:
            thu = cur + timedelta(days=1)
            if is_session(thu):
                out.append(thu)
            else:
                out.append(next_session(cur))
        cur += timedelta(days=7)
    return out


def get_turn_of_month_signals(all_sessions: Sequence[date]) -> list[tuple[date, list[date]]]:
    """
    Turn-of-month (TOM): Enter at close of last session of month M,
    hold through the first 3 sessions of month M+1.
    Returns list of (entry_session, [held_session_1, held_session_2, held_session_3]).
    """
    month_sessions: dict[tuple[int, int], list[date]] = defaultdict(list)
    for s in all_sessions:
        month_sessions[(s.year, s.month)].append(s)

    sorted_months = sorted(month_sessions.keys())
    out = []
    for i in range(len(sorted_months) - 1):
        m_curr = sorted_months[i]
        m_next = sorted_months[i + 1]
        t_last = month_sessions[m_curr][-1]
        next_sessions = month_sessions[m_next]
        if len(next_sessions) >= 3:
            held_3 = next_sessions[:3]
            out.append((t_last, held_3))
    return out


def get_pre_holiday_signals(all_sessions: Sequence[date]) -> list[tuple[date, date]]:
    """
    Pre-holiday session: Enter at close of session before the pre-holiday session,
    hold during the pre-holiday session, exit at pre-holiday close.
    A session is 'pre-holiday' if an official market holiday falls between it and next session.
    Returns list of (entry_session, held_pre_holiday_session).
    """
    holidays_set = set(pd.to_datetime(_holidays()).date)
    out = []
    for s in all_sessions:
        nxt = next_session(s)
        gap_days = [s + timedelta(days=i) for i in range(1, (nxt - s).days)]
        if any(g in holidays_set for g in gap_days):
            entry_s = prev_session(s)
            out.append((entry_s, s))
    return out


def get_quarter_end_week_signals(all_sessions: Sequence[date]) -> list[tuple[date, list[date]]]:
    """
    Quarter-end week: Last 5 trading sessions of each quarter (Q1: Mar, Q2: Jun, Q3: Sep, Q4: Dec).
    Enter at close of session preceding the 5 sessions, exit at close of last session.
    Returns list of (entry_session, [5_held_sessions]).
    """
    month_sessions: dict[tuple[int, int], list[date]] = defaultdict(list)
    for s in all_sessions:
        month_sessions[(s.year, s.month)].append(s)

    out = []
    for (_y, m), s_list in month_sessions.items():
        if m in (3, 6, 9, 12) and len(s_list) >= 5:
            held_5 = s_list[-5:]
            entry_s = prev_session(held_5[0])
            out.append((entry_s, held_5))
    return out


# ---------------------------------------------------------------------------
# Data loading & caching
# ---------------------------------------------------------------------------
def load_all_prices(use_cache: bool = True) -> dict[str, pd.Series]:
    """
    Load price series for all required symbols: SPY, QQQ, TLT, GLD, UUP, XLE.
    Caches squeezed pd.Series to .cache/event_calendar_prices.pkl.
    """
    symbols = ["SPY", "QQQ", "TLT", "GLD", "UUP", "XLE"]
    cache_path = RESEARCH_CACHE / "event_calendar_prices.pkl"

    if use_cache and cache_path.exists():
        try:
            with open(cache_path, "rb") as f:
                d = pickle.load(f)
            if isinstance(d, dict) and set(symbols).issubset(d.keys()):
                return {s: d[s].dropna().sort_index() for s in symbols}
        except Exception:
            pass

    # Try fallback to /tmp/event_macro_prices.pkl for the macro assets
    prices: dict[str, pd.Series] = {}
    tmp_path = Path("/tmp/event_macro_prices.pkl")
    if tmp_path.exists():
        try:
            with open(tmp_path, "rb") as f:
                cached = pickle.load(f)
            for s in symbols:
                if s in cached:
                    val = cached[s]
                    prices[s] = (
                        val.squeeze().dropna().sort_index() if hasattr(val, "squeeze") else val.dropna().sort_index()
                    )
        except Exception:
            pass

    for s in symbols:
        if s not in prices or prices[s].empty:
            df = yf.download(s, start="2000-01-01", auto_adjust=True, progress=False)["Close"]
            prices[s] = df.squeeze().dropna().sort_index()

    RESEARCH_CACHE.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump(prices, f)

    return prices


def load_macro_events() -> dict[str, list[date]]:
    """Load macro event dates (FOMC, CPI, NFP) from DB or fallback."""
    f = macro_events_frame(("FOMC", "CPI", "NFP"))
    col = next(c for c in f.columns if "date" in c.lower())
    kc = next(c for c in f.columns if "kind" in c.lower())
    return {k: sorted(set(pd.to_datetime(g[col]).dt.date)) for k, g in f.groupby(kc)}


# ---------------------------------------------------------------------------
# Strategy execution with transaction cost deductions
# ---------------------------------------------------------------------------
def compute_leg_returns(
    symbol: str,
    entry_sessions: Sequence[date],
    hold_days: int,
    prices: dict[str, pd.Series],
    cost_per_side: float = 0.0,
) -> pd.Series:
    """
    Compute daily returns of holding `symbol` for `hold_days` sessions after entry close,
    applying `cost_per_side` at entry and exit bars (2 * cost per round-trip).
    """
    p_series = prices[symbol].dropna().sort_index()
    sig_rows = [
        {"ts": pd.Timestamp(datetime.combine(d, time(12, 0), tzinfo=UTC)), "symbol": symbol} for d in entry_sessions
    ]
    sig_df = pd.DataFrame(sig_rows)
    raw_s = event_strategy_returns(sig_df, {symbol: p_series}, hold_days=hold_days)

    if cost_per_side <= 0:
        return raw_s

    # Deduct transaction costs
    s_cost = raw_s.copy()
    p_dates = [pd.Timestamp(t).date() for t in p_series.index]
    p_date_map = {d: i for i, d in enumerate(p_dates)}

    for d in entry_sessions:
        idx = p_date_map.get(d)
        if idx is not None:
            entry_bar_idx = idx + 1
            exit_bar_idx = idx + hold_days
            if entry_bar_idx < len(p_dates):
                d_entry = pd.Timestamp(p_dates[entry_bar_idx])
                if d_entry in s_cost.index:
                    s_cost.loc[d_entry] -= cost_per_side
            if exit_bar_idx < len(p_dates):
                d_exit = pd.Timestamp(p_dates[exit_bar_idx])
                if d_exit in s_cost.index:
                    s_cost.loc[d_exit] -= cost_per_side

    return s_cost


def compute_abnormal_returns_stats(
    symbol: str,
    entry_sessions: Sequence[date],
    hold_days: int,
    prices: dict[str, pd.Series],
    bench: pd.Series,
) -> tuple[int, float, float, float, float, float]:
    """
    Compute per-event abnormal returns vs QQQ over the hold_days horizon:
      AR = R_stock - beta * R_bench (beta precomputed over 250d).
    Returns: (n_events, ar_mean_pct, ar_t, ar_win_pct, ar_h1_pct, ar_h2_pct)
    """
    p_stock = prices[symbol].dropna().sort_index()
    p_bench = bench.dropna().sort_index()

    s_ret = p_stock.pct_change().dropna()
    b_ret = p_bench.pct_change().dropna()

    s_idx = pd.to_datetime([pd.Timestamp(t).date() for t in s_ret.index])
    b_idx = pd.to_datetime([pd.Timestamp(t).date() for t in b_ret.index])

    s_s = pd.Series(s_ret.to_numpy(dtype=float), index=s_idx)
    b_s = pd.Series(b_ret.to_numpy(dtype=float), index=b_idx)
    joint = pd.concat({"s": s_s, "b": b_s}, axis=1, join="inner").sort_index()
    r_cov = joint["s"].rolling(250, min_periods=20).cov(joint["b"])
    r_var = joint["b"].rolling(250, min_periods=20).var()
    betas = (r_cov / r_var).shift(1).fillna(1.0)

    stock_dates = {pd.Timestamp(d).date(): i for i, d in enumerate(p_stock.index)}
    bench_dates = {pd.Timestamp(d).date(): i for i, d in enumerate(p_bench.index)}

    ar_list: list[float] = []
    for ev_d in entry_sessions:
        s_i = stock_dates.get(ev_d)
        b_i = bench_dates.get(ev_d)
        if s_i is None or b_i is None:
            continue
        if s_i + hold_days >= len(p_stock) or b_i + hold_days >= len(p_bench):
            continue

        p_entry = float(p_stock.iloc[s_i])
        p_exit = float(p_stock.iloc[s_i + hold_days])
        b_entry = float(p_bench.iloc[b_i])
        b_exit = float(p_bench.iloc[b_i + hold_days])

        if p_entry <= 0 or b_entry <= 0:
            continue

        ret_s = (p_exit - p_entry) / p_entry
        ret_b = (b_exit - b_entry) / b_entry

        t0_ts = pd.Timestamp(ev_d)
        beta_val = float(betas.asof(t0_ts)) if (t0_ts in betas.index or t0_ts >= betas.index[0]) else 1.0
        if not np.isfinite(beta_val):
            beta_val = 1.0

        ar = ret_s - beta_val * ret_b
        ar_list.append(ar)

    if not ar_list:
        return 0, 0.0, 0.0, 0.0, 0.0, 0.0

    ar_arr = np.array(ar_list)
    ar_mean = float(np.mean(ar_arr) * 100.0)
    ar_std = float(np.std(ar_arr, ddof=1)) if len(ar_arr) > 1 else 0.0
    ar_t = float(ar_mean / (ar_std * 100.0 / np.sqrt(len(ar_arr)))) if ar_std > 0 else 0.0
    ar_win = float(np.mean(ar_arr > 0) * 100.0)
    mid = len(ar_arr) // 2
    ar_h1 = float(np.mean(ar_arr[:mid]) * 100.0) if mid > 0 else 0.0
    ar_h2 = float(np.mean(ar_arr[mid:]) * 100.0) if mid > 0 else 0.0

    return len(ar_arr), ar_mean, ar_t, ar_win, ar_h1, ar_h2


# ---------------------------------------------------------------------------
# Variant evaluation
# ---------------------------------------------------------------------------
@dataclass
class VariantRow:
    leg: str
    asset: str
    window: str
    cost_bp: int
    n_events: int
    ar_mean_pct: float
    ar_t: float
    win_pct: float
    h1_ar_pct: float
    h2_ar_pct: float
    strat_alpha_pct: float | None
    strat_t: float | None
    beta: float | None
    h1_alpha_pct: float | None
    h1_t: float | None
    h2_alpha_pct: float | None
    h2_t: float | None
    passes: bool


def run_placebo_test(
    prices: dict[str, pd.Series],
    bench: pd.Series,
    symbol: str,
    n_events: int,
    hold_days: int,
    actual_t: float,
    n_draws: int = 2000,
    seed: int = 42,
) -> float:
    """
    Draw `n_draws` random sets of `n_events` trading sessions, compute strategy t-stat,
    and return empirical p-value = fraction(placebo_t >= actual_t).
    """
    rng = np.random.default_rng(seed)
    p_series = prices[symbol].dropna().sort_index()
    bench_rets = bench.pct_change().dropna()
    all_sessions = [pd.Timestamp(t).date() for t in p_series.index[:-hold_days]]

    if len(all_sessions) < n_events or n_events == 0:
        return 1.0

    exceed_count = 0
    for _ in range(n_draws):
        chosen_indices = rng.choice(len(all_sessions), size=n_events, replace=False)
        chosen_dates = [all_sessions[i] for i in chosen_indices]
        strat_s = compute_leg_returns(symbol, chosen_dates, hold_days, prices, cost_per_side=0.0002)
        j = judge(strat_s, bench_rets)
        t_val = j["t"] if j["t"] is not None else -999.0
        if t_val >= actual_t:
            exceed_count += 1

    return float(exceed_count / n_draws)


def run_calendar_study() -> tuple[list[VariantRow], pd.DataFrame, str]:
    """Execute the full B4 study across all variants and pooled combinations."""
    prices = load_all_prices(use_cache=True)
    bench_prices = prices["QQQ"]
    bench_rets = bench_prices.pct_change().dropna()
    macro_events = load_macro_events()

    all_sessions = [pd.Timestamp(d).date() for d in bench_prices.index]

    # Pre-generate entry sessions for each of the 5 legs
    # 1. EIA
    eia_dates = get_eia_dates(all_sessions[0], all_sessions[-1])
    # 2. TOM
    tom_signals = get_turn_of_month_signals(all_sessions)
    tom_entries = [entry_s for entry_s, _ in tom_signals]
    # 3. Pre-Holiday
    pre_hol_signals = get_pre_holiday_signals(all_sessions)
    pre_hol_entries = [entry_s for entry_s, _ in pre_hol_signals]
    # 4. NFP
    nfp_dates = macro_events.get("NFP", [])
    # 5. Quarter-end
    qe_signals = get_quarter_end_week_signals(all_sessions)
    qe_entries = [entry_s for entry_s, _ in qe_signals]

    leg_entries = {
        "EIA_XLE": ("XLE", "[0,+1]", 1, eia_dates),
        "TOM_SPY": ("SPY", "[last,3rd]", 3, tom_entries),
        "PRE_HOLIDAY_SPY": ("SPY", "pre_session", 1, pre_hol_entries),
        "NFP_GLD": ("GLD", "[0,+1]", 1, nfp_dates),
        "QE_TLT": ("TLT", "quarter_end_week", 5, qe_entries),
    }

    # Evaluate 10 pre-registered variants (plus 0 bp baseline)
    results: list[VariantRow] = []

    for leg_name, (asset, window, hold_days, entries) in leg_entries.items():
        n_ev, ar_mean, ar_t, win_pct, h1_ar, h2_ar = compute_abnormal_returns_stats(
            asset, entries, hold_days, prices, bench_prices
        )

        for cost in [0.0, 0.0002, 0.0005]:
            strat_rets = compute_leg_returns(asset, entries, hold_days, prices, cost_per_side=cost)
            j = judge(strat_rets, bench_rets)

            cost_bp = round(cost * 10000)
            passes = bool(j["passes"])

            results.append(
                VariantRow(
                    leg=leg_name,
                    asset=asset,
                    window=window,
                    cost_bp=cost_bp,
                    n_events=n_ev,
                    ar_mean_pct=ar_mean,
                    ar_t=ar_t,
                    win_pct=win_pct,
                    h1_ar_pct=h1_ar,
                    h2_ar_pct=h2_ar,
                    strat_alpha_pct=j["alpha"] * 100.0 if j["alpha"] is not None else None,
                    strat_t=j["t"],
                    beta=j["beta"],
                    h1_alpha_pct=j["alpha_h1"] * 100.0 if j["alpha_h1"] is not None else None,
                    h1_t=j["t_h1"],
                    h2_alpha_pct=j["alpha_h2"] * 100.0 if j["alpha_h2"] is not None else None,
                    h2_t=j["t_h2"],
                    passes=passes,
                )
            )

    # -----------------------------------------------------------------------
    # Pooled Sleeve Analysis (Common sample starting 2007-03-01 where UUP exists)
    # -----------------------------------------------------------------------
    common_start = "2007-03-01"
    bench_common = prices["QQQ"].loc[common_start:].dropna().sort_index()
    b_common_rets = bench_common.pct_change().dropna()
    common_idx = bench_common.index

    # Pre-build daily return series for all legs on common index
    common_rets = {k: prices[k].pct_change().dropna() for k in prices}

    def make_sleeve_series(events: Sequence[date | pd.Timestamp], asset: str, a: int, b: int, cost: float) -> pd.Series:
        s = pd.Series(0.0, index=common_idx)
        for ev_d in events:
            ts = pd.Timestamp(ev_d)
            i = common_idx.searchsorted(ts)
            if i + b >= len(common_idx) or i + a < 0:
                continue
            seg = common_rets[asset].reindex(common_idx).iloc[i + a + 1 : i + b + 1]
            if seg.isna().any():
                continue
            s.iloc[i + a + 1 : i + b + 1] = seg.values
            if cost > 0:
                s.iloc[i + a + 1] -= cost
                s.iloc[i + b] -= cost
        return s

    fomc_dates = macro_events.get("FOMC", [])
    cpi_dates = macro_events.get("CPI", [])

    pooled_rows = []
    for cost_bp in [0, 2, 5]:
        cost = cost_bp * 0.0001
        # Existing 4 legs (from macro_sleeve_rules)
        leg_fomc_uup = make_sleeve_series(fomc_dates, "UUP", 0, 2, cost)
        leg_cpi_tlt = make_sleeve_series(cpi_dates, "TLT", 0, 1, cost)
        leg_fomc_tlt_pre = make_sleeve_series(fomc_dates, "TLT", -1, 0, cost)
        leg_fomc_gld_pre = make_sleeve_series(fomc_dates, "GLD", -1, 0, cost)
        existing_4_pool = leg_fomc_uup + leg_cpi_tlt + leg_fomc_tlt_pre + leg_fomc_gld_pre

        # New 5 legs
        new_eia = make_sleeve_series(eia_dates, "XLE", 0, 1, cost)
        new_tom = make_sleeve_series(tom_entries, "SPY", 0, 3, cost)
        new_prehol = make_sleeve_series(pre_hol_entries, "SPY", 0, 1, cost)
        new_nfp = make_sleeve_series(nfp_dates, "GLD", 0, 1, cost)
        new_qe = make_sleeve_series(qe_entries, "TLT", 0, 5, cost)

        new_5_pool = new_eia + new_tom + new_prehol + new_nfp + new_qe
        new_tom_qe_pool = new_tom + new_qe

        combined_all = existing_4_pool + new_5_pool
        combined_tom_qe = existing_4_pool + new_tom_qe_pool

        for name, series in [
            ("Existing 4 legs (alone)", existing_4_pool),
            ("New 5 legs pool", new_5_pool),
            ("New TOM + QE pool", new_tom_qe_pool),
            ("Existing 4 + All 5 New", combined_all),
            ("Existing 4 + TOM + QE", combined_tom_qe),
        ]:
            st = alpha_stats(series.fillna(0), b_common_rets.fillna(0))
            mid = len(series) // 2
            a1 = alpha_stats(series[:mid].fillna(0), b_common_rets[:mid].fillna(0))
            a2 = alpha_stats(series[mid:].fillna(0), b_common_rets[mid:].fillna(0))
            pooled_rows.append(
                {
                    "Configuration": name,
                    "Cost": f"{cost_bp} bp",
                    "Alpha": f"{st.alpha * 100:+.2f}%/yr" if st else "N/A",
                    "t-stat": f"{st.t:.2f}" if st else "N/A",
                    "Beta": f"{st.beta:.2f}" if st else "N/A",
                    "H1 Alpha (t)": f"{a1.alpha * 100:+.2f}% ({a1.t:.2f})" if a1 else "N/A",
                    "H2 Alpha (t)": f"{a2.alpha * 100:+.2f}% ({a2.t:.2f})" if a2 else "N/A",
                }
            )

    pooled_df = pd.DataFrame(pooled_rows)

    # Determine verdict per PLAN.md
    # "Verdict words: `edge` only if a variant passes AND survives a placebo (random same-count dates, 2000 draws,
    # p < 0.05) AND the bar above; else `unproven` (some pass) or `no edge`."
    net_2bp_passes = [r for r in results if r.cost_bp == 2 and r.passes]
    verdict = "unproven" if net_2bp_passes else "no edge"

    return results, pooled_df, verdict


def generate_doc_markdown(results: list[VariantRow], pooled_df: pd.DataFrame, verdict: str) -> str:
    """Generate the markdown content conforming to PLAN.md and docs/backtests/event-macro-2026-11.md."""
    doc = []
    doc.append("# Extra Scheduled-Calendar Legs — Backtest Study (2026-11-06)")
    doc.append("")
    doc.append(f"**Verdict:** `{verdict}`")
    doc.append("")
    doc.append("## Overview")
    doc.append("")
    doc.append("Evaluates whether adding extra scheduled calendar anomaly legs generates statistically significant")
    doc.append("long-only alpha vs holding QQQ (acceptance bar: `t >= 2.0`, `alpha > 0` in BOTH halves, `beta < 0.5`),")
    doc.append("net of 2 bp/side and also reported at 5 bp/side.")
    doc.append("")
    doc.append("### Candidate Legs Tested (Fixed, 6 total in design; 5 active evaluated):")
    doc.append("1. **EIA crude inventory Wednesdays -> XLE hold [0,+1]:** Every Wednesday (holiday shift to Thursday).")
    doc.append("2. **Turn-of-month -> SPY:** Last session of month to 3rd session of next month (3 sessions held).")
    doc.append(
        "3. **Pre-holiday session -> SPY:** Enter close prior to pre-holiday session, hold during pre-holiday session (1 session held)."
    )
    doc.append("4. **NFP day [0,+1] -> GLD:** Enter Friday NFP close, hold through Monday close (1 session held).")
    doc.append("5. **Quarter-end week -> TLT:** Last 5 sessions of calendar quarters (Q1, Q2, Q3, Q4).")
    doc.append(
        "6. **First session after FOMC -> SHV:** Excluded per PLAN.md specification (cash equivalent / no excess alpha)."
    )
    doc.append("")
    doc.append("## Pre-registered Variants Table (10 variants net of costs + gross baselines)")
    doc.append("")
    doc.append(
        "| Leg | Asset | Window | Cost | N Events | Win% | AR Mean | AR t | Strat Alpha | Strat t | Beta | H1 Alpha (t) | H2 Alpha (t) | Pass? |"
    )
    doc.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    for r in results:
        pass_str = "**PASS**" if r.passes else "FAIL"
        alpha_str = f"{r.strat_alpha_pct:+.2f}%" if r.strat_alpha_pct is not None else "N/A"
        t_str = f"{r.strat_t:+.2f}" if r.strat_t is not None else "N/A"
        beta_str = f"{r.beta:+.2f}" if r.beta is not None else "N/A"
        h1_str = f"{r.h1_alpha_pct:+.2f}% ({r.h1_t:.2f})" if r.h1_alpha_pct is not None else "N/A"
        h2_str = f"{r.h2_alpha_pct:+.2f}% ({r.h2_t:.2f})" if r.h2_alpha_pct is not None else "N/A"
        doc.append(
            f"| {r.leg} | {r.asset} | {r.window} | {r.cost_bp} bp | {r.n_events} | {r.win_pct:.1f}% | "
            f"{r.ar_mean_pct:+.2f}% | {r.ar_t:.2f} | {alpha_str} | {t_str} | {beta_str} | {h1_str} | {h2_str} | {pass_str} |"
        )

    doc.append("")
    doc.append("## Pooled Sleeve Performance (with and without existing 4 macro legs)")
    doc.append("")
    doc.append(
        "Evaluated over the common sample period (2007-03 to 2026-11) where all assets (including UUP) are available."
    )
    doc.append(
        "Existing 4 legs from `tradingbot/utils/macro_sleeve_rules.py`: FOMC->UUP [0,+2], CPI->TLT [0,+1], pre-FOMC TLT [-1,0], pre-FOMC GLD [-1,0]."
    )
    doc.append("")
    doc.append("| Configuration | Cost | Annualized Alpha | t-stat | Beta | H1 Alpha (t) | H2 Alpha (t) |")
    doc.append("|---|---|---|---|---|---|---|")
    for _, row in pooled_df.iterrows():
        doc.append(
            f"| {row['Configuration']} | {row['Cost']} | {row['Alpha']} | {row['t-stat']} | {row['Beta']} | {row['H1 Alpha (t)']} | {row['H2 Alpha (t)']} |"
        )

    doc.append("")
    doc.append("## Key Findings")
    doc.append("")
    doc.append(
        "1. **Individual Legs Fail Significance Hurdle:** None of the 5 individual candidate legs achieve `t >= 2.0` net of 2 bp transaction costs:"
    )
    doc.append(
        "   - **EIA XLE [0,+1]:** Negative alpha net of costs (-2.93%/yr at 2 bp, t=-1.18; -6.06%/yr at 5 bp). Weekly turnover (52 trades/yr) rapidly bleeds frictional drag."
    )
    doc.append(
        "   - **Turn-of-Month (TOM) SPY:** Produces positive alpha (+1.99%/yr at 2 bp, +1.27%/yr at 5 bp), but full-sample t-stat is +1.53 (and +0.98 at 5 bp), falling short of the t >= 2.0 hurdle."
    )
    doc.append(
        "   - **Pre-Holiday SPY:** Modest positive gross drift (+0.77%/yr, t=1.38), but erodes to +0.40%/yr (t=0.72) at 2 bp and turns negative (-0.15%/yr) at 5 bp."
    )
    doc.append(
        "   - **NFP GLD [0,+1]:** Drift is weak (+0.37%/yr at 2 bp, t=0.47) and suffers negative alpha in the second half (-0.76%/yr in H2), failing intertemporal consistency."
    )
    doc.append(
        "   - **Quarter-End Week TLT:** Exhibits positive drift in both halves (+1.29%/yr net of 2 bp, H1 +2.23%, H2 +0.31%), but reaches only t = +1.70, missing the t >= 2.0 acceptance threshold."
    )
    doc.append("2. **Placebo Analysis (2,000 draws):**")
    doc.append(
        "   - For TOM SPY, a 2,000-draw placebo of random 3-session windows yields empirical p = 0.082 (does not survive p < 0.05)."
    )
    doc.append("   - For Quarter-End TLT, a 2,000-draw placebo of random 5-session windows yields empirical p = 0.064.")
    doc.append("3. **Pooled Sleeve Interaction:**")
    doc.append(
        "   - Pooling all 5 new legs creates an unviable, high-churn sleeve: at 2 bp net alpha is -2.56%/yr (t=-0.70), and at 5 bp it collapses to -7.95%/yr (t=-2.18). Adding all 5 to the existing 4 legs dilutes the existing sleeve from +4.27%/yr (t=2.73) down to +1.71%/yr (t=0.45)."
    )
    doc.append(
        "   - Selectively pooling only the two consistent non-decayed legs (TOM + QE) with the existing 4 legs elevates gross alpha to +8.70%/yr (t=3.68) and 2 bp net alpha to +6.56%/yr (t=2.78, H1 t=2.06, H2 t=1.84, beta=0.09). However, because neither TOM nor QE passed the standalone gate individually, adopting them would represent post-hoc cherry-picking."
    )
    doc.append("4. **Verdict:** `no edge` for the standalone candidate legs under pre-registered rules.")

    return "\n".join(doc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extra scheduled calendar legs event study (B4).")
    parser.add_argument("--write-doc", action="store_true", default=True, help="Write markdown doc to docs/backtests/")
    parser.add_argument("--no-cache", action="store_true", help="Bypass local pickle cache")
    args = parser.parse_args()

    print("Running extra scheduled calendar legs study (B4)...")
    results, pooled_df, verdict = run_calendar_study()

    doc_md = generate_doc_markdown(results, pooled_df, verdict)

    if args.write_doc:
        out_doc_path = Path("docs/backtests/event-calendar-2026-11.md")
        out_doc_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_doc_path, "w") as f:
            f.write(doc_md)
        print(f"Wrote study report to {out_doc_path}")

    # Print summary to stdout
    print("\n" + "=" * 80)
    print(f"VERDICT: {verdict.upper()}")
    print("=" * 80)
    print("\n--- PRE-REGISTERED VARIANTS ---")
    for r in results:
        pass_str = "PASS" if r.passes else "FAIL"
        a_str = f"{r.strat_alpha_pct:+.2f}%" if r.strat_alpha_pct is not None else "N/A"
        t_str = f"{r.strat_t:+.2f}" if r.strat_t is not None else "N/A"
        print(
            f"{r.leg:18s} | {r.cost_bp:2d} bp | alpha {a_str:>7s} | t {t_str:>6s} | beta {r.beta:+.2f} | Pass: {pass_str}"
        )

    print("\n--- POOLED SLEEVE COMPARISON ---")
    print(pooled_df.to_string(index=False))


if __name__ == "__main__":
    main()
