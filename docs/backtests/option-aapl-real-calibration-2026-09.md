# AAPL option bots re-priced on real AAPL chains (2026-09-30)

The synthetic harness (`scripts/onetime_option_bots_backtest.py`) prices every
AAPL option with Black-Scholes on an implied-vol proxy. Its three inputs were
set on one live chain (2026-09-25) or assumed:

- **Vol level:** ^VXN scaled by AAPL's realized vol relative to QQQ's.
- **Put skew:** 0.15.
- **Calls:** flat at ATM vol.

This doc checks all three against 2,556 real AAPL day-expiries (21–66 DTE,
2019-05 → 2026-09, from the DoltHub backfill in `option_quotes`). It then
reruns every bot and the walk-forward on the calibrated pricing. This is the
AAPL counterpart of [index-vol-real-surface-2026-09.md](index-vol-real-surface-2026-09.md).

## Data fix first: pre-split AAPL spots were 4× too low

`scripts/onetime_backfill_dolthub_options.py` fetched closes with
`auto_adjust=False`, believing that returns unadjusted prices. yfinance still
**split-adjusts** them. As a result, every AAPL row before the 2020-08-31 4:1
split stored a spot of ~$75 beside strikes around $300.

- **Rows corrected in place:** 12,534 rows over 99 days, via
  `underlying_price × 4` where the price was below $150. The guard makes the
  update idempotent.
- **Script fixed:** it now multiplies back every later split (`split_factor`).
- **SPY:** never split, so it was unaffected.

## Calibration

The fit uses the harness's own smile, `iv = ATM × (1 − skew × z)` with
`z = ln(K/S) / (ATM·√T)`, per day and expiry.

| Input | Harness until now | Real AAPL, 2019–26 |
|---|---|---|
| **ATM IV / proxy** | 1.00 | **0.825** with no report before expiry; 0.94 with one |
| Put skew | 0.15 | 0.16 |
| Call skew | 0 (calls flat at ATM) | 0.04 median; `0.038 + 0.127 · ln(ATM/0.25)`, stable in both halves |
| Earnings | none in the price | a **4.3%** one-sd jump in any expiry spanning a report |

In practical terms:

- **Level:** the proxy made every AAPL option about 17% too expensive. The
  harness's median IV/HV20 falls from 1.24 to **1.02**. Real AAPL implied vol
  barely exceeds realized vol.
- **Calls:** a 25-delta call trades at about 0.93× ATM vol, where the model had
  1.0. This is real but small, and it was the smallest of the three errors.
- **Earnings:** the jump is added as extra total variance in any expiry that
  spans a report. That prices the earnings premium the catalyst bot pays when
  it buys calls into a report.

`--calibration real` is now the default. `--calibration original` reproduces
every earlier doc. Not calibrated, because the backfill has no expiry past
~65 days: the term structure. LEAPs are still priced at 30-day vol.

## Every bot, live rules, 2012-06 → 2026-09 (split 2019-07-31)

| Bot | Sells or buys vol | H2 t: original → real | Full t: original → real |
|---|---|---|---|
| LeapCall | buys calls | 1.98 → 1.91 | 3.13 → 2.85 |
| PMCC | long LEAP, short calls | 2.26 → **1.33** | 3.49 → 2.51 |
| Wheel | sells puts, then calls | 1.68 → 0.85 | 0.86 → 0.13 |
| IronCondor | sells both wings | 0.12 → **−0.59** | 1.06 → 0.22 |
| Collar | long stock, sells calls | 1.09 → 0.98 | 1.36 → 1.18 |
| CreditSpread (paused) | sells | −1.51 → −1.37 | −0.22 → −0.73 |
| CatalystCall | buys calls into earnings | 0.62 → 0.93 | 1.06 → −0.06 |
| Mispricing | both ways | 0.88 → **1.62** | 1.03 → **2.79** |
| AAPL buy & hold | | 1.21 | 0.81 |

**The selling bots lose what the proxy's inflated premium gave them.** The one
bot that also buys cheap vol, Mispricing, gains. Its cheap side buys hedged
straddles, and at real prices those are cheap more often.

**Caveat:** H1 (2012–2019) is priced with a calibration fitted on 2019–2026. It
is out of sample for the calibration, not only for the rules.

## Walk-forward re-tune on the real calibration

Same protocol as before: grid-search on H1, judge on H2, and ship only what
beats the defaults out of sample. "Consensus" is the modal value of each field
across the H1 top 10.

| Bot | Rules | H1 t | H2 t | H2 beta | H2 max DD |
|---|---|---|---|---|---|
| LeapCall | shipped (0.60Δ, 1.5× leverage cap) | 2.32 | **1.91** | 0.43 | −27.6% |
| | consensus | 2.33 | 1.89 | 0.35 | −24.6% |
| PMCC | shipped (0.70Δ LEAP) = H1 #1 | 2.40 | **1.33** | 0.30 | −28.4% |
| | consensus | 2.24 | 1.32 | 0.31 | −24.6% |
| Mispricing | shipped (10-pt gaps, 1.5σ wings, WW hedge) | 2.56 | **1.62** | 0.02 | −19.0% |
| | consensus (1.5σ wings, exit 5 DTE) | 2.87 | 1.14 | 0.04 | −30.9% |
| IronCondor | shipped (20Δ, $35 wings, 60 DTE) | 1.64 | **−0.59** | 0.06 | −27.0% |
| | consensus | 1.70 | −0.63 | 0.06 | −35.4% |
| | textbook defaults | −1.61 | −1.08 | | |
| Wheel | defaults (30Δ put / 30Δ call) | −0.70 | 0.85 | 0.73 | −27.2% |
| | **consensus: 40Δ put, 20Δ call, sell puts only at IV/HV ≥ 1.1** | 0.71 | **1.50** | 0.60 | −28.7% |
| Collar | defaults | 0.71 | 0.98 | 0.45 | −20.2% |
| | consensus (35Δ put, 15Δ call, only when IV is cheap) | 1.24 | 1.57 | **0.66** | −23.3% |

### Verdicts

- **IronCondor: nothing survives.** Only 4% of 192 variants are positive in H2.
  This is the same finding as CreditSpread (paused 2026-09-26) and the SPY
  condor, and it makes IronCondor a pause candidate.
- **Wheel: the consensus passes the walk-forward bar, and ships** (`OptionWheelBot.RULES`, 2026-09-30). It beats the defaults
  in both halves, at lower beta (0.60 vs 0.73), and it makes sense at real
  prices:
  - it sells puts only when IV really is rich, which it usually is not for
    AAPL;
  - it sells covered calls further out, where the cheap call wing no longer
    pays for capping AAPL's upside.
  - Its t of 1.50 is still below 2. It is AAPL beta with some alpha, not an
    edge.
- **Collar: not shipped.** The consensus has a better t, but mostly by
  collaring less often. Beta rises from 0.45 to 0.66, and CLAUDE.md prefers the
  lower-beta version.
- **LeapCall, PMCC, Mispricing: the shipped rules stay.** None of them clears
  t 2 out of sample any more.
  - PMCC fell from 2.37 in the round-2 doc to 1.33.
  - LeapCall is 1.91, and remains mostly AAPL picked with hindsight.
  - Mispricing is the only near-zero-beta result, at 1.62.

## Reproduce

```bash
uv run python scripts/onetime_option_bots_backtest.py                           # real calibration, all bots
uv run python scripts/onetime_option_bots_backtest.py --calibration original    # the earlier docs' numbers
uv run python scripts/onetime_option_bots_backtest.py --tune --bots option_LeapCallBot,option_IronCondorBot,option_MispricingBot,option_WheelBot,option_PMCCBot,option_CollarBot
```
