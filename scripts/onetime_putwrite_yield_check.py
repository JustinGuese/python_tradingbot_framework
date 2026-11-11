"""option_PutWriteBot with T-bill interest credited on the cash collateral (2026-11).

The backtests in onetime_putwrite_backtest.py price options off ^IRX but never pay interest on
the cash backing the puts; a live broker book parks that cash in SHV (LIVETRADE_CASH_PROXY).
This re-scores ONLY pre-declared configurations, no grid: the a-priori defaults and the shipped
H1-winner rules, on SPY, plus the same two on QQQ as the single declared QQQ test.

Daily return += rf * calendar_days / 365 on every step, i.e. the whole book earns the T-bill
rate. For a cash-secured put-write the collateral is ~the whole book, so this is the right order.

  uv run python scripts/onetime_putwrite_yield_check.py --cache-dir /tmp/pw_cache
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

from scripts.onetime_putwrite_backtest import (
    SYNTHETIC_SPLIT,
    CachedSurfaceReplayMarket,
    load_cached_prices,
    load_synthetic_inputs,
    sim_synthetic_putwrite,
)
from scripts.option_tune_common import metrics
from tradingbot.option_putwritebot import OptionPutWriteBot
from tradingbot.utils.option_replay import IRX, run_strategy
from tradingbot.utils.putwrite_rules import PutWriteRules, decide_putwrite

DEFAULTS = PutWriteRules(
    underlying="SPY",
    put_delta=0.25,
    target_dte=35,
    max_term_ratio=None,
    trend_filter=False,
    min_gap=None,
    take_profit=0.50,
    exit_dte=21,
)


def add_tbill(curve: pd.Series, rf_annual: pd.Series) -> pd.Series:
    """Add rf * elapsed calendar days / 365 to each period's return, rebuild the curve."""
    idx = pd.DatetimeIndex(curve.index)
    rf = rf_annual.reindex(idx, method="ffill").fillna(0.0).clip(lower=0.0).to_numpy()
    days = np.r_[0.0, np.diff(idx.values).astype("timedelta64[D]").astype(float)]
    r = curve.pct_change().fillna(0.0).to_numpy() + rf * days / 365.0
    return pd.Series(curve.iloc[0] * np.cumprod(1.0 + r), index=curve.index)


def row(label, mode, curve, bench, split=None):
    out = {"config": label, "mode": mode}
    for tag, c, b in (("full", curve, bench),) + (
        (("H1", curve[curve.index < split], bench), ("H2", curve[curve.index >= split], bench)) if split else ()
    ):
        m = metrics(c, b)
        out.update({f"{tag}_alpha": m["alpha"], f"{tag}_t": m["t"], f"{tag}_beta": m["beta"]})
        if tag == "full":
            out.update(cagr=m["true_cagr"], max_dd=m["max_dd"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--out", default="docs/backtests/putwrite-yield-2026-11.md")
    args = ap.parse_args()

    rows = []
    configs = [("defaults", DEFAULTS), ("shipped (H1 winner)", OptionPutWriteBot.RULES)]
    for und in ("SPY", "QQQ"):
        synth = load_synthetic_inputs(und, cache_dir=args.cache_dir, start=None, end=None, is_smoke=False)
        split = pd.Timestamp(SYNTHETIC_SPLIT)
        for name, rules in configs:
            r = replace(rules, underlying=und)
            curve, _ = sim_synthetic_putwrite(synth, r)
            curve.index = pd.DatetimeIndex(curve.index)
            for yld in (False, True):
                c = add_tbill(curve, synth["r"]) if yld else curve
                rows.append(
                    row(f"{und} {name}" + (" + T-bill" if yld else ""), "synthetic 2000-26", c, synth["qqq"], split)
                )

        market = CachedSurfaceReplayMarket(
            start=date(2019, 5, 10),
            end=date(2026, 9, 25),
            underlyings=["SPY", "QQQ"],
            cache_dir=args.cache_dir,
            live_expiries=True,
        )
        prices = load_cached_prices({"SPY", "QQQ", "^VIX", "^VIX3M", IRX}, market.days[0], cache_dir=args.cache_dir)
        qqq = prices.upto("QQQ", market.days[-1])["close"]
        irx = prices.upto(IRX, market.days[-1])["close"] / 100.0
        for name, rules in configs:
            r = replace(rules, underlying=und)
            curve, _book = run_strategy(
                market, lambda m, h, r=r, u=und: decide_putwrite(m, h, r, u), prices, fill="quote"
            )
            curve.index = pd.DatetimeIndex(curve.index)
            for yld in (False, True):
                c = add_tbill(curve, irx) if yld else curve
                rows.append(row(f"{und} {name}" + (" + T-bill" if yld else ""), "real surface 2019-26", c, qqq))

    df = pd.DataFrame(rows)
    fmt = df.copy()
    for col in fmt.columns:
        if col.endswith("alpha") or col in ("cagr", "max_dd"):
            fmt[col] = fmt[col].map(lambda v: f"{v:+.1%}" if pd.notna(v) else "")
        elif col.endswith("_t") or col.endswith("beta"):
            fmt[col] = fmt[col].map(lambda v: f"{v:.2f}" if pd.notna(v) else "")
    body = fmt.to_markdown(index=False)
    with open(args.out, "w") as f:
        f.write(
            "# option_PutWriteBot with T-bill yield on collateral (2026-11)\n\n"
            "Pre-declared configurations only (no grid): a-priori defaults and the shipped H1 winner, SPY and QQQ.\n"
            "Interest credited at ^IRX on the whole book. Alpha vs QQQ; the benchmark is not yield-adjusted.\n\n"
            + body
            + "\n"
        )
    print(body)


if __name__ == "__main__":
    main()
