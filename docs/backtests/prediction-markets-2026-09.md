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

## Not done / follow-ups

- **Phase 3 (event sizing for the option bots) is deferred.** It has no
  backtest path. The capture now stores the CPI ladder (`cpi_next_mean` and
  `cpi_next_std`), which is its input.
- **A real test B needs 0–1 DTE SPY chains.** `optionchainsnapshot` starts at
  7 DTE. Adding the nearest expiry to the capture would allow a like-for-like
  digital comparison in a few months.
- **The capture keeps running,** so recession-odds history will lengthen. A
  re-test of a recession-only overlay makes sense once it has two clean years
  beyond 2024.
