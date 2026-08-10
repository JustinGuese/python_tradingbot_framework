"""
Build the NilssonHedge submission package for AdaptiveMeanReversionBot
from the REAL live track record stored in the `portfolio_worth` table
(namespace tradingbots-2025, db `postgres`).

Inputs (already dumped from the DB):
    raw/strategy.csv   -> AdaptiveMeanReversionBot daily portfolio_worth
    raw/benchmark.csv  -> Benchmark_QQQ daily portfolio_worth (same window)

Outputs:
    AdaptiveMeanReversionBot_monthly_returns.csv
    AdaptiveMeanReversionBot_monthly_returns.xlsx
    AdaptiveMeanReversionBot_equity_curve.png
    AdaptiveMeanReversionBot_factsheet.pdf
    summary_stats.csv
"""

import math
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
INCEPTION = "2026-03-22"

def load(name):
    df = pd.read_csv(os.path.join(HERE, "raw", name), header=None,
                     names=["date", "worth"])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["worth"].astype(float).sort_index()

strat = load("strategy.csv")
bench_raw = load("benchmark.csv")
# Rebase benchmark to the strategy's starting notional at inception for a like-for-like compare
bench = bench_raw / bench_raw.iloc[0] * strat.iloc[0]

# ----------------------------------------------------------------------
# Monthly returns (month-end to month-end; first month measured from inception)
# ----------------------------------------------------------------------
def monthly_returns(series):
    me = series.resample("ME").last()
    # prepend inception value so the first month's return is measured from day 1
    incp = pd.Timestamp(INCEPTION)
    start = pd.Series([series.iloc[0]], index=[incp - pd.offsets.MonthEnd(1)])
    me = pd.concat([start, me])
    return me.pct_change().dropna() * 100

strat_m = monthly_returns(strat)
bench_m = monthly_returns(bench)
monthly = pd.DataFrame({"Strategy_%": strat_m, "Benchmark_QQQ_%": bench_m})
monthly.index = monthly.index.strftime("%Y-%m")
monthly.index.name = "Month"

# CSV (long form)
monthly.round(4).to_csv(os.path.join(HERE, "AdaptiveMeanReversionBot_monthly_returns.csv"))

# ----------------------------------------------------------------------
# Year x month matrix (NilssonHedge style) with YTD compounded
# ----------------------------------------------------------------------
def matrix(series_pct):
    s = series_pct.copy()
    s.index = pd.to_datetime(s.index + "-01")
    df = pd.DataFrame({"r": s.values}, index=s.index)
    df["Year"] = df.index.year
    df["Mon"] = df.index.strftime("%b")
    order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    piv = df.pivot_table(index="Year", columns="Mon", values="r", aggfunc="first")
    piv = piv.reindex(columns=[m for m in order if m in piv.columns])
    ytd = df.groupby("Year")["r"].apply(lambda x: (np.prod(1 + x / 100) - 1) * 100)
    piv["YTD"] = ytd
    return piv.round(2)

xlsx = os.path.join(HERE, "AdaptiveMeanReversionBot_monthly_returns.xlsx")
with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
    m_strat = pd.Series(strat_m.values, index=monthly.index)
    m_bench = pd.Series(bench_m.values, index=monthly.index)
    matrix(m_strat).to_excel(xw, sheet_name="Strategy monthly %")
    matrix(m_bench).to_excel(xw, sheet_name="Benchmark QQQ %")
    monthly.round(4).to_excel(xw, sheet_name="Long form")

# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------
r = strat_m / 100
days = (strat.index[-1] - pd.Timestamp(INCEPTION)).days
years = days / 365.25
total_ret = strat.iloc[-1] / strat.iloc[0] - 1
cagr = (1 + total_ret) ** (1 / years) - 1
ann_vol = r.std(ddof=1) * math.sqrt(12) if len(r) > 1 else 0.0
sharpe = (r.mean() * 12) / ann_vol if ann_vol else 0.0
dr = r[r < 0]
sortino = (r.mean() * 12) / (dr.std(ddof=1) * math.sqrt(12)) if len(dr) > 1 and dr.std(ddof=1) else 0.0
run_max = strat.cummax()
mdd = ((strat - run_max) / run_max).min()
calmar = cagr / abs(mdd) if mdd else 0.0
b_total = bench.iloc[-1] / bench.iloc[0] - 1

stats = {
    "Strategy": "AdaptiveMeanReversionBot",
    "Instrument": "QQQ (Nasdaq-100 ETF)",
    "Type": "Systematic long / cash, daily, trend-following",
    "Track record": "Live (paper book, $10,000 notional)",
    "Inception": INCEPTION,
    "As of": strat.index[-1].date().isoformat(),
    "Months live": len(strat_m),
    "Starting notional (USD)": round(strat.iloc[0], 2),
    "Current value / AUM (USD)": round(strat.iloc[-1], 2),
    "Total Return %": round(total_ret * 100, 2),
    "CAGR (annualized) %": round(cagr * 100, 2),
    "Ann. Volatility %": round(ann_vol * 100, 2),
    "Sharpe (rf=0)": round(sharpe, 2),
    "Sortino": round(sortino, 2),
    "Max Drawdown %": round(mdd * 100, 2),
    "Calmar": round(calmar, 2),
    "Best Month %": round(r.max() * 100, 2),
    "Worst Month %": round(r.min() * 100, 2),
    "QQQ B&H over window %": round(b_total * 100, 2),
    "Trades to date": 1,
    "Current exposure": "100% long QQQ",
}
pd.Series(stats).to_csv(os.path.join(HERE, "summary_stats.csv"), header=False)
print("=== SUMMARY ===")
for k, v in stats.items():
    print(f"{k:28s} {v}")
print("\n=== MONTHLY RETURNS ===")
print(monthly.round(2).to_string())

# ----------------------------------------------------------------------
# Equity curve chart
# ----------------------------------------------------------------------
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(9, 4.2))
ax.plot(strat.index, strat.values, lw=1.8, color="#1a5cff",
        label="AdaptiveMeanReversionBot")
ax.plot(bench.index, bench.values, lw=1.3, color="#888", alpha=0.8,
        label="QQQ buy & hold (rebased)")
ax.set_title("AdaptiveMeanReversionBot — live equity vs QQQ", fontsize=11)
ax.set_ylabel("Portfolio value (USD)")
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)
fig.autofmt_xdate()
fig.tight_layout()
chart = os.path.join(HERE, "AdaptiveMeanReversionBot_equity_curve.png")
fig.savefig(chart, dpi=140)

# ----------------------------------------------------------------------
# One-page PDF factsheet
# ----------------------------------------------------------------------
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.gridspec import GridSpec

pdf_path = os.path.join(HERE, "AdaptiveMeanReversionBot_factsheet.pdf")
with PdfPages(pdf_path) as pdf:
    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait
    gs = GridSpec(5, 2, figure=fig, height_ratios=[0.7, 1.4, 1.95, 1.85, 0.45],
                  hspace=0.7, wspace=0.15)

    # Header
    axh = fig.add_subplot(gs[0, :]); axh.axis("off")
    axh.text(0, 0.75, "AdaptiveMeanReversionBot", fontsize=20, fontweight="bold")
    axh.text(0, 0.42, "Systematic trend-following strategy on QQQ (Nasdaq-100)",
             fontsize=11, color="#333")
    axh.text(0, 0.12, f"Live track record since {INCEPTION}  •  as of "
             f"{strat.index[-1].date().isoformat()}  •  Manager: Justin Guese", fontsize=9, color="#666")

    # Equity curve
    axe = fig.add_subplot(gs[1, :])
    axe.plot(strat.index, strat.values, lw=1.8, color="#1a5cff", label="Strategy")
    axe.plot(bench.index, bench.values, lw=1.2, color="#999", alpha=0.8,
             label="QQQ buy & hold")
    axe.set_title("Growth of $10,000 (live)", fontsize=10)
    axe.legend(fontsize=8); axe.grid(True, alpha=0.3)
    axe.tick_params(labelsize=8)
    for lbl in axe.get_xticklabels():
        lbl.set_rotation(30); lbl.set_ha("right")

    # Key stats table
    axk = fig.add_subplot(gs[2, 0]); axk.axis("off")
    key_rows = [
        ("Return since inception", f"{total_ret*100:+.2f}%"),
        ("  (over ~3.5 months, net)", ""),
        ("Ann. volatility", f"{ann_vol*100:.2f}%"),
        ("Sharpe (rf=0)", f"{sharpe:.2f}"),
        ("Max drawdown", f"{mdd*100:.2f}%"),
        ("Best / worst month", f"{r.max()*100:+.1f}% / {r.min()*100:+.1f}%"),
        ("Starting notional", f"${strat.iloc[0]:,.0f}"),
        ("Current value (AUM)", f"${strat.iloc[-1]:,.0f}"),
        ("QQQ over same window", f"{b_total*100:+.2f}%"),
        ("Annualized*", f"{cagr*100:+.1f}%"),
    ]
    tbl = axk.table(cellText=key_rows, colLabels=["Metric", "Value"],
                    cellLoc="left", loc="upper left",
                    colWidths=[0.62, 0.38])
    tbl.auto_set_font_size(False); tbl.set_fontsize(8.5); tbl.scale(1, 1.15)
    for (rr, cc), cell in tbl.get_celld().items():
        if rr == 0:
            cell.set_facecolor("#1a5cff"); cell.set_text_props(color="white", fontweight="bold")

    # Monthly returns table
    axm = fig.add_subplot(gs[2, 1]); axm.axis("off")
    mr = monthly.round(2).reset_index()
    mtbl = axm.table(cellText=mr.values,
                     colLabels=["Month", "Strat %", "QQQ %"],
                     cellLoc="center", loc="upper left")
    mtbl.auto_set_font_size(False); mtbl.set_fontsize(8.5); mtbl.scale(1, 1.15)
    for (rr, cc), cell in mtbl.get_celld().items():
        if rr == 0:
            cell.set_facecolor("#1a5cff"); cell.set_text_props(color="white", fontweight="bold")

    # Strategy description
    axd = fig.add_subplot(gs[3, :]); axd.axis("off")
    desc = (
        "Strategy\n"
        "AdaptiveMeanReversionBot is a fully systematic, rules-based strategy trading a single\n"
        "instrument — the QQQ Nasdaq-100 ETF. It is long whenever the 200-day trend is intact and\n"
        "volatility is calm (price > 200-day SMA and ATR below its 20-day average), and moves to cash\n"
        "only on a confirmed trend breakdown (price falls > 3% below the 200-day SMA). The buffered exit\n"
        "keeps the book invested through ordinary corrections and steps aside in sustained downtrends.\n"
        "It holds no leverage, no shorts and no overnight derivatives — exposure is either 100% QQQ or cash.\n\n"
        "Execution\n"
        "Signals are evaluated daily near the NYSE close and executed via Interactive Brokers. Fills in the\n"
        "track record include 0.05% one-way slippage. The book runs on a $10,000 notional; returns are\n"
        "net of modelled trading costs.\n\n"
        "Note on window\n"
        "Since inception the strategy spent its first ~2.5 weeks in cash before its first long entry (9 Apr 2026),\n"
        "which explains the gap to QQQ buy-and-hold over this specific bull-market window."
    )
    axd.text(0, 1.0, desc, fontsize=8.2, va="top", family="sans-serif", linespacing=1.35)

    # Footer / disclaimer
    axf = fig.add_subplot(gs[4, :]); axf.axis("off")
    axf.text(0, 0.5,
             "Contact: Justin Guese  •  guese.justin@gmail.com\n"
             "*Annualized figure extrapolates a ~3.5-month live history and will be volatile until a longer record accrues.\n"
             "Disclaimer: Live track record of a systematic paper book ($10,000 notional). Past performance is not\n"
             "indicative of future results. For manager research only; not an offer of securities or investment advice.",
             fontsize=7, color="#666", va="center", linespacing=1.4)

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)

print("\nWrote:")
for f in sorted(os.listdir(HERE)):
    if not f.startswith("raw") and f != "build_package.py":
        print("  ", f)
