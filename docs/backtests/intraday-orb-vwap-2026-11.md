# Intraday QQQ: VWAP pullback and opening-range breakout (2026-11)

Two bots from a pair of reels: `VWAPPullbackBot` and `OpenRangeBreakoutBot`. Both are
long-only QQQ on 5-minute bars, flat every night, one trade per session, paper only.
The rules are pure functions (`utils/vwap_pullback_rules.py`, `utils/orb_rules.py`) that the
live bots replay each run and the backtest drives session by session. They are not copied
from the reels' own results, which were on Nasdaq futures.

## Verdict: unproven, and costs decide it

- **Sample:** 60 sessions of yfinance 5m bars (the 60-day cap), 29 and 35 trades. The
  t-statistics below are far from the t >= 2 bar in either direction.
- **VWAP pullback:** positive expectancy in R (+0.18R), but 0.05%/side slippage
  (the framework's paper cost) turns the alpha negative (t -1.99). Beats 92% of matched-risk
  random entries, which is encouraging and also weak at n = 29. The first half lost and the
  second half won, so there is no stable edge yet.
- **ORB:** negative expectancy even before costs (-0.09R); with costs, alpha -21%/yr at
  t -3.21. By the CLAUDE.md rule (t <= -2) that is a pause candidate. It beats 0% of
  random entries. The reel's "wins 91%, small target" shape did not reproduce at 5m: the
  win rate here is 63%, and the average loss is 2.6x the average win.
- **Not tuned.** Defaults only: a walk-forward re-tune on 60 sessions would be noise. The
  live bots save their 5m bars to `historic_data`; rerun with `--from-db` once there are
  months of them.
- **Costs matter more than the signal.** 5bp/side is conservative for QQQ (the real
  spread is ~1bp), so rerun with a lower `SLIPPAGE` before drawing conclusions about the
  VWAP bot.

## Backtest output

`PYTHONPATH=. uv run python scripts/onetime_intraday_orb_vwap_backtest.py`, run 2026-11-02:

```
### VWAPPullbackBot defaults  (60 sessions)
- no costs / all: 29 trades, win 45%, avg win +1.61R, avg loss -0.98R, expectancy +0.181R, return +0.03%, alpha +0.1%/yr t 0.02 beta 0.03 corr 0.16, maxDD -1.27%
- no costs / first half: 16 trades, win 31%, avg win +1.70R, avg loss -0.97R, expectancy -0.138R, return -0.93%, alpha -9.3%/yr t -1.22 beta -0.04 corr -0.22, maxDD -1.27%
- no costs / second half: 13 trades, win 62%, avg win +1.56R, avg loss -1.00R, expectancy +0.573R, return +0.98%, alpha +10.1%/yr t 1.31 beta 0.05 corr 0.40, maxDD -0.58%
- slippage 0.05%/side / all: 29 trades, win 45%, avg win +1.61R, avg loss -0.98R, expectancy +0.181R, return -2.83%, alpha -11.9%/yr t -1.99 beta 0.02 corr 0.14, maxDD -2.85%
- slippage 0.05%/side / first half: 16 trades, win 31%, avg win +1.70R, avg loss -0.97R, expectancy -0.138R, return -2.51%, alpha -22.3%/yr t -2.67 beta -0.06 corr -0.26, maxDD -2.63%
- slippage 0.05%/side / second half: 13 trades, win 62%, avg win +1.56R, avg loss -1.00R, expectancy +0.573R, return -0.33%, alpha -0.8%/yr t -0.11 beta 0.05 corr 0.42, maxDD -0.68%
- random-entry control (100 draws, matched risk): strategy total R beats 92% of them

### OpenRangeBreakoutBot defaults  (60 sessions)
- no costs / all: 35 trades, win 63%, avg win +0.27R, avg loss -0.71R, expectancy -0.094R, return -1.61%, alpha -6.3%/yr t -1.01 beta 0.03 corr 0.20, maxDD -2.03%
- no costs / first half: 16 trades, win 56%, avg win +0.30R, avg loss -0.72R, expectancy -0.146R, return -0.98%, alpha -8.6%/yr t -1.02 beta -0.06 corr -0.28, maxDD -1.08%
- no costs / second half: 19 trades, win 68%, avg win +0.25R, avg loss -0.70R, expectancy -0.050R, return -0.64%, alpha -2.9%/yr t -0.36 beta 0.07 corr 0.48, maxDD -1.22%
- slippage 0.05%/side / all: 35 trades, win 63%, avg win +0.27R, avg loss -0.71R, expectancy -0.094R, return -5.00%, alpha -21.4%/yr t -3.21 beta 0.02 corr 0.12, maxDD -5.00%
- slippage 0.05%/side / first half: 16 trades, win 56%, avg win +0.30R, avg loss -0.72R, expectancy -0.146R, return -2.55%, alpha -22.5%/yr t -2.51 beta -0.08 corr -0.33, maxDD -2.55%
- slippage 0.05%/side / second half: 19 trades, win 68%, avg win +0.25R, avg loss -0.70R, expectancy -0.050R, return -2.51%, alpha -19.3%/yr t -2.15 beta 0.06 corr 0.38, maxDD -2.57%
- random-entry control (100 draws, matched risk): strategy total R beats 0% of them
```

## Walk-forward tuning, 2026-11-02: nothing to ship

`scripts/onetime_intraday_tune.py`: the same rules on 12 liquid tickers (QQQ, SPY, IWM, DIA, XLK,
SMH, AAPL, MSFT, NVDA, AMZN, GOOGL, META) over the same 60 sessions, which is more trades but
highly correlated ones. Cells are ranked on the first 30 sessions at 1bp/side, then judged on the
last 30. Mean net return per trade, in bps:

| | Defaults, 1bp | Defaults, 5bp | Cells net-positive at 1bp (H1 / H2) | Mean H2 of the H1-top-10 |
|---|---|---|---|---|
| ORB (162 cells) | −2.4 / −5.2 | −10 / −13 | 9% / 6% | −2.9 |
| VWAP pullback (192 cells) | −3.3 / +5.0 | −11 / −3 | 24% / 61% | +0.6 |

- **ORB:** no parameter set is positive out of sample, and none at 5bp in either half. The cells that
  looked best in H1 lost in H2. There is no edge to tune.
- **VWAP pullback:** the H1-top-10 average +0.6 bps out of sample, no better than the grid average
  (+0.4). A positive H2 at 1bp is the market in that half, not the parameters. It is negative at 5bp.
- **Shipped defaults are unchanged.** A re-tune would not pass the repo's own rule (new parameters ship
  only if they beat the defaults out of sample).
- **What would change this:** years of 5m bars (the 60-day yfinance cap is the limit), or a
  structural change to the rules rather than a parameter move. See the chat summary for candidates.
