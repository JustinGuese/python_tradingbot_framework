# Follow-up to event-macro-2026-11.md. Run: PYTHONPATH=. python <this file> (pool needs a port-forward to the prod DB).
import pickle, numpy as np, pandas as pd, os, dotenv
dotenv.load_dotenv('.env.secret')
from tradingbot.utils.macro_calendar import FOMC_STATEMENT_DATES, macro_events_frame
from tradingbot.utils.alpha_report import alpha_stats
d=pickle.load(open('/tmp/event_macro_prices.pkl','rb')); px=pd.DataFrame(d).sort_index(); r=px.pct_change(); idx=px.index
f=macro_events_frame(("FOMC","CPI"))
print(f.head(2), f.columns.tolist())
col=[c for c in f.columns if 'date' in c.lower()][0]; kc=[c for c in f.columns if 'kind' in c.lower()][0]
ev={k:[pd.Timestamp(x) for x in g[col]] for k,g in f.groupby(kc)}
cost=float(os.environ.get("COST",0.0005))  # per side
def sleeve(events,asset,a,b):
    s=pd.Series(0.0,index=idx); n=0
    for e in events:
        i=idx.searchsorted(e)
        if i+b>=len(idx) or i+a<0: continue
        seg=r[asset].iloc[i+a+1:i+b+1]
        if seg.isna().any(): continue
        s.iloc[i+a+1:i+b+1]=seg.values
        s.iloc[i+a+1]-=cost; s.iloc[i+b]-=cost; n+=1
    return s
legs={"FOMC_UUP02":sleeve(ev["FOMC"],"UUP",0,2),"CPI_TLT01":sleeve(ev["CPI"],"TLT",0,1),
      "FOMC_TLT_pre":sleeve(ev["FOMC"],"TLT",-1,0),"FOMC_GLD_pre":sleeve(ev["FOMC"],"GLD",-1,0)}
qqq=r["QQQ"]
def rep(name,s,start="2007-03-01"):
    s=s[start:]; q=qqq[start:]
    m=s.index.dayofweek<5
    st=alpha_stats(s[m].fillna(0),q[m].fillna(0))
    h=len(s)//2
    a1=alpha_stats(s[:h],q[:h]); a2=alpha_stats(s[h:],q[h:])
    print(f"{name:14s} alpha {st.alpha*100:5.2f}%/yr t {st.t:5.2f} beta {st.beta:5.2f} | H1 {a1.alpha*100:5.2f}% ({a1.t:.2f}) H2 {a2.alpha*100:5.2f}% ({a2.t:.2f})")
for k,v in legs.items(): rep(k,v)
rep("pool_UUP+CPI",legs["FOMC_UUP02"]+legs["CPI_TLT01"])
rep("pool_all4",sum(legs.values()))
