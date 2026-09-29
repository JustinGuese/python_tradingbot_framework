# Prediction markets as a signal — kill tests and overlay walk-forward (2026-09-28)

**Verdict:** the data pipeline ships; both trading ideas are dropped.

- **Capture ships.** `predictionmarketsnapshot` records the curated Kalshi and
  Polymarket macro contracts daily, with history backfilled from 2021-07.
- **Kill test A passes for the Fed path and fails for recession odds.**
- **The overlay bot is not scheduled.** Out of sample (2024–2026) its alpha
  was +3.9%/yr at t 1.03, below the t ≥ 2 bar. It also lost to the same book
  gated on QQQ < SMA200.
- **Kill test B fails.** On Kalshi's S&P 500 range contracts, a flat-vol
  lognormal at ^VIX1D is better calibrated than Kalshi's own prices (Brier t
  +9.0). No slice favours Kalshi at significance, so the threshold-gap options
  trade (Phase 2) is not built.

## Data

| Series | Venue / ticker | Markets | From | Median OI | Median spread |
|---|---|---|---|---|---|
| fed_rate | Kalshi KXFED (+ legacy FED-) | 660 | 2021-07-02 | 757 | 0.05 |
| fed_decision | KXFEDDECISION | 195 | 2023-04-07 | 262 | 0.06 |
| cpi_mom / cpi_yoy | KXCPI / KXCPIYOY | 552 / 713 | 2021-06 / 2022-12 | ~550 | 0.04–0.06 |
| payrolls / unemployment / gdp | KXPAYROLLS / KXU3 / KXGDP | 422 / 627 / 219 | 2023-03 / 2021-07 / 2021-06 | 10 / 251 / 2245 | 0.04–0.06 |
| recession | KXRECSSNBER | 6 | 2022-07-01 | 68k | 0.01 |
| shutdown | KXGOVSHUT, KXGOVTSHUTDOWN | 23 | 2021-09-28 | 9.6k | 0.03 |
| pm_recession / pm_shutdown | Polymarket events | 4 / 12 | 2024-08 / 2023-09 | — | — |

About 257k daily rows in total. The Kalshi API quirks the parser has to
handle are listed in AGENTS.md under "7. Prediction markets": the historical
tier, the two candle schemas, and the legacy ticker-only strikes.

**Read the rules before using a series.** Kalshi's "Will there be a recession in
2026?" resolves Yes on two consecutive negative GDP quarters in 2025 *or* 2026.
That is a rolling two-year window, and it is not NBER despite the ticker.

## Kill test A — coverage

These are point-in-time features on business days since 2021-06
(`scripts/onetime_prediction_market_coverage.py`):

| Feature | First bar | Coverage after | Longest gap (bdays) |
|---|---|---|---|
| fed_path_bps (≈6-month rate path vs current) | 2021-12-28 | 99.9% | 1 |
| fed_next_bps | 2021-07-30 | 97.5% | 32 |
| fed_cut_next_prob | 2022-03-18 | 98.8% | 12 |
| cpi_next_mean / std | 2021-10-15 | 100% | 0 |
| shutdown_prob | 2021-09-29 | 100% | 0 |
| recession_prob | 2022-07-04 | 94.1% | **65** (Jul–Nov 2022) |
| pm_shutdown_prob / pm_recession_prob | 2023-09 / 2024-08 | 100% | 0 |

**Pass for the Fed path, fail for recession.** The first half of the
walk-forward therefore starts at 2021-12-27, and recession odds cover only the
second half of it.

Two feature bugs were found and fixed while checking these numbers. Both
affected every Fed feature.

- **Quiet strikes.** Kalshi returns a candle only on days a strike traded, so
  on quiet days the lower strikes of a ladder went missing. For a January 2024
  meeting that was a certain hold, `fed_next_bps` read +24bp.
  `fill_gaps` now carries each strike's last price for up to 10 days.
- **Missing strikes.** The January 2024 ladder listed 5.25 and 5.75 but no
  5.50. The ladder mean put the mass between those two strikes at 5.75. It now
  puts it at the next 25bp level (5.50).

## Phase 1 — PredictionMarketOverlayBot walk-forward

- **Base book:** SPY/QQQ 40/40, IEF 10, GLD 10.
- **Risk score r:** the larger of two components.
  - Recession odds between `rec_lo` and `rec_hi`.
  - The rise in `fed_path_bps` over `hawk_lookback` bars, divided by `hawk_bps`.
- **Sizing:** equity is scaled by (1 − r).
  - Weight freed by recession risk goes to IEF.
  - Weight freed by hawkish repricing goes to cash.
- **Timing:** rebalances weekly on Fridays.
- **Protocol:** grid-search on H1 (2021-12-27 → 2023-12), judge on H2 (2024-01
  → 2026-09).
- **Script:** `scripts/onetime_prediction_overlay_walkforward.py`.

| Variant | H1 alpha | H1 t | H2 alpha | H2 t | H2 beta | H2 max DD | H2 trades |
|---|---|---|---|---|---|---|---|
| PM, H1-tuned (hawk_bps 25, lookback 10) | +6.8% | 1.13 | **+3.9%** | **1.03** | 0.38 | 10.2% | 291 |
| PM, defaults | +2.6% | 0.43 | +2.4% | 0.66 | 0.40 | 11.4% | 283 |
| QQQ < SMA200 ablation | +0.5% | 0.08 | +6.7% | 1.41 | 0.46 | 8.6% | 34 |
| Static book (no overlay) | +0.1% | 0.07 | +3.6% | 1.92 | 0.70 | 16.5% | 34 |

Reading it:

- **The prediction-market signal earned its keep in 2022–23**, which is where
  it was tuned. That was the hiking cycle, the one regime the Fed-path feature
  is built to see.
- **Out of sample** it delivers the static book's alpha at half the beta and a
  lower drawdown. That is a risk reduction, not added alpha.
- **The SMA200 ablation beats it on H2 alpha and t.** The gate required both
  t ≥ 2 and beating the ablation. It fails both.
- **It churns about 9× more** than the ablation, because a continuous score
  moves the book every week.

It is **not scheduled** and is in no copier's `botWeights`. That follows the
precedent in values.yaml ("NOT SCHEDULED"). Adding a `bots:` entry is all it
takes to paper-trade it anyway.

## Kill test B — calibration vs the options market

`scripts/onetime_prediction_market_calibration.py`:

- **Contracts:** every settled Kalshi S&P 500 **daily** range contract settling
  at the 16:00 ET close, from 2022-04 (INX-/INXD-/KXINX-). There were 11,334
  contracts on 1,062 days (2022-04-29 to 2026-09-25), within ±3σ of spot.
- **Kalshi price:** taken one hour after the prior close.
- **Options price:** a lognormal range probability at the prior close's
  **^VIX1D** (^VIX9D before 2023-04).

**A proxy, not real digitals:** Kalshi's S&P contracts are all daily. The free
SPY chain history (DoltHub) has only 14/28/43-DTE expiries, so no 1-day call
spread can be priced from real quotes. The proxy has no skew, which *handicaps
the options side*.

| Slice | n | Brier Kalshi | Brier options | t (Kalshi − options, by event) |
|---|---|---|---|---|
| **All** | 11,334 | 0.0831 | **0.0750** | **+8.96** |
| VIX1D era (2023-04+) | 9,644 | 0.0755 | 0.0692 | +7.90 |
| ATM, abs(z) ≤ 0.5σ | 1,970 | 0.1724 | 0.1717 | −0.64 |
| 1–2σ | 3,879 | 0.0670 | 0.0588 | +5.99 |
| 2–3σ | 3,531 | 0.0230 | 0.0081 | +10.19 |
| Highest-volume third | 3,778 | 0.1717 | 0.1720 | −1.97 |

Options are better in every year from 2022 to 2026 (t +3.4 to +8.5).

**Kalshi's prices are systematically too high.** Prices of 0.10–0.20 paid out
10.5% (mean price 0.15); 0.35–0.50 paid 23% (price 0.44); 0.50–0.65 paid 20%
(price 0.54). The flat-vol options proxy is close to the diagonal throughout.
That is the favourite-longshot bias plus fee-driven overround across the
ranges.

**The mispricing is on the side we cannot trade.** Fading Kalshi's overpriced
ranges would be the trade, but Kalshi is US-only. Polymarket's comparable
markets are also geo-blocked. Buying options where Kalshi's odds are higher
would mean following the worse-calibrated forecaster. **Phase 2 is not built.**

A parsing bug was found here too. Legacy tail contracts ("INX-22MAY03-T4000:
3999.99 or lower") carry their strike only in the ticker, and were read as
"above". `kalshi.market_strike` now takes the direction from the contract's
wording. Legacy negative strikes (`-TN0.1`) were also being dropped; the 4
affected stored CPI markets were re-derived.

# Round 2 (2026-09-29)

This round asks which uses of prediction markets belong in this framework.
Quoting, arbitrage and market making on the venues themselves need WebSocket
books, order state and capital on the venue, so they live in a separate,
long-running module. The framework's part is research that decides whether
that module is worth building, plus signals for bots that run here.

**Round 2 verdict: none of the three ideas ships.**
- **Kalshi's mispricing disappears at executable prices,** so the case for a
  quoting or arbitrage module is weak.
- **Kalshi's release uncertainty doesn't improve option_IndexVolBot.**
- **Polymarket policy odds don't lead the ETFs they move.**

The one open lead is maker quoting in 2026. It is only an upper bound, and it
is for the external module to check on Kalshi's demo.

## Phase A — the calibration edge at executable prices

This is `scripts/onetime_prediction_market_calibration.py --executable`. It
runs on the same 1,127 KXINX daily ladders as kill test B (2022-04 to
2026-09).
- **Entry:** the first hourly candle after 17:00 ET on the prior day.
- **Settlement:** at the contract's result.
- **Fee:** the current KXINX schedule from the series API (`quadratic`,
  multiplier 1): a taker fee of 0.07·p·(1−p) per contract and no maker fee.
  Legacy INX paid half that.

**Only 48% of contracts had a two-sided quote at entry.** The median spread was
4¢. Across a ladder, the **bids sum to 0.83 and the asks to 1.14**. The
overpricing kill test B found was in *last-trade* prices. At the bid there is
no overround to sell.

| Rule (one $1 contract per signal) | Contracts | Days | Net ¢/contract | t (by day) |
|---|---|---|---|---|
| Sell every YES at the bid | 5,723 | 843 | −3.7 | −24.8 |
| Options fair value, taker, margin 0 | 2,132 | 643 | −2.3 | −3.6 |
| Options fair value, taker, margin 2¢ | 1,004 | 423 | −2.5 | −2.4 |
| Options fair value, taker, margin 5¢ | 465 | 245 | −1.2 | −0.7 |
| Tilt: sell YES bid 0.30–0.55, taker | 316 | 262 | −3.3 | −1.4 |
| Options fair value, maker **upper bound**, margin 2¢ | 3,309 | 819 | −1.2 | −2.4 |
| Options fair value, maker **upper bound**, margin 5¢ | 1,826 | 663 | +0.6 | +0.8 |
| Tilt, maker **upper bound** | 645 | 435 | +1.0 | +0.7 |

"Maker upper bound" means: rest one tick inside the spread, and count a fill
whenever a later hourly candle trades through the quote. Hourly candles don't
show queue position, so this overstates fills.

**Verdict: #3 (fair-value quoting) and #6 (market making) are not supported
by the data.**
- **Every taker rule loses after fees.** That includes the no-model tilt rule.
- **The maker bound is flat overall.** It is positive only in 2026: t +2.2 at
  a 2¢ margin and +3.2 for the tilt, over 9 months. That may be real (deeper
  2026 flow), or it may be the fill model flattering a trending year.
- **What would settle it:** a few weeks of real queue data on Kalshi's demo,
  collected by the external module before it risks capital. Nothing here
  justifies building it for the edge alone.

## Phase B — release uncertainty as an option_IndexVolBot gate

**Idea.** Skip or shrink new SPY condors while Kalshi is unusually unsure about
the next print. Round 3 rejected the calendar blackout ("a 35-DTE condor spans a
release whatever day it opens"); this gate keys on *how uncertain* the release
is, not *when* it is.

**Feature: `event_std_z`** (`prediction_market_features.add_event_uncertainty`)
is the larger of the CPI m/m and U3 ladder z-scores.
- **Why a z-score.** The ladder's std narrows about 30% over the month before
  a release, and its level moves with the regime (CPI median 0.20pp in 2022,
  0.12pp in 2025). So log(std) is z-scored against the trailing 365 days within
  three days-to-release buckets.
- **Point in time.** Bar D sees prices through D−1.
- **Payrolls is left out.** Median open interest is 10 contracts, and its
  std falls every year from 2023 to 2026.
- **How often it is high:** above 1.0 on 33% of days, above 1.5 on 24%, and
  mostly in 2022.

**Walk-forward.** `scripts/onetime_index_vol_backtest.py --pm --calibration original`,
2021-11-26 → 2026-09-28, split 2024-01-01, on top of the live rules (VIX/VIX3M ≤ 1.0).
- It was run before the real-chain recalibration became the default. On real
  SPY prices the bot's own edge is gone
  (`docs/backtests/index-vol-real-surface-2026-09.md`), so a gate on it is moot.

| Variant | H1 t | H1 max DD | H2 t | H2 max DD | Trades |
|---|---|---|---|---|---|
| Live rules | 2.07 | −1.9% | **0.84** | −0.7% | 15 |
| H1 winner: size ×0.5 when z > 1.5 | 2.32 | −0.8% | 0.69 | −0.8% | 15 |
| Size ×0.5 when z > 1.0 | 2.25 | −0.8% | 0.53 | −0.8% | 15 |
| Skip when z > 2.0, size ×0.5 when z > 1.5 | 2.08 | −0.8% | 0.55 | −0.8% | 10 |
| Skip when z > 1.5 | 1.91 | −0.7% | 0.55 | −0.8% | 8 |

The decide path (the live `decide_indexvol` on the synthetic chain) agrees:
live rules H2 t 1.16, the H1 winner 0.95.

**Verdict: does not ship.**
- None of the 11 variants beats the live rules on H2.
- The size-down's H1 gain is a smaller H1 drawdown (−1.9% → −0.8%) on the same
  15 trades. That is 15 trades in five years, so there is not enough to separate
  a signal from one lucky halving.
- `IndexVolRules.max_event_std_z` / `event_size_z` stay `None`.
- The live bot still reads `Market.event_uncertainty()` and logs it on every
  entry check, so the live record accumulates for a re-test.

## Phase C — policy odds vs the ETFs they should move

`scripts/onetime_prediction_event_basket_study.py`.

For macro releases, prediction markets copy deeper markets (fed funds futures,
CPI fixings). Policy events have no other market, so if prediction-market
prices carry anything a daily bot can use, it should show up here.

**Links.** 98 links with 3,764 link-days (2024-01 to 2025-12). Each link is a
hand-curated event → (long ETFs, short ETFs):
- 2024 election, Trump win → KRE+XLE vs TAN+ICLN;
- Mexico/Canada 25% → SPY vs EWW+EWC;
- China 100% → SPY vs FXI;
- China tariff-rate buckets (expected rate) → SPY vs FXI;
- 8 trade-deal events × 17 countries → country ETF vs SPY.

**Method.** dx is the change in odds from one evening to the next. Each link's
dx is z-scored, and the pooled slope has standard errors clustered by week and
by event, keeping the more conservative of the two.

| Link group | n | Same-day slope, bp per 1 sd | t |
|---|---|---|---|
| China tariff rate | 125 | +27.3 | 3.82 |
| Trump trade | 211 | +31.6 | 1.83 |
| Mexico/Canada 25% | 44 | +13.3 | 0.84 |
| China 100% | 14 | +13.6 | 0.51 |
| Trade deals, all countries | 3,370 | +2.8 | 1.06 |

| Pooled | Same day | Next day | 5 days |
|---|---|---|---|
| Full, t | **+2.21** | −1.29 | −1.89 |
| H1 (to 2025-09-04), t | +2.79 | −1.54 | −1.50 |
| H2, t | −0.15 | −0.06 | −1.31 |

**Verdict: does not pass (monitor only).**

- **The mapping is real.** Tariff and election odds move with the ETFs on the
  same day.
- **They don't lead.** By the close the ETFs have priced the move, and the next
  day shows nothing.
- **Not tradable either:** the sign rule on big moves (|z| > 1) earns −0.6 bp
  per link-day.
- **One post-hoc hint.** The China tariff-rate pair *reverses* the next day
  (−22 bp per sd, t −3.4 with weeks as clusters). That is one trade-war episode
  and 27 weeks, found by slicing after the pre-registered test failed, so it is
  a hypothesis, not a bot.
- **Trade-deal markets barely move their country ETFs.** Deals were mostly
  priced in, and a single-country deal is small news for a whole index.
- **No new capture.** Links are hard-coded in the study, and nothing new was
  added to the daily capture registry. A bot would need it; a monitor doesn't.

## The external execution module

These are the integration points for the separate venue-trading module:

- **Alpha report.** `alpha_report.load_worth` finds bots from
  `portfolio_worth.bot_name`, and that column is a foreign key to `bots.name`.
  The daily worth calculator (`calculate_portfolio_worth.py`) prices every
  `bots` row's portfolio JSON and skips a bot that already has a row for the
  day.
  - **The simplest contract:** the module owns one `bots` row (for example
    `PM_KalshiQuoter`) and sets its `portfolio` to `{"USD": <mark-to-market
    NAV>}` each day before the calculator runs. The calculator then records the
    right worth, and the weekly `alphareport` judges it against QQQ with the
    same bar as every bot here.
  - Keep the name out of every copier's `botWeights`: the copiers would read
    the row as a cash position.
- **Shared data, read-only.** `prediction_market_snapshots`, `vol_surface`,
  `option_quotes` and `macro_events` can serve as fair-value inputs. The module
  keeps its own orders, fills and quotes tables.
- **No code coupling.** `utils/kalshi.py` and `utils/polymarket.py` are
  read-only research clients. The module brings its own authenticated WebSocket
  clients.

## Not done / follow-ups

- **Phase 3 (event sizing for the option bots)** was tested in round 2
  (Phase B) and does not ship. `option_IndexVolBot` logs `event_std_z`, so a
  re-test can use live data once there are more than 15 condors.
- **A real test B needs 0–1 DTE SPY chains.** `optionchainsnapshot` starts at
  7 DTE. Adding the nearest expiry to the capture would allow a like-for-like
  digital comparison in a few months.
- **The capture keeps running,** so recession-odds history will lengthen. A
  re-test of a recession-only overlay makes sense once it has two clean years
  beyond 2024.
