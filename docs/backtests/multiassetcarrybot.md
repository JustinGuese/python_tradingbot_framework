# MultiAssetCarryBot — backtest (NEGATIVE RESULT, not deployed)

FX forward carry (six CurrencyShares ETFs) plus a bond roll-yield leg (IEF/TLT),
each vol-targeted, each gated on carry actually exceeding the alternative.

**Status: implemented, tested, twice revised, and deliberately NOT scheduled.**
It still loses money. The second revision cut the loss roughly in half and the
drawdown by 21 points, which is worth recording precisely because it was not
enough — the residual loss is where the structural argument lives.

## Result

`BACKTEST_PERIOD="max"`, adjusted daily closes, default cost model.

| | v1 (ranked top-3) | **v2 (gate vs USD cash)** |
|---|---|---|
| Window | 2007-03-23 → 2026-09-04 (19.45y) | same |
| Total return | −38.8% | **−23.3%** |
| CAGR | −2.49% | **−1.36%** |
| Sharpe | −0.41 | **−0.28** |
| Max drawdown | 48.5% | **29.2%** |
| Annualized vol | 5.8% | 4.6% |
| Trades | 7,314 | 2,927 |

## What changed in v2, and how much each change was worth

Two fixes, both aimed at a diagnosed error rather than at the metric:

1. **The carry gate is now absolute, not cross-sectional.** v1 ranked the six
   currencies and held the top three unconditionally. For a long-only book the
   alternative to a foreign currency is USD cash, so v1 was structurally short
   USD carry. v2 holds a currency only when its measured carry exceeds the
   ^IRX cash return over the same window plus a 50bp margin.
2. **The measurement window went from 63 days to 252.** Required by fix 1: a
   threshold test needs a level, and the estimator's mean absolute error against
   known 3-month market rates is 4.4pp at 63 days versus 2.1pp at 252.

Ablating them (all runs are v2 code with one change reverted, so the middle row
is not literally v1 — it retains v2's bond momentum filter):

| Configuration | Total | CAGR | Sharpe | Max DD |
|---|---|---|---|---|
| v2 as shipped | −23.3% | −1.36% | −0.28 | 29.2% |
| gate disabled, 252d window | −25.9% | −1.53% | −0.27 | 40.7% |
| gate disabled, 63d window | −42.6% | −2.81% | −0.51 | 50.5% |

Most of the gain came from the longer window and from the gate's effect on
turnover and drawdown, not from the gate's return contribution. That is a
warning sign, not a success: the fix worked as designed and the strategy is
still negative.

### Both sleeves still lose independently

| Configuration | CAGR | Sharpe | Max DD | Vol |
|---|---|---|---|---|
| Both (as shipped) | −1.36% | −0.28 | 29.2% | 4.6% |
| FX sleeve only | −1.29% | **−0.62** | 24.7% | 2.1% |
| Bond sleeve only | −0.70% | −0.11 | 16.5% | 5.1% |
| Bond only, 126d momentum | −0.26% | −0.03 | 17.1% | 4.9% |

The correctly-gated FX sleeve has the *worst* Sharpe of any configuration
tested. Combining the two sleeves is worse than the bond sleeve alone.

## Why it fails — the part worth keeping

**1. Long-only carry is spot-risk-dominated, and that is not fixable by
gating.** This is the finding, and v2 is what establishes it. Carry's premium is
a long-short spread: in KMP2018 the long and short legs' spot moves largely
cancel, leaving the rate differential. Long-only, you absorb the full ~10%
annualized spot volatility of a currency to earn a 1–3% differential — roughly
5:1 risk to signal, before the differential is even measured. Fixing *when* to
take that trade (v2) cannot fix the ratio of what you earn to what you risk. v1
was reliably negative; v2 is less reliably negative.

**2. USD cash was a genuinely strong competitor over this window.** Through
2023-24 with the 3-month bill at 5.24%, none of the six currencies out-carried
cash — the best, CAD, measured +1.47%. v1 held three of them anyway. v2 correctly
sits in cash there, which is why its drawdown fell 21 points.

**3. Cash earns 0% in this backtest, which flatters nothing here but is worth
stating.** The framework does not credit interest on the USD residual. v2 holds
more cash than v1, so its true return is understated by roughly the T-bill yield
times time-in-cash — on the order of +0.5 to +1.0%/yr over this period. Even
crediting that generously, the strategy lands near flat with a 29% drawdown.

**4. The bond leg confuses roll yield with expected return.** `^TNX − ^IRX > 0`
says the roll pays and is silent on duration, which swamped it in 2022. v2 adds
the same 12-month momentum filter the FX legs use — a real bug fix, since v1
applied the "not optional" crash filter to FX only — and the bond sleeve
improves from −1.25% to −0.70%, but does not turn positive.

## What would have to change

Not tuning, and no longer "measure carry against cash" either — that has now
been tried and measured.

- **Signed exposure in the core** (negative weights, gross > 1), which makes the
  actual long-short carry trade expressible. Same prerequisite as a literal
  MOP2012 trend sleeve. This is the only change that addresses point 1.
- **A better-instrumented carry measurement** would help the ranking but not the
  risk ratio, so it is second-order until signed exposure exists.

Do not re-attempt long-only multi-asset carry. Two independent designs, one
naive and one correctly gated, both lose over 19 years, and the reason is the
long-only constraint rather than the parameters.
