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
  Yang-Zhang and GARCH forecasts, the z-scored signal and the tail hedge.
  - All stay off except one. The closest miss, a VIX/VIX3M ≤ 1.0 entry gate,
    **shipped on 2026-09-28 anyway**, for its drawdown. See "Shipped anyway"
    below.
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

The event gate was first run FOMC-only, then rerun with FOMC + CPI once
`FRED_API_KEY` existed (bullet below the table).

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
    That is what happened; see "Shipped anyway" below.
- **Unwinding on inversion hurts.** It closes condors at the worst marks, just
  before vol mean-reverts.
- **The tail hedge buys negative correlation at the cost of drawdown in
  calm years.** At 0.25%/month the H2 t is flat, but the max DD is worse.
- **The FOMC blackout changes almost nothing.** A 35-DTE condor holds through
  one or two meetings whatever day it opens.
- **Adding CPI days (rerun with `FRED_API_KEY`) changes nothing either.**
  - Grid A's H1 winner is the same variant and makes the same H2 t of 0.01.
  - On its own, a FOMC+CPI blackout of 1 session scores H1 1.38 and H2 3.24;
    2 sessions score H1 1.47 and H2 3.76. The live rules score 1.67 and 3.46.
  - The 2-session blackout wins on H2 but loses on H1, where the choice is
    made, so it is not picked.

### Shipped anyway: VIX/VIX3M ≤ 1.0 (2026-09-28)

`option_IndexVolBot.RULES` now carries `max_term_ratio=1.0`. While the vol
curve is inverted (VIX above VIX3M), no new condor opens. This is a
deliberate override of the walk-forward protocol: the gate lost on H2.

It trades H2 alpha t for drawdown, on the 2007–2026 window:

| | Gate | Ungated live rules |
|---|---|---|
| H2 t | 2.65 | 3.46 |
| H1 t | 2.84 | 1.67 |
| Full t | 3.87 | 3.35 |
| Max drawdown | −2.7% | −6.3% |
| Trades | 56 | 80 |

Both halves stay at t ≥ 2 with the gate, which the ungated rules do not
manage on H1.

The mechanism is plain: an inverted curve means the market is already pricing
near-term stress, and a 35-DTE short condor opened then is the trade that makes
the drawdown.

A missing ^VIX3M quote blocks the entry. Revisit this with the live record,
since this choice has no out-of-sample support.

### The live decision path, re-baselined (round 4, 2026-09-28)

Round 4 moved the bot's logic into one pure function,
`utils/option_strategies.decide_indexvol`. The live bot, the replay over stored
chains and a synthetic chain now all execute it.
`scripts/onetime_index_vol_backtest.py --decide` runs it on a synthetic SPY
chain built from the same model as the fast simulator above:
- a $1 strike grid, with contracts listed down to a $0.01 mid;
- the harness's fills;
- strikes chosen by `select_iron_condor`;
- sizing from cash after margin.

**It found a live-vs-backtest drift.** The live bot fitted HAR on 5 years of
closes (`getYFData(period="5y")`). Every backtest, including the walk-forward
that picked these rules, fits it on an expanding window from 1999. On the
2007–2026 window that difference alone was large:

| Live decision path | Full t | H1 t | H2 t | Max DD |
|---|---|---|---|---|
| gated, HAR on 5 years | 1.35 | 0.18 | 1.81 | −5.6% |
| gated, HAR on all history | 4.13 | 2.66 | 3.26 | −2.4% |

The live bot now uses all history (`LiveMarket.closes`, `period="max"`), and
replay and the synthetic chain do the same.

With that fixed, the decision path next to the fast simulator, 2007–2026
(split 2016-11):

| Variant | Full t | H1 t | H2 t | Max DD | Trades |
|---|---|---|---|---|---|
| fast sim, live rules (gate on) | 3.87 | 2.84 | 2.65 | −2.7% | 56 |
| **decide path, live rules (gate on)** | **4.13** | **2.66** | **3.26** | **−2.4%** | 59 |
| fast sim, ungated | 3.35 | 1.67 | 3.46 | −6.3% | 80 |
| decide path, ungated | 2.44 | 0.50 | 3.88 | −7.7% | 80 |

**Result for the shipped rules:** they reproduce within the ±0.3 t acceptance
band on full and H1 (+0.26, −0.18), and do better on H2 and drawdown.

**The ungated rules do not reproduce:** H1 falls from 1.67 to 0.50, with
2008 in that half. What differs is strike selection on a smile-consistent
chain, wings that stop at the last quoted strike, and IVs solved per
contract. They matter most in a crash.

**What this means for the gate:** on the path the bot actually trades, it
beats the ungated rules on full t, H1 t and drawdown, and loses only H2
(3.26 vs 3.88). The drawdown-over-t choice looks better here than it did on
the fast simulator.

The research grids (`--tune`, `--gates`) stay on the fast simulator: the decide
path takes about 3 s per simulated year. Treat their rankings as relative;
confirm a winner with `--decide` before shipping it.

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

### option_CrossVolBot on real chains: paused (2026-10-01)

The 50-name DoltHub import made a real replay possible.

**Setup:**
- 25 names with a full history, 2025-01-02 to 2026-09-29 (434 days).
- Fills at the recorded bid/ask.
- Fair vol was fitted only on closes up to each day.
- The shortlist is every loaded name. Live shortlists by yesterday's scan; with every chain loaded, ranking all names makes the same choice.
- Two ways of pricing the expiry:
  - **Listed:** only the expiries DoltHub recorded.
  - **Bot's own:** the bot's own ~30-day expiry, priced off each day's surface (`--live-expiries`). The data does not list that expiry.

The kill rule was fixed before the result: t < −2 on listed expiries kills it, and the 8-point gap is a robustness check, not a rescue.

| Expiries | Variant | Alpha/yr | t | H1 t | H2 t | Max DD | Trades |
|---|---|---|---|---|---|---|---|
| Listed | live rules (gap ≥ 5) | −10.2% | −3.56 | −2.27 | −2.81 | −14.3% | 34 |
| Listed | gap ≥ 8 | −5.1% | −2.76 | −1.25 | −2.45 | −7.5% | 18 |
| Bot's own (surface) | live rules | −12.0% | −3.61 | −2.65 | −2.56 | −16.7% | 89 |
| Bot's own (surface) | gap ≥ 8 | −8.1% | −3.77 | −1.60 | −3.53 | −11.7% | 52 |

- **Every variant is negative, with beta under 0.1.** It is not losing to the market. It loses on the trades themselves.
- **Raising the gap does not help.** On listed expiries the 8-point gap loses less only because it trades half as often. On the bot's own expiry it loses more (t −3.77).
- **What happened:** paused (`suspend: true`). It never traded live, and its book is $100k cash.
- **Caveats:**
  - The 25 names were picked by today's size. Survivorship flatters short vol, so the real result is, if anything, worse.
  - The replays solved IV with today's T-bill rate. That is close to the 2025–26 rate.

### Where the loss comes from: the signal or the spread? (2026-10-01)

The same replays were rerun twice, once filling every option leg at mid and
once at the recorded bid/ask (`--fill mid`), with a per-trade log
(`--trades-csv`). Each replayed day is now priced at its own T-bill rate.

| | CrossVol (25 names, 2025–26, listed) | IndexVol (SPY 2019–26, live expiries) |
|---|---|---|
| t at bid/ask | −3.59 (34 trades) | −0.71 (36 trades) |
| t at mid | −0.80 (53 trades) | −0.58 (36 trades) |
| Spread paid / total loss | $14,617 / $13,659 | $1,220 / $6,340 |
| IV − realized vol, mean | +1.1 pts | +2.5 pts (median +4.4), 81% of trades win |
| Fair (HAR) − realized, mean | **−6.3 pts** | −1.5 pts |
| Trades with a report inside the hold | 0 | – |

**CrossVol: the spread costs about the whole loss, and nothing is left under it.**
- Before the spread, the 34 trades made +$302 in total.
- The "IV ≥ forecast + 5 pts" gap mostly marks a forecast that runs low. On the names it selects, the HAR forecast came in 6 points under the vol the stock then realized, while IV was only 1 point over it.
- The earnings filter worked: no trade held a report.
- A round trip cost about $430 per condor, roughly 10% of the $4k at risk.

**IndexVol: the premium is real, but the condor gives it back in the tails.**
- Three of 36 trades lose more than the other 33 make: 2020-05-06 (−$6.5k), 2025-03-24 (−$4.6k) and 2022-03-14 (−$2.9k).
- In two of those three, realized vol was *below* IV. The rallies ran through the short call, which is the call-skew problem in
  [index-vol-real-surface-2026-09.md](index-vol-real-surface-2026-09.md).

**Verdict:** better execution (limit orders at mid) would not rescue either strategy. Selling single-name or SPY vol through these structures has no edge on real prices.

## What still limits all of this

- **No per-name option history**, until the capture accumulates or history
  is imported. The replay harness is ready for it: it only needs the rows in
  `option_quotes`.
- **The SPY skew is calibrated on one day** (see
  [index-vol-2026-09.md](index-vol-2026-09.md)). `vol_surface.rr25_30` now
  records it daily, so it can be recalibrated against months of real data.
- **CPI and jobs-report dates come from FRED** (`FRED_API_KEY`, in the
  cluster secret since 2026-09-28). If the key goes missing,
  `macrocalendarsnapshot` exits 1 and the calendar stays FOMC-only.

## Reproduce

```bash
uv run python scripts/onetime_option_bots_backtest.py --hedge           # WW vs fixed band
uv run python scripts/onetime_index_vol_backtest.py --gates             # grids A and B
FRED_API_KEY=... uv run python scripts/onetime_index_vol_backtest.py --gates   # with CPI days
uv run python scripts/onetime_option_replay_backtest.py --strategy crossvol    # once option_quotes has history
python -m tradingbot.optionchainsnapshot --backfill                     # rebuild vol_surface / scan from option_quotes
```
