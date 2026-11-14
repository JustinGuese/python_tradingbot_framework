# Retail book: joint walk-forward study, 2026-11-14

This is PLAN.md Part A, step A1. It asks whether eToro, MT5/MQL5 and Collective2
should copy InstitutionalFlowBot, either alone or in an inverse-vol blend, or
whether a plain "QQQ above its 200-day SMA, else cash" book does the same job.

**Result:** the ship rule passes, but only one stream could be reproduced, so
the "blend" is InstitutionalFlowBot at weight 1.0. By the CLAUDE.md bar the edge
is **unproven**: H2 alpha vs QQQ is +9.0%/yr at t 1.48, and vs the EW universe it
is +6.5%/yr at t 1.13. Neither reaches t ≥ 2.

## Method

`scripts/onetime_retail_blend_study.py` runs offline. It uses yfinance data in
memory and a scratch SQLite DB, and does not touch Postgres.

- **InstitutionalFlowBot** runs with its shipped defaults: top 20, rebalanced
  on Fridays, with the QQQ < SMA200 gate.
  - The engine is the framework's `backtest_bot`: 0.05% slippage per side and
    the stage-2 no-trade band.
  - It is one continuous run from 2019-01-02 to 2026-11-13, sliced into halves.
    The H2 book therefore carries its H1 positions, as a live book would.
  - Features are warm from 2017 history.
  - Its H1 numbers match `institutionalflowbot.md` exactly.
- **Universe:** the 95 S&P 100 names with daily history from before 2017. GEV,
  PLTR, SNDK and UBER are excluded, because the engine inner-joins timestamps.
  These names are held live but are missing from the backtest.
- **Halves:**
  - H1 = 2019-01-03 → 2022-12-30 (1,007 days). The weights are fit here, and so
    were the defaults, in the earlier 12-combination grid.
  - H2 = 2023-01-03 → 2026-11-13 (971 days). This half is judged.
  - PLAN.md's ship rule says "second half (2019-06 to 2026-11)". That reads as a
    typo, and the A1 brief's H2 = 2023 → 2026-11 is used here.
- **Blend:** weights are inverse-vol on H1 daily returns only
  (`inverse_vol_weights`). Rows on or after 2023-01-01 never enter the fit.
- **QQQ + SMA200 gate:** the signal is formed at close t (close > the SMA200 of
  closes up to t) and earns the return of t+1. In the main columns cash earns 0.
  The T-bill column pays ^IRX/252 on cash days.
- **EW S&P 100 hold:** an equal-weight buy of the same 95 names at the first
  close of each half, then held.
- **Alpha:** `utils/alpha_report.alpha_stats` on daily returns. The QQQ
  regression and the EW regression are both run within each half.
- **Columns:**
  - **Cash share** is the mean fraction of the book not invested, rebuilt from
    the trade log.
  - **Trades per week** counts every fill (each buy or sell leg).
  - **20 bp** gives CAGR and max DD with an all-in cost of 20 bp per side on
    traded notional. For InstitutionalFlowBot that is 15 bp on top of the
    engine's built-in 5 bp. The baselines are charged on their switches and
    initial buys.
  - **T-bill** is CAGR with cash earning ^IRX. This is what the live copier's
    SHV parking approximates.

## Dropped streams

- **option_ImpliedSignalBot: dropped.** Its curve is built from the
  `option_quotes` table, which exists only in production Postgres. No offline
  cache or exported curve exists: `/tmp/implied_signal_cache` is absent and the
  study's grid CSV holds metrics, not returns. This study was not allowed to
  access production, so the stream cannot be reproduced.
- **EarningsInsiderTiltBot: dropped.** It needs DB earnings and insider history,
  and insider rows were untyped before 2026-09-22. No reproducible backtest
  curve exists.
- **Consequence:** the inverse-vol "blend" has one leg, with weights
  `{"InstitutionalFlowBot": 1.0}`. Its rows are identical to InstitutionalFlowBot
  alone.

## Results

Alpha is annualised, with its t-stat in brackets. The α columns are measured vs
QQQ and vs the EW hold over the same half. "—" marks a stream regressed on
itself.

### H1 (2019-01-03 → 2022-12-30, fit)

| Stream | CAGR | Max DD | α vs QQQ (t) | Beta | Corr | α vs EW (t) | Cash | Trades/wk | CAGR / DD @20 bp | CAGR T-bill |
|---|---|---|---|---|---|---|---|---|---|---|
| InstitutionalFlowBot | +12.1% | −21.9% | +7.1% (1.20) | 0.30 | 0.56 | +6.1% (1.06) | 30% | 10.1 | +8.5% / −22.3% | +12.7% |
| Inverse-vol blend (= IFB 1.0) | +12.1% | −21.9% | +7.1% (1.20) | 0.30 | 0.56 | +6.1% (1.06) | 30% | 10.1 | +8.5% / −22.3% | +12.7% |
| QQQ | +15.3% | −35.1% | — | 1.00 | 1.00 | −1.2% (−0.26) | 0% | 0.0 | +15.2% / −35.1% | +15.3% |
| QQQ + SMA200 gate | +14.6% | −21.9% | +7.5% (1.12) | 0.43 | 0.66 | +7.6% (1.04) | 28% | 0.1 | +13.5% / −22.4% | +15.3% |
| EW S&P 100 hold | +16.5% | −33.8% | +3.1% (0.75) | 0.85 | 0.94 | — | 0% | 0.5 | +16.5% / −33.8% | +16.5% |

### H2 (2023-01-03 → 2026-11-13, judged)

| Stream | CAGR | Max DD | α vs QQQ (t) | Beta | Corr | α vs EW (t) | Cash | Trades/wk | CAGR / DD @20 bp | CAGR T-bill |
|---|---|---|---|---|---|---|---|---|---|---|
| **InstitutionalFlowBot** | **+21.8%** | **−11.9%** | **+9.0% (1.48)** | 0.44 | 0.60 | **+6.5% (1.13)** | 11% | 13.1 | +17.0% / −13.0% | +22.4% |
| Inverse-vol blend (= IFB 1.0) | +21.8% | −11.9% | +9.0% (1.48) | 0.44 | 0.60 | +6.5% (1.13) | 11% | 13.1 | +17.0% / −13.0% | +22.4% |
| QQQ | +28.2% | −22.8% | — | 1.00 | 1.00 | −1.1% (−0.32) | 0% | 0.0 | +28.2% / −22.8% | +28.2% |
| QQQ + SMA200 gate | +19.1% | −16.1% | −0.8% (−0.17) | 0.73 | 0.86 | −1.1% (−0.20) | 8% | 0.1 | +18.3% / −16.5% | +19.5% |
| EW S&P 100 hold | +26.6% | −19.8% | +3.9% (1.28) | 0.79 | 0.94 | — | 0% | 0.5 | +26.6% / −19.8% | +26.6% |

For the full window, the framework's own `backtest_bot` alpha for
InstitutionalFlowBot is +8.8%/yr at t 2.05, beta 0.35, corr 0.57. That window
includes H1, where the defaults were chosen, so it is not out-of-sample
evidence.

## Ship rule, applied literally (H2)

| Check | InstitutionalFlowBot / blend | Pass |
|---|---|---|
| H2 CAGR ≥ 15% | +21.8% (+17.0% at 20 bp) | yes |
| H2 max DD ≥ −30% | −11.9% (−13.0% at 20 bp) | yes |
| α vs EW hold > 0, H1 | +6.1% | yes |
| α vs EW hold > 0, H2 | +6.5% | yes |
| Not beaten by gated QQQ on H2 return AND drawdown | gated QQQ +19.1% / −16.1% loses on both | yes |

The verdict does not change with costs or cash yield:

- **At 20 bp per side,** the gated QQQ out-returns the bot: +18.3% vs +17.0%.
  It still has the deeper drawdown (−16.5% vs −13.0%), so it does not beat the
  bot on both, and the rule still passes.
- **With T-bill cash on both sides,** the bot makes +22.4% and the gated QQQ
  +19.5%. That is a pass.

## Reading it honestly

- **Unproven, not working.** No alpha in this study reaches t ≥ 2 out of sample.
  The ship rule is a product rule: return, drawdown and beating free
  alternatives. It is not the CLAUDE.md edge bar. Passing it means the book is
  a reasonable thing to copy, not that it has proven skill.
- **The gate does most of the work in H1, not in H2.** In H1 the gated QQQ
  matches the bot on drawdown and alpha (+7.5% vs +7.1%). In H2 the gate alone
  adds nothing (−0.8% alpha, beta 0.73). In the same half the bot keeps +9.0%,
  at a lower beta of 0.44 and a shallower drawdown. The stock-selection leg is
  what separates it from the free baseline in H2. That leg is exactly the part
  whose t is 1.1 to 1.5.
- **It lags buy-and-hold in a rally.** H2 QQQ made +28.2% and the EW hold made
  +26.6%, against +21.8% for the bot. That gap is the cost of a 0.44 beta and an
  11% average cash share. It is a lower-beta book, not a higher-return one.
- **Survivorship (today's constituents).**
  - The universe is today's S&P 100. The EW hold of those survivors beats QQQ
    by +3.1% and +3.9%/yr with no signal at all, and some of that alpha is
    survivorship.
  - The α vs EW column cancels the bias of holding names that turned out to be
    winners. It does not cancel how today's list shapes which names pass the
    trend filter in 2019.
  - The four recent listings excluded here (GEV, PLTR, SNDK, UBER) are traded
    live. The live book can differ from this curve.
- **Costs bite.** At 13 fills per week, going from 5 bp to 20 bp per side costs
  about 4.8 points of H2 CAGR (+21.8% to +17.0%). On retail venues, spreads on
  large caps are well under 20 bp, but eToro/MT5 CFD financing is not modelled.
- **Not modelled:** execution timing. The engine fills at the Friday close that
  produced the signal. Live, the bot runs at 21:15 UTC on Friday, after the
  close, so fills land on the next session.
- **The defaults saw H1 only, but the bot was designed in 2026-09** by someone
  who could see the whole price history. H2 is out of sample for the
  parameters, not for the design.

## Reproducing

```bash
RETUNE_SCRATCH=<scratch dir> PYTHONPATH=. uv run python scripts/onetime_retail_blend_study.py > retail_blend.json
```

It takes about 4 minutes, mostly yfinance downloads and the single backtest.
The JSON is also written to `<scratch dir>/retail_blend.json`.

**InstitutionalFlowBot alone: SHIP** (all five checks pass; edge unproven at H2 t 1.48).

**Retail book weights:** `{"InstitutionalFlowBot": 1.0}`. option_ImpliedSignalBot
and EarningsInsiderTiltBot were dropped as not reproducible offline.

SHIP
