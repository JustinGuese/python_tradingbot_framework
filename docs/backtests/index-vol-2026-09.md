# option_IndexVolBot: SPY iron condors when implied beats forecast vol (2026-09)

Script: `scripts/onetime_index_vol_backtest.py`. Window 2000-03-10 → 2026-09-25,
split at 2013-06-19:
- **H1** (2000–2013, with the dot-com bust and 2008) is where parameters are chosen;
- **H2** (2013–2026, with 2015, February 2018, 2020 and 2022) is where they are judged.

Each run starts with $100k. Alpha is measured against QQQ.

## Why an index

Round 2 of the AAPL option bots ([option-bots-round2-2026-09.md](option-bots-round2-2026-09.md))
found the pricing-mismatch signal real, but its short at-the-money structure lost
the premium to single-stock jumps. This bot tries the same premium on an index,
which has three advantages:
- Indexes jump less than single names.
- SPY was not picked with hindsight, unlike AAPL.
- The backtest's implied vol is real. ^VIX is the 30-day implied vol of the
  S&P 500, not a stand-in scaled from another asset, and it covers two crashes.

`shortvolcarrybot` (unscheduled, see [shortvolcarrybot.md](shortvolcarrybot.md))
sold vol through SVXY, the VIX-futures ETF, and failed because that
instrument's premium disappeared after 2018. This bot sells defined-risk SPY
option spreads instead: a different instrument with a capped loss.

## Pricing: calibrated, not assumed

Every option is priced with Black-Scholes as
`iv(K) = ATM × (1 − skew × z)`, where `z = ln(K/S)/(ATM·√T)`, with separate
put and call skews. The parameters come from the live SPY chain of 2026-09-25:

| | ATM IV / index | Put skew | Call skew |
|---|---|---|---|
| SPY vs ^VIX, 28–66 DTE | 0.82–0.92 (used: **0.85**) | 0.24–0.31 (**0.25**) | 0.08–0.12 (**0.08**) |
| QQQ vs ^VXN | 0.86–0.97 (**0.90**) | 0.20–0.23 (**0.21**) | 0.07–0.10 (**0.07**) |

^VIX is a variance-swap rate, so it already includes the skew. Using it
directly as at-the-money vol would overstate every credit by 10–15%.

Other modeling choices:
- **Expiries:** every Friday. SPY lists weeklies, so "the first expiry at
  least 35 days out" is 35–41 days, as it is live.
- **Fills:** the AAPL harness's cost model, mid ± max(0.0074% of spot, 1.5%
  of premium) per leg. That is wider than SPY's quotes today and narrower
  than before the 2008 penny pilot, so double costs are tested below.

## The gap barely predicts the at-the-money premium

The table compares ATM IV minus the HAR fair vol against ATM IV minus the vol
realized over the next 21 days.

| IV − fair (pts) | Days | Mean IV | Mean fair | Realized next 21d | IV − realized, mean | Share IV > realized |
|---|---|---|---|---|---|---|
| < −3 | 2607 | 14.1% | 18.9% | 13.5% | +0.6 | 72% |
| −3 … 0 | 2445 | 15.8% | 17.4% | 15.6% | +0.1 | 64% |
| 0 … 3 | 1043 | 20.6% | 19.3% | 19.6% | +1.0 | 65% |
| 3 … 6 | 401 | 25.2% | 21.0% | 23.2% | +1.9 | 69% |
| 6 … 10 | 137 | 30.9% | 23.3% | 27.9% | +3.1 | 71% |
| > 10 | 22 | 39.2% | 27.6% | 38.1% | +1.1 | 50% |

- The correlation is 0.06, against 0.38 on AAPL.
- SPY's at-the-money premium is thin, 0–3 vol points. Most of ^VIX's
  famous premium over realized vol sits in the put skew, not at the money.
- The gap does pick out high-vol stretches, where the premium is largest in
  points. That is what the gate uses.

## Walk-forward

**Defaults**, the textbook condor chosen a priori:
- 16-delta shorts, 5% wings, 45 DTE;
- out at 50% of the credit, at 2× the credit, or at 21 DTE;
- sell whenever IV ≥ fair.

**Grid:** 864 combinations of DTE, put and call delta, wing width, take-profit,
stop, exit DTE and minimum gap.

| | H1 t | H2 t |
|---|---|---|
| Defaults | 0.90 | −0.13 |
| Single H1 winner (0.10Δ, 10% wings, 35 DTE, no gap gate, exit 7 DTE) | 4.13 | **−0.47** |
| Mean of the H1 top 10 | — | 1.59 |
| **H1 top-10 consensus** (each parameter's most common value) | 3.45 | **2.64** |
| Share of the grid with H2 t > 0 | — | 52% |

The single H1 winner fails out of sample, so under the protocol it does not
ship. The top 10 as a group does generalize. What separates the variants that
hold up on H2 from those that don't is the gap gate: only selling when ATM IV
is at least 3 points above the HAR forecast.

**The consensus rule was written down after seeing that the single winner
failed**, so its H2 t of 2.64 is not a clean out-of-sample number. The clean
evidence is the set of checks below, none of which was used to choose it.

## The rules now live

`IndexVolRules(target_dte=35, put_delta=0.10, call_delta=0.10, width_pct=0.10, min_gap=0.03, exit_dte=7)`,
with take-profit at 50% of the credit, stop at 2× the credit, ^VIX < 40, and
max loss 20% of the book.

| Run | Window | CAGR | Alpha/yr | t | Beta | Corr | Max DD | Trades |
|---|---|---|---|---|---|---|---|---|
| SPY, live rules | full | +2.1% | +2.0% | 4.28 | 0.01 | 0.07 | -6.4% | 103 |
| SPY, live rules | H1 | +2.6% | +2.6% | 3.45 | 0.01 | 0.09 | -6.4% |  |
| SPY, live rules | H2 | +1.6% | +1.5% | 2.64 | 0.00 | 0.04 | -5.4% |  |
| SPY, defaults | full | +1.6% | +1.2% | 0.75 | 0.06 | 0.19 | -19.9% | 165 |
| SPY buy & hold | full | +8.5% | +3.1% | 1.61 | 0.62 | 0.86 | -55.2% | - |

**Robustness checks** (none of these was used to choose the rules):

| Check | Full t | H1 t | H2 t | Max DD |
|---|---|---|---|---|
| Double transaction costs (`OPTION_COST_SCALE=2`) | 3.16 | 2.51 | 2.07 | -7.9% |
| Fills at mid | 5.06 | 4.04 | 3.17 | -6.1% |
| **QQQ / ^VXN, same rules** (2001–2026) | 2.39 | 2.44 | 0.85 | -11.3% |
| Put skew 0.15 instead of 0.25 | 3.48 | 2.79 | 2.19 | -8.0% |
| Put skew 0.10, flat calls | 2.07 | 0.96 | 2.51 | -9.1% |
| ATM = 0.80 × VIX (the gate opens less: 58 trades) | 1.81 | 1.20 | 1.43 | -6.2% |
| ATM = 0.75 × VIX (34 trades) | 3.07 | 1.80 | 2.64 | -4.1% |

On QQQ the defaults lose (t −1.14, max DD −51%) while the chosen rules stay
positive in both halves.

**Per trade**, which is the honest sample size given about 4 trades a year:

| | Trades | Win rate | Mean | Worst | Trade-level t | Losing years |
|---|---|---|---|---|---|---|
| SPY | 83 | 94% | +0.67% | -6.0% | 4.85 | 2011 −1.0%, 2015 −2.3%, 2016 −0.9% |
| QQQ | 44 | 91% | +1.01% | -6.2% | 3.72 | 2019 −0.9%, 2020 −5.1% |

Here back-to-back positions count as one trade, so SPY's 103 opens become 83.

## What the numbers mean

- **This is the first option bot with positive alpha in both halves and zero
  beta.** It is also the first whose implied vol is observed rather than
  proxied. Beta is 0.00–0.01 and correlation to QQQ is under 0.1, which is
  exactly the kind of return CLAUDE.md asks for.
- **The edge is small:** about +2%/yr at 20% max risk per condor. Alpha scales
  roughly with that sizing, and the t-stat does not. A larger `max_risk_pct`
  would raise both return and drawdown; it was not tuned.
- **Where it comes from:**
  - selling 10-delta wings, which the put skew prices richly;
  - only after vol has risen above what the forecast expects;
  - with wings 10% away, so a crash costs a bounded ~6% of the book instead
    of running over the short strike.
- **What could make it worse live:**
  - The skew is calibrated on one day. It is steeper in calm tapes and
    flatter in panics, but the result survives halving it.
  - American exercise and dividends on SPY are not modeled.
  - The live gate uses the chain's real at-the-money IV, not 0.85 × ^VIX.
- **Still unproven** until the live paper record says otherwise. The
  `option_quotes` capture of SPY and QQQ chains (from 2026-09-28) will let
  the skew and the credits be checked against observed prices.

## Reproduce

```bash
uv run python scripts/onetime_index_vol_backtest.py                     # live rules + defaults, signal table
uv run python scripts/onetime_index_vol_backtest.py --tune              # the walk-forward grid
uv run python scripts/onetime_index_vol_backtest.py --underlying QQQ    # robustness
OPTION_COST_SCALE=2 uv run python scripts/onetime_index_vol_backtest.py # cost stress
```
