# MultiAssetCarryBot — backtest (NEGATIVE RESULT, not deployed)

FX forward carry (six CurrencyShares ETFs, ranked on measured carry) plus a bond
roll-yield leg (IEF/TLT, held while `^TNX − ^IRX > 0`), each vol-targeted.

**Status: implemented, tested, and deliberately NOT scheduled.** It loses money.

## Result

`BACKTEST_PERIOD="max"`, adjusted daily closes, default cost model.

| | |
|---|---|
| Window | 2007-03-23 → 2026-09-04 (**19.45 years**, 4,852 bars) |
| Total return | **−38.8%** |
| CAGR | **−2.49%** |
| Sharpe | **−0.41** |
| Max drawdown | **48.5%** |
| Annualized vol | 5.8% |
| Trades | 7,314 |

Losing 48.5% peak-to-trough on a 5.8%-vol book is the worst possible shape: no
return, and the drawdown still arrives.

### Both sleeves lose independently

| Configuration | Total | CAGR | Sharpe | Max DD |
|---|---|---|---|---|
| Both (as shipped) | −38.8% | −2.49% | −0.41 | 48.5% |
| FX sleeve only | −9.8% | −0.53% | −0.27 | 13.2% |
| Bond sleeve only | −21.7% | −1.25% | −0.26 | 23.8% |
| FX only, momentum filter disabled | −25.7% | −1.51% | −0.67 | 26.3% |

So this is not one broken leg dragging down a good one.

## Why it fails — the part worth keeping

**1. Long-only does not merely weaken carry, it can invert it.** This is the
important finding, and it is the structural difference from trend. Trend's long
half is independently profitable, so dropping the short side costs return but
leaves a strategy. Carry's premium *is* a long-short spread: KMP2018 is long
high-carry and short low-carry. Long-only, the alternative to holding a foreign
currency is holding **USD cash**, so the sleeve is implicitly short USD carry at
all times. Across 2007–2026 the USD spent much of the period at or near the top
of the developed-market rate range while the dollar strengthened substantially —
so "long the best of six non-USD currencies" was a losing trade *regardless of
how well the ranking worked*. The ranking picks the best member of a bad set.

**2. The bond leg confuses roll yield with expected return.** `^TNX − ^IRX > 0`
correctly says the roll pays, and it is true that a Treasury future's roll yield
is the term spread net of financing. But the signal is silent on duration, and
duration swamped the roll in 2022 (TLT fell roughly 50% peak-to-trough). An
upward-sloping curve is not a forecast that rates will not rise.

**3. The FX carry estimator is noisier than it looks.** The construction —
`return(FXi) − spot_return(pair_i)`, whose difference is the foreign deposit rate
minus a fee that cancels in a cross-sectional rank — is sound in principle and
needs no rate feed. Measured on current data it produces a sensible *ordering*
(GBP and EUR at the top, JPY and CHF at the bottom, matching policy rates), which
also confirms the invert flags on the three USD-first pairs are correct. But the
*levels* are wrong by percentage points: a recent 63-day estimate put JPY at
−6.9% against an actual policy rate near 0.5%. The likely cause is a timing
mismatch — the ETF marks at the 4pm ET NAV while Yahoo's `=X` quote is a 24-hour
FX close — and the error is largest for JPY, where the Asian session moves after
the US close. Fine for ranking, not fine for any use that reads the magnitude.

## What would have to change

Not tuning. The constraint is structural, so the honest options are:

- **Signed exposure in the core** (negative weights, gross > 1), which makes the
  actual long-short carry trade expressible. This is the same prerequisite listed
  for a literal MOP2012 trend sleeve.
- **Or measure carry against USD cash explicitly** and hold cash whenever no leg
  out-carries it — which, over most of this window, means holding cash almost
  always, i.e. not a strategy.

Do not re-attempt long-only multi-asset carry without addressing point 1. The
strategy is not underperforming because of parameter choices.
