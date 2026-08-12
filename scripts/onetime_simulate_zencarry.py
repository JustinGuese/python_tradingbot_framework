"""
Simulate the XAUZenCarryBot rule on the parents' RECORDED holdings.

This is not a backtest — no signals are recomputed and no prices are fetched. It
replays the merged rule over what XAUZenbotTreeBot and GoldenButterflyMomBot
actually did, as recorded in portfolio_worth, which is the only honest way to
evaluate a meta-bot whose inputs are two other bots' live decisions.

    return on day t is earned by the position held at the close of day t-1
    -> the merged rule picks its leg from the PREVIOUS day's alpha state

Run against the cluster DB (kubectl port-forward svc/psql-service 15432:5432):

    POSTGRES_URI="postgres:$PW@127.0.0.1:15432/postgres" \
        uv run python scripts/onetime_simulate_zencarry.py

Read-only. Writes nothing.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.utils.db import engine

ALPHA = "XAUZenbotTreeBot"
CARRY = "GoldenButterflyMomBot"
DUST = 1e-6
MAX_GAP_DAYS = 4  # Fri->Mon is 3; anything longer is a recorder outage


def load() -> pd.DataFrame:
    return pd.read_sql(
        "SELECT bot_name, date::date AS d, portfolio_worth, holdings "
        "FROM portfolio_worth WHERE bot_name IN (%(a)s, %(c)s) ORDER BY date",
        engine,
        params={"a": ALPHA, "c": CARRY},
    )


def build_panel(df: pd.DataFrame) -> pd.DataFrame:
    a = df[df.bot_name == ALPHA].set_index("d")
    c = df[df.bot_name == CARRY].set_index("d")
    print(f"{ALPHA}: {len(a)} rows  {a.index.min()} .. {a.index.max()}")
    print(f"{CARRY}: {len(c)} rows  {c.index.min()} .. {c.index.max()}")

    idx = a.index.intersection(c.index)
    panel = pd.DataFrame(
        {
            "aw": a.loc[idx, "portfolio_worth"].astype(float),
            "cw": c.loc[idx, "portfolio_worth"].astype(float),
            "a_in": a.loc[idx, "holdings"].apply(lambda h: float((h or {}).get("^XAU", 0)) > DUST),
        }
    ).sort_index()

    # calculate_portfolio_worth writes 7 days a week, so Sat/Sun rows just carry
    # Friday's valuation forward. Keeping them would inject ~29% zero-return
    # pairs and make every annualised number below wrong.
    days = pd.to_datetime(pd.Series(panel.index, index=panel.index))
    panel = panel[days.dt.dayofweek < 5]

    panel["gap"] = pd.Series(panel.index, index=panel.index).diff().dt.days
    panel["a_ret"] = panel["aw"].pct_change()
    panel["c_ret"] = panel["cw"].pct_change()
    panel["leg_alpha"] = panel["a_in"].shift(1).fillna(False).astype(bool)
    return panel


def stats(rets: pd.Series, label: str) -> None:
    rets = rets.dropna()
    n = len(rets)
    cum = float((1 + rets).prod())
    cagr = cum ** (252 / n) - 1
    vol = float(rets.std()) * np.sqrt(252)
    curve = (1 + rets).cumprod()
    dd = float((curve / curve.cummax() - 1).min())
    sharpe = (float(rets.mean()) * 252) / vol if vol else float("nan")
    calmar = cagr / abs(dd) if dd else float("nan")
    print(
        f"{label:14s} n={n:4d}  cum={cum - 1:+7.2%}  CAGR={cagr:+7.2%}  "
        f"vol={vol:6.2%}  maxDD={dd:+7.2%}  Sharpe={sharpe:5.2f}  Calmar={calmar:5.2f}"
    )


def main() -> None:
    panel = build_panel(load())
    usable = panel[(panel["gap"] <= MAX_GAP_DAYS) & panel["a_ret"].notna()].copy()
    dropped = max(0, len(panel) - 1 - len(usable))
    usable["m_ret"] = np.where(usable["leg_alpha"], usable["a_ret"], usable["c_ret"])

    print(f"\nusable weekday pairs: {len(usable)}  (dropped {dropped} spanning >{MAX_GAP_DAYS}d recorder gaps)")
    print(f"alpha leg live on {usable['leg_alpha'].mean():.1%} of them ({int(usable['leg_alpha'].sum())} days)\n")

    stats(usable["a_ret"], "XAUZen alone")
    stats(usable["c_ret"], "GoldenBfly")
    stats(usable["m_ret"], "ZenCarry")

    alpha_days = usable[usable["leg_alpha"]]
    carry_days = usable[~usable["leg_alpha"]]
    print(f"\nalpha-leg days: {float((1 + alpha_days['a_ret']).prod()) - 1:+.2%} over {len(alpha_days)} days")
    print(f"carry-leg days: {float((1 + carry_days['c_ret']).prod()) - 1:+.2%} over {len(carry_days)} days")
    print(
        "\nNOTE: the alpha leg's entire contribution comes from a handful of "
        "days. Treat every annualised figure above as a description of this "
        "window, not an estimate of anything."
    )


if __name__ == "__main__":
    main()
