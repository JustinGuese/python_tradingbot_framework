# option_IndexVolBot on real SPY option data (2026-09-29)

**Verdict: on real option prices, IndexVolBot is not distinguishable from
zero.**
- 2019–2026: alpha +0.8%/yr, t 0.45.
- Since early 2024 (the second half by sampled days): alpha −1.6%/yr, t −1.01.

The synthetic backtest says t 3.23 over the same window. The halves split the
sampled days in two; they are denser after mid-2024, so the split falls around
January 2024. That result was an
artifact of its pricing model, mainly the call skew. The bot's live rules
(`OptionIndexVolBot.RULES`) are unchanged in this test.

## Data

The DoltHub `post-no-preference/options` backfill in `option_quotes` covers
SPY from 2019-05-10 to 2026-09-25, 1,177 days. It is a **daily sample**, not a
full chain:
- ~3 expiries a day, nearest to ~14, ~30 and ~45–65 days;
- ~22 strikes each;
- Mon/Wed/Fri only until mid-2024.

The sample changes daily. A condor leg the bot opened was quoted again on about
1 day in 20, and its expiry on about 1 day in 4.

A plain replay (`onetime_option_replay_backtest.py`) therefore marks a held leg
at its last recorded quote, which is usually the entry price. That gave
t 0.26, a number that measures the gaps rather than the strategy.

## Method: a per-day surface

`utils/option_surface.py` fits each day's vol surface from whatever that day
recorded:
- **IV:** solved from the mid with the bots' own r and q, on the
  out-of-the-money side only.
- **Moneyness:** standardised as ln(K/F) / T^0.35.
- **Within an expiry:** IV is linear in moneyness.
- **Across expiries:** total variance is linear in T.
- **Spread:** the median of the day's quotes nearest in price.

`SurfaceReplayMarket` (in `utils/option_replay.py`) quotes any contract the day
did not record off that surface. With `live_expiries=True`, the chain the
strategy enters is the one the live bot would see: the first Friday at least
35 days out, on SPY's $1 strike grid.

### How accurate the surface is

The test hides one expiry per day, predicts it from the rest, and scores the
prediction against the recorded mids (SPY, 197 sampled days, 15k quotes).
Figures are median vol points.

| Case | Delta 5–15 bias | Delta 5–15 abs. error | Wings <5 delta bias | Delta 15–70 bias |
|---|---|---|---|---|
| Interpolated (30-day held out) | +0.15 | 0.34 | −0.31 | +0.05 to +0.16 |
| Extrapolated (14-day held out) | +0.54 | 0.88 | −1.19 | +0.18 to +0.42 |

**The exponent 0.35 was chosen on this test.** At 0 (fixed ln(K/F)), near-expiry
wings came out 4.8 points too cheap. At 0.5, the 5–15 delta options came out
1.1 points too rich.

**AAPL was not used to choose it.** It interpolates as well (bias within
±0.5 points out of the money). Extrapolation there runs 1–3 points low,
plausibly from earnings bumps in its term structure.

A short wing marked slightly cheap flatters a short-vol book. The real result
is therefore not better than shown.

## Result: 2019-05-10 → 2026-09-25, live rules, $100k

| Pricing | Trades | Win rate | Net P&L | Alpha/yr | t | Beta | Max DD |
|---|---|---|---|---|---|---|---|
| Synthetic (^VIX × 0.85, calibrated skew) | 32 | 100% | +$22.5k | +2.6% | 3.23 | 0.01 | −2.3% |
| Real surface, live expiries: full | 40 | 88% | +$4.6k | +0.8% | 0.45 | 0.01 | −6.2% |
| Real surface: H1 (2019-05 → ~2024-01) | | | | +3.0% | 0.89 | 0.00 | −6.2% |
| Real surface: H2 (~2024-01 → 2026-09) | | | | −1.6% | −1.01 | 0.03 | −4.5% |
| Real surface, sampled expiries (42–65 DTE) | 23 | 87% | +$0.8k | +0.3% | 0.16 | 0.00 | −8.1% |

**The synthetic book never lost.** Every one of its 32 condors reached the 50%
take-profit, most within 1–3 weeks.

**On real prices, 35 winners averaging +$660 are erased by 5 losers.**

| Opened | Loss | What happened |
|---|---|---|
| 2020-05-13 | −$4.6k | The May–June 2020 rally, through the short call. |
| 2020-07-27 | −$2.7k | The August 2020 rally; the condor was still under water at 7 DTE. |
| 2022-07-13 | −$3.4k | The July–August 2022 bear-market rally, call side. |
| 2022-09-12 | −$3.4k | The September 2022 selloff, put side. |
| 2025-03-24 | −$4.6k | The April 2025 tariff crash, put side. |

## Why the synthetic backtest was wrong: the call skew

The synthetic prices options as `ATM × (1 − skew × z)`, with the put and call
skews calibrated on **one** live chain, 2026-09-25, a calm day. Across
2019–2026, real upside wings are much cheaper than that. The strike the bot
picks at 10 delta therefore sits closer to spot.

Median distance of the short strikes from spot at entry:

| | Short call | Short put |
|---|---|---|
| Synthetic | 9.0% | 11.6% |
| Real surface | **7.1%** | 11.3% |

A 2-point-closer short call is what the V-shaped recoveries of 2020 and 2022
ran through. Put placement matched.

**The entry signal itself holds up on real data.** Real 1-month SPY ATM IV
divided by ^VIX has a median of 0.84 over 1,177 days, against the 0.85 the
synthetic assumes. It ranges from 0.75 to 0.90 by year. The real gap signal
fires in the same episodes as the synthetic one. The strategy fails on what it
sells, not on when it sells.

## Caveats

- **Fills are model fills.** 98% of the quotes the replay needed came off the
  surface, and the spread is modelled from the day's recorded spreads. What is
  real is the level, skew and term structure of SPY's vols each day.
- **Fewer days before mid-2024.** The replay steps only on sampled days
  (Mon/Wed/Fri until mid-2024), so exits can be a day or two late.
- **Seven years of history.** The synthetic covers 2000–2026. Rerunning that
  full window with skews calibrated on this surface is the obvious next check.

## Reproduce

```bash
kubectl port-forward -n tradingbots-2025 svc/psql-service 5432:5432 &
POSTGRES_URI=... uv run python scripts/onetime_option_replay_backtest.py --strategy indexvol --end 2026-09-25 --live-expiries
POSTGRES_URI=... uv run python scripts/onetime_option_replay_backtest.py --strategy indexvol --end 2026-09-25 --surface
```
