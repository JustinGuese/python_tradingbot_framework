The decisive finding
Gold went nowhere over this period (GLD −0.85%). So XAUZenbotTreeBot's 16.7% CAGR is not gold beta — its beta to gold is 0.14. Its alpha is positive against every benchmark I can construct:

XAUZenbotTreeBot vs corr beta alpha (ann.) t
GLD 0.28 0.14 +17.0% 0.99
Gold futures 0.14 0.06 +16.9% 0.96
FTWD −0.11 −0.14 +20.2% 1.14
QQQ 0.06 0.05 +15.5% 0.87
SPY 0.13 0.15 +14.3% 0.81
Positive against all five, in a flat gold market, with near-zero beta to everything. That's the only thing in the fleet that looks like genuine skill rather than repackaged index exposure.

Ranking by potential

1. XAUZenbotTreeBot — clear first. Everything above, plus max DD −4.5% (Calmar 3.70), 20 real trades, and the best recent form in the fleet (+7.5pp vs QQQ over the last 60 days). The scaling headroom is the key part: it's out of the market 92% of days, so you can increase size or exposure without touching the signal. Caveat: t ≈ 1.0 over 140 days — it needs roughly 2–3× more data to reach significance.

2. KronosTraderBot — the best risk container. 729 trades in just 76 days, max DD −1.61%, vol 5.2%, beta 0.10–0.21. Alpha is ~0, so there's no edge yet. But two things make it interesting: it's the only genuinely active bot that's positive in its second half against both QQQ and SPY, and its risk profile means that if alpha does turn positive you could run it at 3–4× and still have a smaller drawdown than SPY. It's also the youngest — give it runway.

3. EarningsInsiderTiltBot — the best candidate for iteration. 1,179 trades, 0% flat days (real continuous exposure), Sortino 1.66, max DD −4.84%, Calmar 3.90, highest daily win rate in the fleet (45.9%). Alpha is −1.0% vs SPY with t = −0.07, i.e. indistinguishable from beta — not losing, just not winning. Its value is breadth: with that many trades, alpha estimates converge fastest and small per-trade improvements compound. If you want something to actively work on, this one gives you the most signal per week of effort.

Watch but don't scale: TARegimeAdaptiveBot. It has the highest positive t-stat in the fleet (1.70, +15.2% vs QQQ) — closest thing to significance you have. But that's 4 trades. Four bets. It's either real regime timing or luck, and nothing in the data can separate those yet.

Kill list. EURUSDTreeBot is now clearly dead — it has negative alpha against EURUSD itself (t = −1.26), so it's not an FX edge, it's just losing. Same for SynthesizedHyperConvexityBot (−26%), GptBasedStrategyBTCTabased (−28%, frozen since April), DeepSeekToolBot (−14%), SqueezeMomentumBot. Also note XAUSyntheticMetalTreeBot is not a second XAUZenbot — its gold alpha is only +5.8% (t 0.24) and it's negative vs SPY.

Two things that would actually raise your confidence
The reason everything reads n.s. is sample size — 76 to 141 days. The fleet has been dead for 5 weeks, which is 5 weeks of statistical power you didn't accumulate. Restarting it is the single highest-value action for answering this question properly, and that needs the numpy commit pushed.

Second: the site only benchmarks against SPY/QQQ/FTWD, which is why the gold test above isn't visible anywhere in your UI. I'd add GLD and BTC-USD as benchmark series in portfolio_worth so the generator measures asset-class alpha the same way I just did by hand. Want me to wire that in?
