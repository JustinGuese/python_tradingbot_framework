import importlib.util
import io
import os
import sys
import warnings

import pandas as pd
import requests

warnings.filterwarnings("ignore")
spec = importlib.util.spec_from_file_location("fxs", sys.argv[1])
# reuse the first script's functions without re-running its prints
with open(sys.argv[1]) as fh:
    src = fh.read().split('print("--- framework basis')[0]
ns = {}
exec(compile(src, "fxs", "exec"), ns)

run, report, etf, ret = ns["run"], ns["report"], ns["etf"], ns["ret"]
ids = {
    "FXE": "IR3TIB01EZM156N",
    "FXB": "IR3TIB01GBM156N",
    "FXA": "IR3TIB01AUM156N",
    "FXC": "IR3TIB01CAM156N",
    "FXF": "IR3TIB01CHM156N",
    "FXY": "IR3TIB01JPM156N",
    "USD": "IR3TIB01USM156N",
}
cp = "/tmp/fred.pkl"
if os.path.exists(cp):
    R = pd.read_pickle(cp)
else:
    d = {}
    for k, v in ids.items():
        t = requests.get(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={v}", headers={"User-Agent": "Mozilla/5.0"}, timeout=30
        ).text
        df = pd.read_csv(io.StringIO(t))
        s = pd.to_numeric(df[v], errors="coerce")
        s.index = pd.to_datetime(df["observation_date"])
        d[k] = s.dropna()
    R = pd.DataFrame(d).sort_index()
    R.to_pickle(cp)
print(R.tail(2), file=sys.stderr)
Rd = R.reindex(etf.index.union(R.index)).ffill().reindex(etf.index).shift(25)  # ~1 month publication lag
diff = Rd[list(etf.columns)].sub(Rd["USD"], axis=0)
print("--- rank by FRED 3m rate differential (ETF P&L), signed L/S ---")
for k in (1, 2, 3):
    for g in (1.0, 2.0):
        report(f"FRED diff k={k} gross={g} +rf", run(k=k, gross=g, signal=diff, cash_credit=True))
report("FRED diff k=2 gross=1 framework basis (no rf)", run(k=2, signal=diff))
report("FRED diff k=3 gross=1 framework basis (no rf)", run(k=3, signal=diff))
print("--- combine: z(FRED diff)+z(12m total return) ---")
mom = etf.pct_change(252)


def z(df):
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1), axis=0)


report("FRED diff + 12m mom k=2 +rf", run(k=2, signal=z(diff) + z(mom), cash_credit=True))
report("FRED diff + 12m mom k=3 +rf", run(k=3, signal=z(diff) + z(mom), cash_credit=True))
report("12m mom k=2 gross 1.5 +rf", run(k=2, signal=mom, gross=1.5, cash_credit=True))
report("12m mom k=3 +rf", run(k=3, signal=mom, cash_credit=True))
for w in (63, 126, 189, 378):
    report(f"mom {w}d k=2 +rf", run(k=2, signal=etf.pct_change(w), cash_credit=True))
