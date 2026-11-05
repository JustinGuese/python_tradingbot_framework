# quantsmd v3: is the one candidate real? (2026-11-05)

Question: after the v2 round (docs/backtests/quantsmd-v2-2026-11.md), only `QuantsmdSmaDailyGatedBot` cleared the
project bar (t >= 2, positive in both halves, beta < 0.5): t 3.08 over 2000-2026, beta 0.16. This round adds
two bots and a robustness test of that one candidate. Reproduce with
`scripts/onetime_quantsmd_backtest.py <module>` and `scripts/onetime_quantsmd_robustness.py --section <name>`
(sections: gate_window, costs, leave_one_out, subperiods).

## New bots

| Bot | Start | Alpha/yr | t | Beta | Corr | Max DD | H1 t | H2 t |
|---|---|---|---|---|---|---|---|---|
| SmaDailyGatedV3 (SHV cash parking, `regime_window` param) | 2007 | +4.1% | 1.96 | 0.26 | 0.52 | -20.1% | 1.29 | 1.51 |
| GatedBlend (0.4 sma_gated / 0.2 reversal / 0.2 momentum / 0.2 te_gated, static) | 2006 | +1.3% | 1.06 | 0.17 | 0.57 | -12.8% | 0.54 | 0.99 |

- V3 is on a 2007+ window (SHV exists from 2007), so its t is not comparable with the v2 bot's 3.08 over 2000-2026.
  Both halves are positive. SHV parking adds the T-bill yield the paper bot was missing. Its beta is higher (0.26 vs 0.16), most likely
  because the 2007+ window drops the 2000-06 stretch (beta 0.06) and keeps the 2013-19 stretch (beta 0.40); this was
  not isolated, so the v2 bot on the same 2007+ window is the fair comparison and has not been run.
- The blend does not beat its best component (t 1.06). The four components are all sector or ETF8 trend books,
  so they are correlated (corr with QQQ 0.57) and averaging them dilutes the one strong signal.
  The blend was fixed-weight, not fitted, so this is a clean negative.

## Robustness of SmaDailyGated (v2 bot, 2000-2026)

| Test | Result (alpha, t) |
|---|---|
| Gate window 100 / 150 / 200 / 250 / 300 | +4.9% (2.93) / +4.8% (2.73) / +5.5% (3.11) / +6.0% (3.33) / +6.5% (3.49) |
| Slippage 5 / 10 / 20 bps (4x default) | +5.5% (3.11) / +5.2% (2.89) / +4.4% (2.46) |
| Leave one sector out (9 runs) | t 2.84 to 3.45, alpha +5.1% to +5.9%, max DD -18% to -21% |
| 2000-2006 | +4.3% (1.52), beta 0.06 |
| 2007-2012 | +1.5% (0.39), beta 0.18 |
| 2013-2019 | -0.0% (-0.01), beta 0.40 |
| 2020-2026 | +6.2% (1.83), beta 0.16 |

## Verdict

- **It is not a one-parameter or one-sector artefact.** t stays at 2.7-3.5 for every gate window from 100 to 300
  (no knife edge, and 200 is not the best), at 4x costs, and with any single sector removed.
- **But the alpha is episodic.** Three of four sub-periods are positive, and none reaches t = 2 alone; 2013-2019
  is flat (beta 0.40, so it was mostly just long equities). The headline t of 3.08 rests on the gate sitting
  in cash through the 2000-02 and 2008-09 bear markets. On 2013-2026 alone the default's t is 1.35.
- **What this means:** the gate is a drawdown filter that earns its alpha by avoiding bear markets, not a signal that
  adds return in bull markets. It clears the bar on the 25-year backtest and survives the checks above, but there
  is no live track record, and a bull-market stretch like 2013-2019 earns about nothing against QQQ.
  Call it a credible candidate, not a proven edge. Let the weekly `alphareport` accumulate live days
  before promoting it to any copier.

## Caveats

Same as earlier rounds: long-only, today's ETF survivors, framework cost model, cash earns 0% in the v2 bot's
backtest (so its real return is slightly understated), no live history.
