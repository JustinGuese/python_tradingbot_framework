"""Shared walk-forward grid tuning harness for option strategies.

Implements the walk-forward protocol from docs/backtests/retune-2026-09.md and
option-bots-round2-2026-09.md:
  1. Pick candidate combinations on H1 by rank_key (default: alpha_t).
  2. Judge on unseen H2.
  3. Ship tuned parameters only if the H1 winner beats the a-priori defaults on H2.
     Otherwise defaults ship.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from scripts.onetime_option_bots_backtest import metrics as base_metrics
from tradingbot.utils.alpha_report import alpha_stats, AlphaStats


def true_cagr(
    total_return: float | None = None,
    start: Any = None,
    end: Any = None,
    curve: pd.Series | None = None,
) -> float:
    """Annualized compound growth rate from total return and date window, or directly from an equity curve.

    CAGR = (1 + total_return) ** (1 / years) - 1
    where years = (end - start).days / 365.25.
    Returns NaN if duration <= 0, total_return < -1, or curve cannot be evaluated.
    """
    if curve is not None and len(curve) >= 2:
        start = curve.index[0]
        end = curve.index[-1]
        v_start = float(curve.iloc[0])
        v_end = float(curve.iloc[-1])
        if v_start <= 0:
            return float("nan")
        total_return = (v_end / v_start) - 1.0

    if total_return is None or start is None or end is None:
        return float("nan")

    try:
        s = pd.to_datetime(start)
        e = pd.to_datetime(end)
        if getattr(s, "tzinfo", None) is not None:
            s = s.tz_localize(None) if hasattr(s, "tz_localize") else s.replace(tzinfo=None)
        if getattr(e, "tzinfo", None) is not None:
            e = e.tz_localize(None) if hasattr(e, "tz_localize") else e.replace(tzinfo=None)
    except Exception:
        return float("nan")

    days = (e - s).days
    if days <= 0:
        return float("nan")
    years = days / 365.25
    if 1 + total_return <= 0:
        return -1.0 if 1 + total_return == 0 else float("nan")
    try:
        return float((1 + total_return) ** (1 / years) - 1)
    except Exception:
        return float("nan")


def compute_sharpe(
    curve: pd.Series, risk_free_rate: float = 0.0, periods_per_year: float = 252.0
) -> float:
    """Annualized Sharpe ratio computed from daily curve returns."""
    if len(curve) < 3:
        return float("nan")
    rets = curve.pct_change().dropna()
    rets = rets.replace([np.inf, -np.inf], np.nan).dropna()
    if len(rets) < 2:
        return float("nan")
    std = float(rets.std())
    if not np.isfinite(std) or std <= 0:
        return float("nan")
    mean_ret = float(rets.mean()) * periods_per_year
    ann_vol = std * math.sqrt(periods_per_year)
    if ann_vol <= 0:
        return float("nan")
    res = (mean_ret - risk_free_rate) / ann_vol
    return float(res) if np.isfinite(res) else float("nan")


def compute_max_dd(curve: pd.Series) -> float:
    """Maximum drawdown as a negative fraction (e.g. -0.25)."""
    if len(curve) == 0:
        return float("nan")
    cummax = curve.cummax()
    dd = curve / cummax - 1.0
    min_dd = float(dd.min())
    return min_dd if np.isfinite(min_dd) else float("nan")


def metrics(curve: pd.Series, qqq: pd.Series | None = None) -> dict[str, float]:
    """Compute performance and alpha metrics for an equity curve vs benchmark.

    Reuses `alpha_stats` and `base_metrics` from `scripts/onetime_option_bots_backtest.py`.
    Returns NaN for alpha/t/beta/corr if curve or benchmark has fewer than 3 shared points.
    """
    nan_dict = {
        "cagr": float("nan"),
        "true_cagr": float("nan"),
        "alpha": float("nan"),
        "t": float("nan"),
        "alpha_t": float("nan"),
        "beta": float("nan"),
        "corr": float("nan"),
        "sharpe": float("nan"),
        "Sharpe": float("nan"),
        "max_dd": float("nan"),
    }
    if curve is None or len(curve) < 3:
        if curve is not None and len(curve) >= 2:
            nan_dict["true_cagr"] = true_cagr(curve=curve)
            nan_dict["cagr"] = nan_dict["true_cagr"]
            nan_dict["max_dd"] = compute_max_dd(curve)
        return nan_dict

    if qqq is None or len(qqq) < 3:
        nan_dict["true_cagr"] = true_cagr(curve=curve)
        nan_dict["cagr"] = nan_dict["true_cagr"]
        nan_dict["sharpe"] = compute_sharpe(curve)
        nan_dict["Sharpe"] = nan_dict["sharpe"]
        nan_dict["max_dd"] = compute_max_dd(curve)
        return nan_dict

    try:
        m = base_metrics(curve, qqq)
        alpha = m.get("alpha")
        t = m.get("t")
        beta = m.get("beta")
        corr = m.get("corr")
        max_dd = m.get("max_dd")
        cagr = m.get("cagr")
    except Exception:
        alpha, t, beta, corr, max_dd, cagr = None, None, None, None, None, None

    tc = true_cagr(curve=curve)
    if not np.isfinite(tc) and cagr is not None and np.isfinite(cagr):
        tc = float(cagr)
    sh = compute_sharpe(curve)
    if max_dd is None or not np.isfinite(max_dd):
        max_dd = compute_max_dd(curve)

    def _clean(val: Any) -> float:
        return float(val) if val is not None and np.isfinite(val) else float("nan")

    clean_alpha = _clean(alpha)
    clean_t = _clean(t)
    clean_beta = _clean(beta)
    clean_corr = _clean(corr)
    clean_tc = _clean(tc)
    clean_sh = _clean(sh)
    clean_dd = _clean(max_dd)

    return {
        "cagr": clean_tc,
        "true_cagr": clean_tc,
        "alpha": clean_alpha,
        "t": clean_t,
        "alpha_t": clean_t,
        "beta": clean_beta,
        "corr": clean_corr,
        "sharpe": clean_sh,
        "Sharpe": clean_sh,
        "max_dd": clean_dd,
    }


def ship_decision(defaults_h2: Any, winner_h2: Any) -> str:
    """Decide whether to ship tuned parameters or keep the defaults.

    Returns "winner" or "defaults".

    Protocol: pick on H1, judge on H2. Ship tuned values only if the
    H1 winner beats the a-priori defaults on H2. Otherwise defaults ship.
    An empty or too-short H2 series yields NaN t and never "winner".
    """
    def _extract_t(val: Any) -> float:
        if val is None:
            return float("nan")
        if isinstance(val, (int, float, np.number)):
            return float(val) if np.isfinite(val) else float("nan")
        if isinstance(val, (dict, pd.Series)):
            for k in ("H2_t", "t", "alpha_t", "H2_alpha_t"):
                if k in val:
                    v = val[k]
                    return float(v) if v is not None and np.isfinite(v) else float("nan")
        if hasattr(val, "t"):
            v = getattr(val, "t")
            return float(v) if v is not None and np.isfinite(v) else float("nan")
        if hasattr(val, "alpha_t"):
            v = getattr(val, "alpha_t")
            return float(v) if v is not None and np.isfinite(v) else float("nan")
        return float("nan")

    t_def = _extract_t(defaults_h2)
    t_win = _extract_t(winner_h2)

    if np.isnan(t_win) or not np.isfinite(t_win):
        return "defaults"

    if np.isnan(t_def) or not np.isfinite(t_def):
        return "winner" if t_win > 0 else "defaults"

    if t_win > t_def:
        return "winner"

    return "defaults"


class WalkForwardDataFrame(pd.DataFrame):
    """DataFrame wrapper allowing tuple or nested window indexing (e.g. df[("H1", "t")] or df["H1"]["t"])."""

    @property
    def _constructor(self):
        return WalkForwardDataFrame

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, tuple) and len(key) == 2:
            flat_key = f"{key[0]}_{key[1]}"
            if flat_key in self.columns:
                return super().__getitem__(flat_key)
        elif isinstance(key, str) and key in ("H1", "H2", "full"):
            prefix = f"{key}_"
            sub_cols = [c for c in self.columns if c.startswith(prefix)]
            if sub_cols:
                sub_df = super().__getitem__(sub_cols).copy()
                sub_df.columns = [c[len(prefix) :] for c in sub_cols]
                return sub_df
        return super().__getitem__(key)


def _split_curve(curve: pd.Series, split: Any) -> tuple[pd.Series, pd.Series]:
    """Split equity curve into H1 and H2 series."""
    if len(curve) == 0:
        return curve.copy(), curve.copy()

    if split is None:
        half = len(curve) // 2
        return curve.iloc[:half], curve.iloc[half:]

    if isinstance(split, int):
        return curve.iloc[:split], curve.iloc[split:]

    if isinstance(split, (str, pd.Timestamp, datetime.date, datetime.datetime)):
        split_dt = pd.to_datetime(split)
        idx = curve.index
        if hasattr(idx, "tz") and idx.tz is not None and getattr(split_dt, "tzinfo", None) is None:
            split_dt = split_dt.tz_localize(idx.tz)
        elif hasattr(idx, "tz") and idx.tz is None and getattr(split_dt, "tzinfo", None) is not None:
            split_dt = split_dt.tz_localize(None)
        return curve.loc[:split_dt], curve.loc[split_dt:]

    half = len(curve) // 2
    return curve.iloc[:half], curve.iloc[half:]


def _extract_run_output(res: Any, default_bench: pd.Series | None) -> tuple[pd.Series, int, pd.Series | None]:
    """Parse (curve, trades, bench) from arbitrary run_fn return formats."""
    if isinstance(res, dict):
        curve = res.get("curve")
        trades = res.get("trades", 0)
        bench = res.get("benchmark", res.get("qqq", default_bench))
    elif isinstance(res, tuple):
        if len(res) == 1:
            curve, trades, bench = res[0], 0, default_bench
        elif len(res) == 2:
            curve, trades, bench = res[0], res[1], default_bench
        else:
            curve, trades, bench = res[0], res[1], res[2]
    elif isinstance(res, pd.DataFrame):
        bench = default_bench
        if "strategy" in res.columns:
            curve = res["strategy"]
            if "qqq" in res.columns:
                bench = res["qqq"]
            elif "benchmark" in res.columns:
                bench = res["benchmark"]
        elif "close" in res.columns:
            curve = res["close"]
        else:
            curve = res.iloc[:, 0]
        trades = 0
    elif isinstance(res, pd.Series):
        curve = res
        trades = 0
        bench = default_bench
    else:
        raise TypeError(f"Unsupported run_fn output type: {type(res)}")

    if hasattr(trades, "trades"):
        trades_count = int(trades.trades)
    elif hasattr(trades, "__len__") and not isinstance(trades, (str, bytes)):
        trades_count = len(trades)
    elif isinstance(trades, (int, float, np.number)):
        trades_count = int(trades)
    else:
        trades_count = 0

    return curve, trades_count, bench


def summary_table(
    df: pd.DataFrame,
    bot_name: str = "bot",
    defaults_row: pd.Series | dict | None = None,
    h1_winner_row: pd.Series | dict | None = None,
) -> pd.DataFrame:
    """Build summary DataFrame matching the columns of option-bots-round2-2026-09.md."""
    grid_size = len(df)
    if grid_size == 0:
        return pd.DataFrame(
            columns=[
                "Bot",
                "Grid",
                "Defaults, H2 t",
                "H1 winner, H2 t",
                "Top-10 mean H2 t",
                "Grid with H2 t > 0",
                "Shipped",
            ]
        )

    if defaults_row is None:
        defaults_row = df.iloc[0]
    if h1_winner_row is None:
        h1_winner_row = df.iloc[0]

    defaults_h2_t = float(defaults_row.get("H2_t", defaults_row.get("t", float("nan"))))
    winner_h2_t = float(h1_winner_row.get("H2_t", h1_winner_row.get("t", float("nan"))))

    # Top-10 mean H2 t
    h1_col = "H1_t" if "H1_t" in df.columns else "t"
    h2_col = "H2_t" if "H2_t" in df.columns else "t"
    ranked = df.sort_values(by=h1_col, ascending=False, na_position="last")
    top10_mean_h2_t = float(ranked[h2_col].head(10).mean())

    # Share of grid with H2 t > 0
    valid_h2 = df[h2_col].dropna()
    share_pos = float((valid_h2 > 0).mean()) if len(valid_h2) > 0 else float("nan")

    decision = ship_decision(defaults_row, h1_winner_row)
    shipped = "Yes" if decision == "winner" else "No"

    return pd.DataFrame(
        [
            {
                "Bot": bot_name,
                "Grid": grid_size,
                "Defaults, H2 t": f"{defaults_h2_t:.2f}" if np.isfinite(defaults_h2_t) else "NaN",
                "H1 winner, H2 t": f"{winner_h2_t:.2f}" if np.isfinite(winner_h2_t) else "NaN",
                "Top-10 mean H2 t": f"{top10_mean_h2_t:.2f}" if np.isfinite(top10_mean_h2_t) else "NaN",
                "Grid with H2 t > 0": f"{share_pos:.0%}" if np.isfinite(share_pos) else "NaN",
                "Shipped": shipped,
            }
        ]
    )


def walk_forward(
    grid: Sequence[Any],
    run_fn: Callable[[Any], Any],
    split: Any,
    rank_key: str = "alpha_t",
    defaults: Any = None,
    bot_name: str = "bot",
    benchmark: pd.Series | None = None,
    n_jobs: int = 1,
    verbose: bool = True,
) -> WalkForwardDataFrame:
    """Walk-forward grid search runner.

    Args:
        grid: List of combinations / parameter sets to evaluate.
        run_fn: Callable returning (curve, trades) or (curve, trades, bench) or dict.
        split: Date or index to split H1 (train) and H2 (test).
        rank_key: Objective to pick H1 winner ("alpha_t", "t", "sharpe", "alpha", etc.).
        defaults: A-priori default parameters/rules. If None, grid[0] is used.
        bot_name: Name of the strategy / bot for summary reports.
        benchmark: Benchmark close series (e.g. QQQ).
        n_jobs: Parallel jobs (-1 for all CPUs, 1 for synchronous).
        verbose: If True, prints markdown summary table.

    Returns:
        WalkForwardDataFrame with one row per combination containing H1/H2/full
        alpha, t, beta, corr, Sharpe, true_cagr, max DD, trade count.
    """
    combos = list(grid)
    if not combos:
        cols = [
            "combo",
            "params",
            "H1_alpha",
            "H1_t",
            "H1_alpha_t",
            "H1_beta",
            "H1_corr",
            "H1_sharpe",
            "H1_Sharpe",
            "H1_cagr",
            "H1_true_cagr",
            "H1_max_dd",
            "H1_trades",
            "H2_alpha",
            "H2_t",
            "H2_alpha_t",
            "H2_beta",
            "H2_corr",
            "H2_sharpe",
            "H2_Sharpe",
            "H2_cagr",
            "H2_true_cagr",
            "H2_max_dd",
            "H2_trades",
            "full_alpha",
            "full_t",
            "full_alpha_t",
            "full_beta",
            "full_corr",
            "full_sharpe",
            "full_Sharpe",
            "full_cagr",
            "full_true_cagr",
            "full_max_dd",
            "full_trades",
            "trades",
            "trade_count",
        ]
        empty_df = WalkForwardDataFrame(columns=cols)
        empty_df.attrs["summary_df"] = summary_table(empty_df, bot_name=bot_name)
        empty_df.attrs["summary_markdown"] = empty_df.attrs["summary_df"].to_markdown(index=False)
        return empty_df

    # Run simulations
    if n_jobs != 1:
        try:
            from joblib import Parallel, delayed

            raw_results = Parallel(n_jobs=n_jobs)(delayed(run_fn)(c) for c in combos)
        except Exception:
            raw_results = [run_fn(c) for c in combos]
    else:
        raw_results = [run_fn(c) for c in combos]

    rows = []
    defaults_idx = 0 if defaults is None else None

    for i, (combo, res) in enumerate(zip(combos, raw_results, strict=True)):
        if defaults is not None and combo == defaults and defaults_idx is None:
            defaults_idx = i

        curve, trades, bench = _extract_run_output(res, benchmark)
        h1_curve, h2_curve = _split_curve(curve, split)

        h1_m = metrics(h1_curve, bench)
        h2_m = metrics(h2_curve, bench)
        full_m = metrics(curve, bench)

        row = {
            "combo": combo,
            "params": combo,
            "H1_alpha": h1_m["alpha"],
            "H1_t": h1_m["t"],
            "H1_alpha_t": h1_m["alpha_t"],
            "H1_beta": h1_m["beta"],
            "H1_corr": h1_m["corr"],
            "H1_sharpe": h1_m["sharpe"],
            "H1_Sharpe": h1_m["Sharpe"],
            "H1_cagr": h1_m["cagr"],
            "H1_true_cagr": h1_m["true_cagr"],
            "H1_max_dd": h1_m["max_dd"],
            "H1_trades": trades,
            "H2_alpha": h2_m["alpha"],
            "H2_t": h2_m["t"],
            "H2_alpha_t": h2_m["alpha_t"],
            "H2_beta": h2_m["beta"],
            "H2_corr": h2_m["corr"],
            "H2_sharpe": h2_m["sharpe"],
            "H2_Sharpe": h2_m["Sharpe"],
            "H2_cagr": h2_m["cagr"],
            "H2_true_cagr": h2_m["true_cagr"],
            "H2_max_dd": h2_m["max_dd"],
            "H2_trades": trades,
            "full_alpha": full_m["alpha"],
            "full_t": full_m["t"],
            "full_alpha_t": full_m["alpha_t"],
            "full_beta": full_m["beta"],
            "full_corr": full_m["corr"],
            "full_sharpe": full_m["sharpe"],
            "full_Sharpe": full_m["Sharpe"],
            "full_cagr": full_m["cagr"],
            "full_true_cagr": full_m["true_cagr"],
            "full_max_dd": full_m["max_dd"],
            "full_trades": trades,
            "trades": trades,
            "trade_count": trades,
        }
        rows.append(row)

    df = WalkForwardDataFrame(rows)

    # Defaults row
    if defaults_idx is not None and defaults_idx < len(df):
        defaults_row = df.iloc[defaults_idx]
    else:
        defaults_curve, def_trades, def_bench = _extract_run_output(run_fn(defaults), benchmark)
        def_h1_c, def_h2_c = _split_curve(defaults_curve, split)
        def_h1_m = metrics(def_h1_c, def_bench)
        def_h2_m = metrics(def_h2_c, def_bench)
        def_full_m = metrics(defaults_curve, def_bench)
        defaults_row = {
            "combo": defaults,
            "params": defaults,
            "H1_alpha": def_h1_m["alpha"],
            "H1_t": def_h1_m["t"],
            "H1_alpha_t": def_h1_m["alpha_t"],
            "H1_beta": def_h1_m["beta"],
            "H1_corr": def_h1_m["corr"],
            "H1_sharpe": def_h1_m["sharpe"],
            "H1_Sharpe": def_h1_m["Sharpe"],
            "H1_cagr": def_h1_m["cagr"],
            "H1_true_cagr": def_h1_m["true_cagr"],
            "H1_max_dd": def_h1_m["max_dd"],
            "H1_trades": def_trades,
            "H2_alpha": def_h2_m["alpha"],
            "H2_t": def_h2_m["t"],
            "H2_alpha_t": def_h2_m["alpha_t"],
            "H2_beta": def_h2_m["beta"],
            "H2_corr": def_h2_m["corr"],
            "H2_sharpe": def_h2_m["sharpe"],
            "H2_Sharpe": def_h2_m["Sharpe"],
            "H2_cagr": def_h2_m["cagr"],
            "H2_true_cagr": def_h2_m["true_cagr"],
            "H2_max_dd": def_h2_m["max_dd"],
            "H2_trades": def_trades,
            "full_alpha": def_full_m["alpha"],
            "full_t": def_full_m["t"],
            "full_alpha_t": def_full_m["alpha_t"],
            "full_beta": def_full_m["beta"],
            "full_corr": def_full_m["corr"],
            "full_sharpe": def_full_m["sharpe"],
            "full_Sharpe": def_full_m["Sharpe"],
            "full_cagr": def_full_m["cagr"],
            "full_true_cagr": def_full_m["true_cagr"],
            "full_max_dd": def_full_m["max_dd"],
            "full_trades": def_trades,
            "trades": def_trades,
            "trade_count": def_trades,
        }

    # H1 Winner selection
    rank_col = "H1_t"
    if rank_key in ("alpha_t", "t"):
        rank_col = "H1_t"
    elif rank_key in ("sharpe", "Sharpe"):
        rank_col = "H1_sharpe"
    elif rank_key == "alpha":
        rank_col = "H1_alpha"
    elif f"H1_{rank_key}" in df.columns:
        rank_col = f"H1_{rank_key}"
    elif rank_key in df.columns:
        rank_col = rank_key

    valid_scores = df[rank_col].dropna()
    if len(valid_scores) > 0:
        winner_idx = valid_scores.idxmax()
        winner_row = df.loc[winner_idx]
    else:
        winner_row = df.iloc[0]

    # Generate summary report
    sum_df = summary_table(df, bot_name=bot_name, defaults_row=defaults_row, h1_winner_row=winner_row)
    sum_md = sum_df.to_markdown(index=False)

    df.attrs["summary_df"] = sum_df
    df.attrs["summary_markdown"] = sum_md
    df.attrs["defaults_row"] = defaults_row
    df.attrs["winner_row"] = winner_row
    df.attrs["ship_decision"] = ship_decision(defaults_row, winner_row)

    if verbose:
        print(f"\n### {bot_name}: {len(combos)} combos, picked on H1 {rank_key}, judged on H2")
        print(sum_md)

    return df
