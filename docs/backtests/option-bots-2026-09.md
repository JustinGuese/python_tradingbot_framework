# Option bots on AAPL: synthetic backtest and walk-forward re-tune (2026-09)

Script: `scripts/onetime_option_bots_backtest.py`. Window 2012-06-01 → 2026-09-24.
The walk-forward split is 2019-07-29:
- **H1** (2012–2019) is where parameters are chosen;
- **H2** (2019–2026) is where they are judged.

Each bot starts with $100k. Alpha is measured against QQQ, as CLAUDE.md requires.
The script reads each bot's `RULES` class attribute, so the backtest always runs
the live rules.

## Read this first: the options are priced, not observed

yfinance has no historical option chains, so every option here is priced with
Black-Scholes (`utils/option_math.py`) on an implied-vol proxy. The decisions
come from the live bots' own rule functions (`utils/option_rules.py`); only the
prices are synthetic.

- **IV proxy.** `^VXN × trailing-252d median(HV30_AAPL / HV30_QQQ)`: real
  Nasdaq-100 implied vol, scaled by AAPL's relative realized vol.
  - It carries a real variance premium. On 73% of days it exceeds AAPL's
    realized vol over the following 21 days, by +5 vol points (median), in both
    halves.
  - So the premium-selling bots were not set up to fail by the proxy.
- **Skew (`--skew`, default 0.15).**
  - Formula: `iv(K) = atm × (1 − 0.15·z)` below the money, flat above, where
    `z = ln(K/S)/(atm·√T)`.
  - Calibration: the live AAPL chain on 2026-09-25 showed −0.15 to −0.21 on the
    30–85-day puts, with calls roughly flat. 0.15 is the conservative end.
  - `--skew 0` reproduces the flat-vol numbers from the first version.
- **Not modelled:**
  - earnings IV run-up and crush (the catalyst bot's thesis);
  - term structure (a LEAP is given 30-day vol and its daily swings, which
    makes its beta look lower than it is);
  - American exercise;
  - the dividends included in the auto-adjusted price path.
- **Execution:**
  - strikes on a 1.25%-of-spot grid;
  - spread widths as a share of spot ($10 at $336 = 3%);
  - each leg fills at mid ± max($0.025 at $336, 1.5% of premium).

## The original rules (skew 0.15)

| Bot | H1 alpha, t | H2 alpha, t | Full CAGR | Max DD |
|---|---|---|---|---|
| option_LeapCallBot (0.70Δ, 3% exit buffer, no trim) | +19.4%, 1.78 | +19.9%, 1.78 | +27.5% | -38.9% |
| option_CreditSpreadBot (0.30Δ, $10, 35 DTE, 50% / 2x / 21 DTE) | -5.5%, -0.94 | -12.5%, -1.95 | -5.2% | -71.8% |
| option_IronCondorBot (0.16Δ, $10, 35 DTE, 50% / 2x / 21 DTE) | -4.2%, -1.14 | -11.0%, -2.42 | -4.3% | -51.9% |
| option_CatalystCallBot | +2.9%, 1.30 | +3.3%, 0.58 | +3.7% | -29.8% |

## Walk-forward re-tune

Method (the same as `retune-2026-09.md`):
- Grid-search economically motivated levers on H1 and rank by H1 alpha t.
- A change ships only if the H1 winner beats the original rules on H2.

Two rounds were run:
1. **Round 1** covered LEAP delta, leverage and trim; exit buffer; IV gate;
   short delta; DTE; profit target; stop; exit DTE; trend side; and the
   condor's call delta.
2. **Round 2** added spread width. The zero-cost run below showed the $10
   spreads barely collecting the variance premium: the long wing buys back
   most of the vol the short leg sells.

Two runs are also a second look at the data, which weakens the evidence.

**Diagnosis before round 2.** With zero transaction costs, the original spread
rules only break even: credit spread alpha −0.7%/yr at t −0.15, condor −1.9% at
t −0.63. So costs are not the whole problem; the narrow structure is.

| Bot | Grid | Result on H2 (out of sample) | Shipped |
|---|---|---|---|
| option_LeapCallBot | 72 | Winner: 0.60Δ, no buffer, trim to 1x above 1.5x. **t 2.10 vs 1.78**, max DD −24% vs −35%. Top-10 mean t 2.02; 100% of the grid positive. | **Yes** |
| option_IronCondorBot | 384 + 192 | Winner: 0.20Δ, **$35 wings**, 60 DTE, 75% take-profit, exit at 7 DTE. **t 0.11 vs −2.42**, +2.5%/yr, max DD −19% vs −44%. Top-10 mean t 0.26; 19% of the grid positive. | **Yes**: it stopped losing, but found no edge |
| option_CreditSpreadBot | 128 + 192 | Every H1 winner loses on H2. Best by rule: $35 wide, 75% take-profit, no stop, exit at 7 DTE: **t −1.50 vs −1.95**. 1–6% of the grid positive. | Yes, being less bad by rule. **Recommend pausing.** |
| option_CatalystCallBot | — | Not tuned: 10 trades in 14 years is nothing to fit. | — |

## The rules now live (skew 0.15)

| Bot | Window | CAGR | Alpha/yr | t | Beta | Corr | Max DD | Trades |
|---|---|---|---|---|---|---|---|---|
| option_LeapCallBot | full | +23.4% | +18.8% | 3.16 | 0.24 | 0.22 | -24.4% | 31 |
| option_LeapCallBot | H1 | +22.6% | +19.9% | 2.51 | 0.15 | 0.11 | -23.5% |  |
| option_LeapCallBot | H2 | +24.3% | +18.5% | 2.10 | 0.28 | 0.28 | -24.4% |  |
| option_CreditSpreadBot | full | +4.9% | -0.9% | -0.22 | 0.35 | 0.42 | -49.0% | 76 |
| option_CreditSpreadBot | H1 | +14.1% | +8.8% | 1.84 | 0.28 | 0.33 | -19.3% |  |
| option_CreditSpreadBot | H2 | -3.6% | -10.1% | -1.50 | 0.38 | 0.46 | -49.0% |  |
| option_IronCondorBot | full | +4.5% | +2.8% | 1.06 | 0.10 | 0.21 | -19.0% | 25 |
| option_IronCondorBot | H1 | +6.5% | +5.5% | 2.20 | 0.06 | 0.14 | -7.2% |  |
| option_IronCondorBot | H2 | +2.5% | +0.5% | 0.11 | 0.12 | 0.24 | -19.0% |  |
| option_CatalystCallBot | full | +3.7% | +2.8% | 0.92 | 0.07 | 0.13 | -29.8% | 10 |
| option_CatalystCallBot | H1 | +2.9% | +2.9% | 1.30 | 0.01 | 0.03 | -6.0% |  |
| option_CatalystCallBot | H2 | +4.4% | +3.3% | 0.58 | 0.10 | 0.16 | -29.8% |  |
| AAPL buy & hold | full | +23.3% | +4.2% | 0.83 | 1.01 | 0.74 | -43.8% | - |

## What the numbers mean

**option_LeapCallBot is profitable, but its "alpha" is mostly AAPL.** The re-tune
did real work: trimming at 1.5x removes the leverage creep, which roughly halves
beta and drawdown for the same return per unit of risk. But the underlying was
picked in 2026, knowing it compounded 23%/yr, and the proxy understates a
LEAP's beta. Before the re-tune, measured against AAPL instead of QQQ, its
alpha was +11.5%/yr at t 1.82. Expect the live record to look like trend-filtered
AAPL at about 1x, not like an 18%/yr edge.

**option_IronCondorBot now roughly breaks even with little market exposure**
(beta 0.1). Wider wings and longer expiries let it keep more of the variance
premium, and the late exit gives trades time to work. There is still no
statistically meaningful edge.

**option_CreditSpreadBot cannot be made profitable in this model.** 320
variants were tried, and no H1 pick survives H2. A single vertical on a
single stock is a directional bet with a thin premium: in 2019–2026 the bear
call spreads fought AAPL's rally and the bull put spreads ate the 2020 and 2022
drawdowns. The recommendation is to pause it (`suspend: true`) and let the
condor carry the premium-selling idea.

**option_CatalystCallBot is unproven.** Its thesis is earnings IV, which this
backtest cannot price.

Round 2 (mispricing, wheel, PMCC, collar, earnings calendar):
[option-bots-round2-2026-09.md](option-bots-round2-2026-09.md).

## Next evidence

The live paper record, plus the real bid/ask, IV and skew that `option_quotes`
accumulates, is the only honest test of any of these. Re-run the alpha check on
live `portfolio_worth` once there are 120+ days.

Reproduce:

```bash
uv run python scripts/onetime_option_bots_backtest.py            # live rules
uv run python scripts/onetime_option_bots_backtest.py --tune     # the walk-forward grids
OPTION_COST_SCALE=0 uv run python scripts/onetime_option_bots_backtest.py  # fills at mid
```
