# Option bots, round 3: vol data, surface analytics, gates, four new bots (2026-09-28)

This round built the missing pieces for option trading, except historical
option data (handled separately) and a real broker:
- vol-index and macro-event data;
- a daily vol-surface summary with options "TA";
- stress and early-assignment risk;
- better volatility forecasts, SVI fitting and hedging bands;
- four new paper bots;
- a replay harness for real chains.

Everything that could be tested walk-forward was, with the usual protocol:
choose on H1, judge on H2, alpha t against QQQ, and ship only if the change
beats the live rules out of sample.

**The short version:**
- **Nothing added to `option_IndexVolBot` beat its live rules out of sample.**
  That covers the term-structure, VVIX and FOMC gates, the unwind, the
  Yang-Zhang and GARCH forecasts, the z-scored signal and the tail hedge. All
  stay off. The closest miss was a VIX/VIX3M ≤ 1.0 entry gate (details below).
- **The Whalley-Wilmott hedging band ships** on `option_MispricingBot`'s
  straddle hedge. It is a small improvement.
- **The four new bots are live-only and unproven.** Their evidence has to come
  from the paper record and, later, the replay harness.

## What was built

| Piece | Where | What it gives |
|---|---|---|
| Vol indices | `utils/vol_indices.py` | ^VIX9D/VIX/VIX3M/VIX6M/VVIX/SKEW/VXN levels, term ratio, cached history |
| Macro calendar | `utils/macro_calendar.py`, `macro_events`, `macrocalendarsnapshot` (weekly) | FOMC statement days (static, from federalreserve.gov, 2006–2027) + CPI and jobs-report dates from FRED |
| Vol surface | `utils/vol_surface.py`, `vol_surface`, written after each capture | Constant-maturity ATM IV (7–180d); 25Δ put/call IV, RR25 and fly; term slope; HAR fair vol and VRP; put/call ratios; GEX; max pain; unusual activity |
| Implied correlation | `implied_correlation`, same job | SPY vs the 50 captured names, market-cap weighted |
| Stress and risk | `option_math.stress_pnl`, `options.stress_book`, `utils/option_risk.py`, `option_risk`, `optionrisksnapshot` (daily) | Per bot and underlying: dollar greeks, P&L under spot/vol shocks and a crash (−20%, +30 vol points), max loss, margin |
| Early assignment | `PortfolioManager.assign_before_dividends`, `option_rules.ex_dividend_close_reason`, `Bot.dividend_threatened_calls` | See the list below |
| Estimators | `utils/vol_estimators.py` | Parkinson, Garman-Klass (the simple 0.5 form), Rogers-Satchell, Yang-Zhang; HAR on any daily-variance series; GARCH(1,1); VRP z-score; Whalley-Wilmott band |
| SVI | `utils/svi.py`, `options.svi_surface_fit` | Raw SVI on total variance; Gatheral's butterfly check g(k) ≥ 0 and the calendar check; outliers beyond half the spread in vol terms |
| Parity filter | `options.parity_violations` | American bounds S − D − K ≤ C − P ≤ S − Ke^(−rT) at bid/ask. Flagged pairs are dropped as bad data, never traded |
| Scan | `utils/mispricing_scan.py`, `option_mispricing_scan` | Daily ranked list per name: vrp z-score, SVI outliers, parity flags |
| Replay | `utils/option_replay.py`, `scripts/onetime_option_replay_backtest.py` | Backtests over stored chains: fills at recorded bid/ask, settles off the recorded underlying price |

Early-assignment handling:
- A short ITM call whose time value is below the next dividend is assigned
  early on the last session before the ex-date, as a broker would.
- Physical bots deliver the shares; cash bots settle at intrinsic value.
- The wheel, PMCC, collar, cross-vol and scanner bots buy such a call back two
  sessions before the ex-date.

## Straddle hedge: fixed band vs Whalley-Wilmott

This test uses `option_MispricingBot`'s live rules on the synthetic AAPL
harness (`scripts/onetime_option_bots_backtest.py --hedge`). Only its cheap
side hedges, so the effect is small.

| Hedge | Full t | H1 t | H2 t | Full max DD |
|---|---|---|---|---|
| Fixed: rehedge to 0 once \|Δ\|·S > 2% of the book | 0.93 | 0.56 | 0.82 | −43.8% |
| **WW, risk aversion 1e-4 (H1 pick)** | **1.03** | **0.64** | **0.88** | −44.1% |
| WW 1e-3 | 0.99 | 0.61 | 0.85 | −44.2% |
| WW 1e-2 / 0.1 / 1 | 0.96 / 0.96 / 0.95 | 0.58 / 0.58 / 0.57 | 0.85 / 0.84 / 0.84 | −43.8% |

The H1 pick also wins on H2, so it ships (`hedge_ww_risk_aversion=1e-4`).
The mechanism is fewer and smaller hedge trades: rehedge only outside
H = (1.5·λ·S·Γ²/γ)^(1/3), and only back to the band's edge. The gain is
0.06 of t, which is worth having but is not an edge in itself.

## option_IndexVolBot: new gates, walk-forward

`scripts/onetime_index_vol_backtest.py --gates` ran two grids on top of the
live rules (0.10Δ, 10% wings, 35 DTE, gap ≥ 3 points):
- **Grid A**, the vol-curve gates, runs from 2007 because ^VIX3M and ^VVIX
  start there. Its split is 2016-11.
- **Grid B**, the forecast, signal and tail hedge, runs from 2000 with the
  split at 2013-06.

The event gate was FOMC-only for this run, because no `FRED_API_KEY` was
available. CPI days are added automatically once the key exists.

**Grid A** has 108 variants: VIX/VIX3M cap × VVIX cap × FOMC blackout ×
unwind. The H1 winner (VIX/VIX3M ≤ 0.95 + VVIX ≤ 110 + 1-day blackout) traded
20 times in H1 at t 4.11, then **made t 0.01 on H2**. It had filtered itself
down to almost no trades. Only 2% of the grid beat the live rules on H2.

**Grid B** has 45 variants: fair model × signal × tail hedge. The H1 winner
(z ≥ 2 on a GARCH forecast, no gap floor) **made t 0.56 on H2** against the
live 2.64. 9% of the grid beat the live rules on H2.

Every lever on its own, against the live rules:

| Variant | Full t | H1 t | H2 t | Full max DD | Trades |
|---|---|---|---|---|---|
| **Live (gap ≥ 3 pts, HAR), 2000–2026** | 4.28 | 3.45 | 2.64 | −6.4% | 103 |
| z ≥ 1.0 only | 1.72 | 2.63 | −0.31 | −20.1% | 173 |
| z ≥ 1.5 only | 2.38 | 2.03 | 1.02 | −11.7% | 119 |
| z ≥ 2.0 only | 1.82 | 1.30 | 0.98 | −7.7% | 73 |
| gap ≥ 3 and z ≥ 1 | 2.89 | 1.87 | 2.35 | −6.3% | 78 |
| gap ≥ 3, Yang-Zhang HAR | 3.31 | 2.26 | 2.36 | −8.0% | 89 |
| gap ≥ 3, GARCH(1,1) | 3.20 | 2.07 | 2.42 | −8.2% | 147 |
| tail hedge 0.25%/month (60-DTE 10Δ puts) | 3.93 | 3.16 | 2.65 | −8.2% | 103 |
| tail hedge 0.5%/month | 2.83 | 2.14 | 2.20 | −15.7% | 103 |
| **Live, 2007–2026 window** | 3.35 | 1.67 | 3.46 | −6.3% | 80 |
| VIX/VIX3M ≤ 1.0 at entry | **3.87** | **2.84** | 2.65 | **−2.7%** | 56 |
| VIX/VIX3M ≤ 0.95 | 1.93 | 0.89 | 1.88 | −6.0% | 40 |
| VVIX ≤ 130 | 3.07 | 1.59 | 3.15 | −6.3% | 70 |
| FOMC blackout, 1 session | 3.26 | 1.57 | 3.45 | −6.4% | 78 |
| unwind at VIX/VIX3M > 1.0 | 1.92 | 0.86 | 1.94 | −6.3% | 117 |
| unwind at > 1.05 | 3.18 | 2.29 | 2.22 | −5.8% | 95 |

What this says:

- **The z-scored signal is worse than the raw 3-point gap on SPY.** This is
  the opposite of the textbook argument ("IV always sits above RV, so z-score
  the spread"). The live rule already answers that argument: it demands a
  level, IV at least 3 points above the forecast, rather than "IV > forecast".
  The z-score instead fires whenever the gap is unusual for the past year,
  which in a calm year includes small absolute gaps that carry no premium.
  - This matters for `option_MispricingScanBot`, which was specified around
    the z-score. It ships as specified, and this table is its prior.
- **Yang-Zhang and GARCH do not beat close-to-close HAR here.** HAR is already
  a good forecaster of index vol. Range data adds precision to measuring
  today's vol, not to forecasting next month's.
- **A VIX/VIX3M ≤ 1.0 entry gate is the closest miss.** On the 2007–2026
  window it halves the drawdown (−2.7% vs −6.3%) and lifts full-window and H1
  t. It loses on H2 (2.65 vs 3.46), so under the protocol it does not ship.
  - It is the first thing to revisit when the live record exists.
  - It is also the natural candidate if drawdown ever matters more than t.
- **Unwinding on inversion hurts.** It closes condors at the worst marks, just
  before vol mean-reverts.
- **The tail hedge buys negative correlation at the cost of drawdown in
  calm years.** At 0.25%/month the H2 t is flat, but the max DD is worse.
- **The FOMC blackout changes almost nothing.** A 35-DTE condor holds through
  one or two meetings whatever day it opens.

## The new bots

All four are paper, $100k, and in values.yaml unsuspended. None can be
backtested yet, because per-name option history does not exist. Their record
starts now.

| Bot | Trades | Why it might work | What would sink it |
|---|---|---|---|
| `option_CrossVolBot` (15:50) | 10Δ condors on up to 5 of the 50 names furthest above their Yang-Zhang HAR forecast (gap ≥ 5 pts, no earnings before expiry) | The AAPL mispricing bot found the gap real but lost it to single-stock jumps. Five names at 4% risk each diversify the jumps | Single-name spreads (far wider than SPY's); correlated sell-offs hit every name at once |
| `option_MispricingScanBot` (16:00) | Both directions at \|z\| ≥ 2 of the name's own gap. Rich: 16Δ condor, SVI-checked strikes. Cheap: straddle hedged in a WW band. Vega budget 1% of equity per vol point; crash cap 15% | The user's specification: a forward forecast, the VRP z-score, surface no-arbitrage filters, parity as a data filter, vega sizing, a term-structure unwind | On SPY the z-score signal loses to the raw gap (table above). Single names only score after 60 days of their own surface |
| `option_EarningsCrushBot` (14:30 and 19:30) | ATM iron butterflies, wings at 1.5 implied moves, entered the session before a report whose two-expiry implied move is ≥ 1.25× its historical RMS move; closed after the open | Large caps price earnings moves above what they deliver, on average | The average hides fat tails: the one report that moves 3× costs several wins |
| `option_DispersionBot` (15:55) | Short SPY iron fly vs long straddles on the 10 largest names, vega-weighted N_i = \|V_I\|·w_i/V_i, when implied correlation is ≥ its 80th percentile | Rich implied correlation mean-reverts; the long straddles pay when members move apart | Correlation → 1 in a crash; 11 structures of rounding error on $100k. Trades nothing until 60 days of history exist |

## What still limits all of this

- **No per-name option history**, until the capture accumulates or history
  is imported. The replay harness is ready for it: it only needs the rows in
  `option_quotes`.
- **The SPY skew is calibrated on one day** (see
  [index-vol-2026-09.md](index-vol-2026-09.md)). `vol_surface.rr25_30` now
  records it daily, so it can be recalibrated against months of real data.
- **CPI dates need `FRED_API_KEY`** in the cluster secret. Without it,
  `macrocalendarsnapshot` exits 1 and the calendar is FOMC-only.

## Reproduce

```bash
uv run python scripts/onetime_option_bots_backtest.py --hedge           # WW vs fixed band
uv run python scripts/onetime_index_vol_backtest.py --gates             # grids A and B
FRED_API_KEY=... uv run python scripts/onetime_index_vol_backtest.py --gates   # with CPI days
uv run python scripts/onetime_option_replay_backtest.py --strategy crossvol    # once option_quotes has history
python -m tradingbot.optionchainsnapshot --backfill                     # rebuild vol_surface / scan from option_quotes
```
