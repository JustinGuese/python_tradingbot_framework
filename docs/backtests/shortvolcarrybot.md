# ShortVolCarryBot — backtest (risk controls work; the return does not)

Short variance via SVXY while the VIX curve is in contango, with a permanent VIXY
tail hedge and a hard kill on `^VIX > 30` or a 15% drawdown from the 20-day high.

**Status: implemented, tested, and deliberately NOT scheduled.** Post-2018 it
earns essentially nothing.

## The instrument changed underneath the strategy

SVXY tracked **−1x** the short-term VIX futures index until **2018-02-28**. After
the 5 February 2018 event — in which the −1x fund XIV terminated and SVXY lost
~90% in a day — ProShares cut the target to **−0.5x**. Pre- and post-2018 SVXY are
different instruments and a single backtest across them is not one strategy's
track record. Both windows are therefore reported separately, and the post-2018
one is the honest headline.

| Window | Years | Total | CAGR | Sharpe | Max DD | Vol | Trades |
|---|---|---|---|---|---|---|---|
| Full 2011-11-10 → 2026-07-17 | 14.68 | +31.8% | +1.90% | 0.37 | 12.9% | 5.5% | 1,144 |
| **Pre-2018 (SVXY −1x)** 2011-11-10 → 2018-02-27 | 6.30 | +31.1% | **+4.40%** | **0.62** | 12.9% | 7.4% | 587 |
| **Post-2018 (SVXY −0.5x)** 2018-02-28 → 2026-07-17 | 8.38 | +0.2% | **+0.02%** | **0.02** | 8.2% | 3.5% | 476 |

Every dollar the strategy ever made, it made under the −1x instrument that no
longer exists. Under the fund as it actually trades today, 8.4 years produced
0.2% total.

## February 2018: the risk controls did their job

This is the part worth keeping, and it is checkable rather than asserted. Tracing
the equity curve through the event:

| Date | Equity |
|---|---|
| 2018-02-01 | 13,436 |
| **2018-02-02** | **13,114** ← exits to cash |
| 2018-02-05 … 2018-03-09 | 13,114 (flat, no position) |

The bot went flat on **Friday 2 February**, the session *before* the Monday crash,
and the re-entry rule then held it out for over a month.

- Drawdown from its prior peak: **−4.9%**
- SVXY itself over the same days: **−92.1%** (243.34 → 19.16)

So the design brief — "only worth doing if the hedge is in the rules from day
one" — is satisfied and demonstrated against the worst day in the strategy's
history. The contango gate, the permanent hedge and the kill are not decoration.

## Why it still should not be deployed

The risk management works; the premium is gone. Three things compound:

1. **Half the exposure.** −0.5x instead of −1x halves the harvested premium while
   the strategy's costs and the hedge's drag are unchanged.
2. **The hedge is a permanent 15% of the sleeve** in a decaying long-vol
   instrument. That is the correct design and it is not free — against a halved
   premium it consumes most of what is left.
3. **The caps bind constantly.** `max_short=0.25` means the book is at most a
   quarter invested, so realized vol is 3.5% and there is little to compound.

A Sharpe of 0.02 is indistinguishable from zero. Deploying it would add
operational surface and a left tail in exchange for nothing.

## What would have to change

- **Options rather than an ETF wrapper.** Short variance expressed directly (an
  ES/SPX put spread or a variance swap proxy) avoids paying a fund to deliver
  half the exposure. That needs options support the bot layer does not have.
- **Or accept it as a hedged-and-tiny sleeve** only if the point is the demonstrated
  drawdown control rather than the return. That is a real argument, but it is not
  the argument the strategy was proposed on.

Worth noting: the pre-2018 numbers (Sharpe 0.62, max DD 12.9% through Feb 2018)
say the *rule set* is sound. It is the available instrument that is not.
