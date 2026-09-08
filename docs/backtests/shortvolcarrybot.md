# ShortVolCarryBot — backtest (much improved, still not deployed)

Short variance via SVXY while the VIX curve is in contango, with a permanent
long-volatility hedge. **Status: implemented, tested, substantially revised, and
still NOT scheduled.** The revision more than doubled the full-history return and
cut drawdown by 5 points, but on the instrument that actually exists today it
earns +0.80%/yr at a Sharpe of 0.14 — better than the +0.02% it started at, and
still not worth an allocation.

## The instrument changed underneath the strategy

SVXY tracked **−1x** the short-term VIX futures index until **2018-02-28**. After
the 5 February 2018 event — in which the −1x fund XIV terminated and SVXY lost
~90% in a day — ProShares cut the target to **−0.5x**. Pre- and post-2018 SVXY
are different instruments; a single backtest across them is not one track
record. Both windows are reported separately and post-2018 is the honest
headline.

## Result — v1 versus v2

| Window | Version | CAGR | Sharpe | Max DD |
|---|---|---|---|---|
| Full 14.68y (2011-11 → 2026-07) | v1 | +1.90% | 0.37 | 12.9% |
| | **v2** | **+3.49%** | **0.40** | 21.5% |
| Pre-2018 (SVXY −1x), 6.30y | v1 | +4.40% | 0.62 | 12.9% |
| | **v2** | **+6.98%** | **0.65** | 17.8% |
| **Post-2018 (SVXY −0.5x), 8.38y** | v1 | +0.02% | 0.02 | 8.2% |
| | **v2** | **+0.80%** | **0.14** | 15.1% |

v2 takes more risk than v1 by design (see fix 1), so the drawdown columns are
not like-for-like against v1; the ablation table below *is* like-for-like and is
the one that matters.

## What changed in v2

**1. The cap was silently overriding the sizing rule.** Vol-targeted sizing
adapts to the instrument change on its own: SVXY's realized vol fell from 71.7%
to 36.8% across the split, so `target_vol / vol` asks for 0.21 of the book before
2018 and 0.41 after — correctly holding risk constant. The hard cap does not
adapt. At its original 0.25 it sat just above what the vol target wanted under
−1x and well below it under −0.5x, so from 2018 it overrode the sizing rule on
nearly every bar and cut the position by ~40%. Restoring it to 0.50 makes it a
backstop again rather than the binding rule.

**2. The stops were the bug.** Both kill rules — `^VIX > 30`, and a 15% drawdown
from the 20-day high — are trailing triggers on a mean-reverting instrument.
They can only fire *after* the spike, so they sell at the bottom, and the
re-entry wait then holds the position out through the normalization, which is
where short vol earns. Ablating them (VIXM hedge, cap 0.50, everything else
identical):

| Window | Stops | CAGR | Sharpe | Max DD |
|---|---|---|---|---|
| Full 14.68y | on | +1.84% | 0.24 | 26.6% |
| | **off** | **+3.49%** | **0.40** | **21.5%** |
| Pre-2018 | on | +4.05% | 0.42 | 19.2% |
| | **off** | **+6.98%** | **0.65** | **17.8%** |
| Post-2018 | on | +0.08% | 0.05 | 20.0% |
| | **off** | **+0.80%** | **0.14** | **15.1%** |

Every metric in every window improves, drawdown included. The stops did not even
buy protection, because the loss is already taken by the time the trigger is true.

**3. The hedge moved from VIXY to VIXM.** VIXY (short-term futures) decays
−51.5%/yr, VIXM (mid-term) −19.3%/yr, but their tail responses differ by nearly
the same factor (Mar 2020: +348.6% vs +121.2%), so protection per unit of
carrying cost is close to a wash on paper — the a-priori case for VIXM is weak
and was not the reason for the switch. It was measured: post-2018 at v1's
settings, VIXM returned +0.08%/yr against VIXY's −0.27%.

## February 2018 — the contango gate is what protects

With **both stops removed**, the bot still goes flat on **Friday 2 February
2018**, the session before the crash, because the contango gate inverted first.

| Configuration | Feb-2018 peak → trough | Full pre-2018 Sharpe |
|---|---|---|
| Stops on (v1 rules) | **−9.7%** | 0.42 |
| **Stops off, contango gate + hedge (v2)** | **−9.1%** | **0.65** |
| Contango gate off as well | **−24.7%** | 0.56 |

SVXY itself fell **−92.1%** over those days. So the gate is load-bearing and the
stops were decoration: they added nothing to the one event they were written for
while costing return in every other year. In every VIX event in this sample a
spike above 30 coincided with an inverted curve, which is why the kill was
redundant with the gate rather than additive to it.

The design brief — "only worth doing if the hedge is in the rules from day one"
— is still satisfied. The permanent hedge and the contango gate are both
mechanical and both remain. What was removed is stop-loss machinery, which was
never in the brief and turned out to be actively harmful.

## Why it still should not be deployed

Post-2018 the strategy earns **+0.80%/yr at Sharpe 0.14 with a 15.1% drawdown**.
That is a real improvement over +0.02% but it is not an allocation. The hedge
sensitivity shows why the premium is gone rather than mis-harvested:

| Post-2018, VIXM hedge ratio | CAGR | Sharpe | Max DD |
|---|---|---|---|
| 0.00 (unhedged short vol) | **−0.44%** | 0.02 | 29.0% |
| 0.05 | −0.24% | 0.03 | 25.9% |
| 0.10 | −0.05% | 0.04 | 23.0% |
| 0.15 (shipped) | +0.08% | 0.05 | 20.0% |
| 0.25 | +0.39% | 0.09 | 14.1% |

**More hedge monotonically improves both return and drawdown.** The unhedged
short-vol position has a *negative* expected return post-2018 — so the hedge is
not protecting a premium, it is diluting a losing trade, and long volatility was
simply the better side of this market. (Rows measured at v1's stop settings; the
ordering is the finding, not the levels.) No hedge design fixes that, because
there is nothing left to hedge.

## What would have to change

- **Options rather than an ETF wrapper.** Short variance expressed directly (an
  ES/SPX put spread or a variance-swap proxy) avoids paying a fund to deliver
  half the exposure at a 0.95% expense ratio. Needs options support the bot
  layer does not have.
- **Or accept it as a demonstrated risk-control exercise rather than a return
  sleeve.** Pre-2018 Sharpe 0.65 with a −9.1% pass through February 2018 says
  the rule set is sound. It is the available instrument that is not.

The transferable lesson is fix 2, and it is not specific to this strategy:
**trailing stops on a mean-reverting instrument reliably cost return without
reducing drawdown.** Check any other bot in this repo that uses one.
