# Option bots round 2 on AAPL: mispricing, wheel, PMCC, collar, earnings calendar (2026-09)

Same harness, same caveats and same walk-forward protocol as
[option-bots-2026-09.md](option-bots-2026-09.md):
- Script: `scripts/onetime_option_bots_backtest.py`.
- Window: 2012-06-01 → 2026-09-25.
- Protocol: parameters chosen on H1 (2012–2019) and judged on H2 (2019–2026).
  A change ships only if the H1 winner beats the a-priori defaults on H2.
- Scoring: alpha against QQQ, with its t-stat.

**The options here are priced, not observed.** Every price comes from
Black-Scholes on a ^VXN-based implied-vol proxy with AAPL put skew 0.15. The
decisions come from the live bots' rule functions (`utils/option_rules.py`).

What round 2 added to the harness:
- The book can hold shares, which fill at S ± 0.05%.
- Expiry can settle physically, as it does for the wheel.
- The book can delta-hedge, as the mispricing bot's straddle does.

`option_EarningsCalendarBot` is not in this document. The proxy has no earnings
implied vol, so its trade cannot be priced at all.

## The pricing-mismatch signal is real

The mispricing bot's fair vol is:
- a HAR-RV forecast of AAPL's realized vol over the trading days to expiry;
- fitted with earnings-reaction days removed;
- plus the historical RMS earnings move when a report falls inside the expiry.

The live bot and the backtest compute it in exactly the same way. Before any
trading, the table below asks whether the gap between IV and fair vol predicts
the variance premium that follows: IV minus the vol AAPL actually realizes over
the next 21 days.

| IV − fair (vol pts) | Days | Mean IV | Mean fair | Realized next 21d | IV − realized, mean | Share IV > realized |
|---|---|---|---|---|---|---|
| < −3 | 263 | 22.3% | 27.7% | 22.1% | +0.2 | 49% |
| −3 … 0 | 504 | 22.7% | 24.1% | 21.2% | +1.5 | 59% |
| 0 … 3 | 743 | 25.5% | 24.0% | 23.5% | +2.0 | 72% |
| 3 … 6 | 694 | 28.7% | 24.4% | 25.6% | +3.1 | 73% |
| 6 … 10 | 611 | 33.3% | 25.4% | 26.1% | +7.3 | 81% |
| 10 … 15 | 432 | 38.6% | 26.3% | 30.4% | +8.1 | 83% |
| > 15 | 332 | 49.6% | 29.0% | 35.6% | +14.0 | 87% |

What the table shows:
- The gap predicts the variance premium monotonically (correlation 0.38).
- HAR is a better forecast than plain 20-day HV: mean absolute error 7.1 vs 8.8
  vol points.
- "Rich" options really are rich.
- "Cheap" options are only fairly priced: the variance premium never goes
  negative, it just vanishes.

The median gap is +4 points. That is the ordinary variance risk premium, and the
bot's threshold sits above it.

## Walk-forward results

| Bot | Grid | Defaults, H2 t | H1 winner, H2 t | Top-10 mean H2 t | Grid with H2 t > 0 | Shipped |
|---|---|---|---|---|---|---|
| option_MispricingBot | 144 | −0.18 | **0.82** | 0.76 | 33% | Yes. Rich gap 6 → 10 pts, cheap gap 3 → 2, wings 1.0σ → 1.5σ, exit 10 → 5 DTE. |
| option_WheelBot | 96 | 1.76 | 0.85 | 1.36 | 100% | No: the H1 pick loses to the defaults on H2. |
| option_PMCCBot | 96 | 1.46 | **2.37** | 2.18 | 100% | Yes. LEAP delta 0.80 → 0.70. |
| option_CollarBot | 108 | 1.18 | 1.14 | 1.59 | 100% | No, by a hair. Wearing the collar "always" beat "below_sma200" and "iv_cheap" in every top-5 row. |

## The rules now live

| Bot | Window | CAGR | Alpha/yr | t | Beta | Corr | Max DD | Trades |
|---|---|---|---|---|---|---|---|---|
| option_MispricingBot | full | +4.5% | +3.0% | 0.93 | 0.11 | 0.18 | -43.8% | 79 |
| option_MispricingBot | H1 | +2.9% | +2.2% | 0.56 | 0.06 | 0.10 | -26.8% |  |
| option_MispricingBot | H2 | +6.2% | +4.2% | 0.82 | 0.13 | 0.22 | -26.7% |  |
| option_WheelBot | full | +18.4% | +3.3% | 0.86 | 0.77 | 0.75 | -38.8% | 238 |
| option_WheelBot | H1 | +11.9% | -2.9% | -0.51 | 0.87 | 0.67 | -38.8% |  |
| option_WheelBot | H2 | +25.2% | +8.5% | 1.76 | 0.73 | 0.81 | -26.8% |  |
| option_PMCCBot | full | +18.5% | +13.5% | 3.53 | 0.22 | 0.31 | -19.2% | 210 |
| option_PMCCBot | H1 | +17.2% | +14.0% | 2.81 | 0.15 | 0.18 | -19.2% |  |
| option_PMCCBot | H2 | +19.8% | +13.7% | 2.37 | 0.26 | 0.38 | -19.1% |  |
| option_CollarBot | full | +13.6% | +4.0% | 1.39 | 0.48 | 0.67 | -26.1% | 58 |
| option_CollarBot | H1 | +11.7% | +3.3% | 0.83 | 0.46 | 0.57 | -26.1% |  |
| option_CollarBot | H2 | +15.6% | +4.9% | 1.18 | 0.49 | 0.73 | -21.1% |  |
| option_LeapCallBot (for comparison) | full | +23.6% | +18.9% | 3.17 | 0.24 | 0.22 | -24.4% | 31 |
| option_LeapCallBot (for comparison) | H2 | +24.5% | +18.6% | 2.10 | 0.28 | 0.28 | -24.4% |  |
| AAPL buy & hold | full | +23.4% | +4.2% | 0.85 | 1.01 | 0.74 | -43.8% | - |

**Zero-cost sensitivity** (`OPTION_COST_SCALE=0`, full-window alpha t):

| Bot | At mid | With costs |
|---|---|---|
| Mispricing | 1.69 | 0.93 |
| Wheel | 0.88 | 0.86 |
| PMCC | 3.94 | 3.53 |
| Collar | 1.58 | 1.39 |

Execution costs eat almost half of the mispricing bot's alpha, and little of
the others'.

## What the numbers mean

**option_MispricingBot: the signal works; the trade that expresses it barely
does.**
- The gap does find rich options. But the defaults sold an at-the-money iron
  butterfly at a 6-point gap, and that lost −14%/yr in H1 with a −76% drawdown.
- The cause is jumps. When AAPL's IV is rich, a short at-the-money position on
  one stock gets run over by single-day moves: 2019-01-03, a −10% guidance cut,
  cost 15% of the book in one day.
- The re-tune (sell only at 10+ points, wings at 1.5σ) turned it into +4%/yr
  at t 0.82 out of sample, with beta 0.1.
- The long-vol side is a delta-hedged straddle when IV sits 2+ points under
  fair. It is roughly flat with near-zero beta, which matches the table above:
  "cheap" options carry no premium to capture.
- Unproven. The live version has three things the backtest cannot test:
  AAPL's own IV (the backtest only has the index proxy), the news check, and
  the smile.

**option_WheelBot is AAPL with a lower beta, not an edge.**
- Its beta is 0.77.
- Alpha is −2.9%/yr in H1 and +8.5% in H2, the half in which AAPL itself had
  +9.7% alpha.
- It kept the defaults, because the H1 winner did worse on H2.
- Expect it to track AAPL more smoothly, not to beat QQQ.

**option_PMCCBot is the best result in either round, with the usual caveat.**
- Against `option_LeapCallBot` it has lower alpha (+13.5% vs +18.9%/yr), but
  half the beta (0.22), a smaller drawdown (−19% vs −24%) and a higher t: 3.53
  vs 3.17 over the full window, 2.37 vs 2.10 on H2.
- So selling calls against the LEAP pays in risk-adjusted terms.
- All 96 variants stayed positive out of sample.
- It is still long AAPL, which was chosen with hindsight, so the LEAP bot's
  caveat applies in full.

**option_CollarBot does what a hedge does.**
- It roughly halves beta (0.48) and cuts the drawdown from −44% to −26%.
- It gives up about 10 points of CAGR against holding the stock.
- Alpha t of 1.39 is not significant.
- A collar worn all the time beat wearing it only below the SMA200 or only
  when puts were cheap.

**option_EarningsCalendarBot is live-only.**
- It sells the front expiry's earnings IV when the move that IV implies is at
  least AAPL's historical earnings move, and when the front/back IV ratio is at
  least 1.15.
- The `option_quotes` history it builds is the only evidence it will get.

## Not built, and why

- **0DTE SPX credit spreads:** a once-a-day cron cannot manage intraday gamma.
- **Box spreads:** they earn the risk-free rate, which is not alpha, and AAPL
  options are American, so they can be assigned early.
- **Dispersion:** needs index and many single-name chains, with history for
  both.
- **Tail hedges** (far-OTM puts, VIX calls): negative carry by design. As a
  standalone bot the alpha is negative in every year without a crash.
- **Naked strangles and straddles:** undefined risk; the margin check refuses
  them. The mispricing bot's butterfly is the defined-risk version.
- **Long straddles into earnings:** IV crush. The calendar bot takes the other
  side of that trade.

## Reproduce

```bash
uv run python scripts/onetime_option_bots_backtest.py                    # every bot, live rules
uv run python scripts/onetime_option_bots_backtest.py --only option_PMCCBot,option_LeapCallBot
uv run python scripts/onetime_option_bots_backtest.py --tune --bots option_MispricingBot,option_WheelBot,option_PMCCBot,option_CollarBot
OPTION_COST_SCALE=0 uv run python scripts/onetime_option_bots_backtest.py  # fills at mid
```
