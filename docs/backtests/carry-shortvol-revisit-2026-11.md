# Carry and short-vol revisit: can they turn positive? (2026-11-19)

Question: ShortVolCarryBot and MultiAssetCarryBot were shelved as negative. They are now run as
paper bots for a forward record. Is there a change that makes either one a positive-alpha bot?
Everything is judged on alpha vs QQQ and its t-stat (CLAUDE.md), weekday rows, H1/H2 halves.

Scripts (all reproduce the tables below; yfinance + FRED public CSV only):
`scripts/onetime_fx_signed_carry_study.py`, `scripts/onetime_fx_fred_signed_study.py`,
`scripts/onetime_shortvol_gate_study.py`.

**Verdict: neither can be fixed by a design change. Both stay paper-only as a forward record.**

## 1. Signed (long/short) FX carry on the six CurrencyShares ETFs

The earlier docs said the missing ingredient was signed exposure. It is now available
(`ALLOW_SHORT`), so this tests it. P&L is the ETFs' own adjusted-close returns (FXE, FXB, FXA, FXC,
FXF, FXY), monthly rebalance, 5 bp per unit of turnover, 0.5%/yr borrow on shorts, 2007-2026.
"Framework basis" credits 0% on cash, as the paper books do. "+rf" credits the T-bill on collateral,
which is what a real broker rebates.

| Ranking signal (long top-k, short bottom-k, gross 1.0) | Basis | Alpha/yr | t | Beta | H1 t | H2 t |
|---|---|---|---|---|---|---|
| Bot's ETF-minus-spot carry, k=2 | framework | -3.11% | -3.46 | +0.08 | -2.31 | -2.63 |
| Bot's ETF-minus-spot carry, k=3 | framework | -1.67% | -2.50 | +0.05 | -1.46 | -2.13 |
| Bot's ETF-minus-spot carry, k=2 | +rf | -1.55% | -1.72 | +0.08 | -1.88 | -0.04 |
| FRED 3m rate differential, k=2 | +rf | -1.04% | -1.08 | +0.10 | -2.03 | +1.53 |
| FRED 3m rate differential, k=3 | +rf | -0.18% | -0.24 | +0.09 | -1.36 | +1.73 |
| FRED 3m rate differential, k=2 | framework | -2.60% | -2.69 | +0.10 | -2.44 | -0.95 |

Findings:

- **Signing the book does not rescue the ETF version.** Even ranking on the true policy-rate
  differential, alpha is negative to flat. The ETF-minus-spot estimator is worse still, and
  negative with high confidence on the framework basis.
- **The +2.5% / t 2.42 reference in `fx-carry-2026-11.md` does not transfer.** It used nine spot
  pairs (including NZD, NOK, SEK, which have no ETF) and accrued the rate differential by formula.
  The likely causes (not isolated here) are the narrower six-currency cross-section and ETF fees
  and tracking against spot. Treat that reference as an upper bound that cannot be traded here.
- **Lead, not an edge:** ranking on 12-month ETF *total return* (carry plus spot), long top-k /
  short bottom-k, +rf, gives alpha +1.9% t +1.96 (k=2) and +1.55% t +2.09 (k=3), beta about -0.04,
  max drawdown -13%, positive in both halves for k=2. It is the best of about ten variants tried,
  so selection bias is not removed. It needs a forward record, not a deployment.

| Total-return momentum variant (+rf, gross 1.0) | Alpha/yr | t | H1 t | H2 t |
|---|---|---|---|---|
| 12m, k=2 | +1.91% | +1.96 | +1.08 | +1.83 |
| 12m, k=3 | +1.55% | +2.09 | +0.62 | +2.81 |
| 189d, k=2 | +2.01% | +2.03 | +1.55 | +1.17 |
| 63d / 126d / 378d, k=2 | +1.11 / +0.76 / +0.88 | +1.20 / +0.80 / +0.96 | | |

## 2. ShortVolCarryBot: tighter gates on SVXY

Post-2018-03 only (SVXY at -0.5x), sized to a 15% vol target capped at 0.5, signals lagged one
day, idle cash credited at the T-bill. 2,195 days. QQQ buy-and-hold in the same window: +17.6% CAGR.

| Gate | CAGR | Max DD | Alpha/yr | t | Beta | H1 t | H2 t |
|---|---|---|---|---|---|---|---|
| ^VIX3M/^VIX > 1.0, 15% VIXM hedge (shipped-like) | +5.0% | -12.6% | +1.0% | +0.35 | +0.23 | -0.35 | +0.92 |
| ratio > 1.0, no hedge | +5.6% | -16.9% | +0.4% | +0.11 | +0.31 | -0.54 | +0.75 |
| ratio > 1.05 | +2.0% | -20.6% | -2.0% | -0.57 | +0.25 | -1.06 | +0.36 |
| ratio > 1.10 | -0.9% | -22.3% | -3.3% | -1.06 | +0.15 | -0.75 | -0.83 |
| ratio > 1.15 | -0.7% | -25.6% | -1.9% | -0.81 | +0.08 | -1.09 | +0.19 |
| ratio > 1.0 and VRP (VIX minus 20d realized) > 4 | +1.6% | -16.9% | -1.3% | -0.39 | +0.18 | -0.65 | +0.19 |
| ratio > 1.0 and VIX < 20 | -1.4% | -25.2% | -4.2% | -1.30 | +0.18 | -1.13 | -0.81 |
| SVXY buy and hold | +9.6% | -62.2% | -5.4% | -0.62 | +1.13 | -0.87 | +0.05 |

**Stricter gates are monotonically worse.** Adding a variance-risk-premium filter or a VIX cap
removes the days that earn and keeps the ones that do not. The only variants above zero are the
loosest, and their t is 0.1-0.35. The earlier doc's conclusion holds: the premium is gone from the
-0.5x wrapper, and what is left is beta (0.2-0.3) with no alpha. Changing the gate does not
change that. The one untested instrument change is a direct short-variance expression (options),
which this repo cannot yet size.

## 3. option_MispricingBot (already live, no change made)

- **It is not suspended**, so no activation was needed.
- **The live record is two trades.** It sat flat in cash from 2026-09-28 to 2026-11-03, then
  bought two ATM AAPL straddles (11-04 to 11-06, 11-09 to 11-17) and delta-hedged with shares.
  Both lost a few hundred dollars net of spread. The weekly report's alpha -11%/yr, t -1.55 is
  34 daily observations of which about 10 had exposure. It is noise, not evidence.
- **Its best evidence is the real-chain AAPL replay**, `option-aapl-real-calibration-2026-09.md`:
  H2 t 1.62 and full-window t 2.79 on real calibrated pricing, the best of any option bot there.
  That is AAPL only (beta to QQQ is not zero) and synthetic-priced in part.
- **Siblings.** CrossVol (the same signal spread over 25 real names) is t -3.56 on real chains and
  0 of 37 grid cells positive in H2, so the single-name gap edge is not general. Dispersion cannot
  trade until `implied_correlation` holds 60 SPY observations (33 as of 2026-11-10).

## What to watch (forward record)

Judge all five reactivated or running bots by the weekly `bot_alpha_report`, which needs about
75-120 live days before t is interpretable. Specific checkpoints:

- Total-return-momentum signed FX (section 1 lead): worth building as a paper bot only if you want
  a forward test, since its backtest t of about 2 is the best of many tries.
- MispricingBot: needs 20+ round trips before any alpha number means anything.
- DispersionBot: the first trade happens when 60 observations exist (about 27 more captured days).
