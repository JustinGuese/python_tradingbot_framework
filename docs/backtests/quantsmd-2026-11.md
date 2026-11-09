# quants.md top-10 strategies, backtested (2026-11-03)

Ten QuantJourney example strategies from https://quants.md/?sort=sharpe, re-implemented as
`tradingbot/quantsmd_*.py`, each bot's header linking its strategy page. Reproduce with
`uv run python scripts/onetime_quantsmd_backtest.py` (about 50 minutes, offline, yfinance data).

Method: one `backtest_bot` run per bot over its whole common history, default parameters, framework cost
model (0.05% slippage), `benchmark_close` = QQQ. Alpha / t / beta / corr are annualised vs QQQ per the
CLAUDE.md formula. H1 / H2 are alpha stats on the two halves of that one equity curve.
Start year is the oldest bar every ticker shares (futures bot: 2019; ETF8 bots: 2006; sector bots: 2000).
QQQ and "universe hold" CAGR are over each bot's own window, so the windows differ between rows.

Note: the old column was total return, corrected 2026-11-09, values are approximate (start year, not exact first bar).

| Bot (source Sharpe) | Start | Alpha/yr | t | Beta | Corr | Sharpe | CAGR (true) | Max DD | QQQ CAGR | H1 alpha (t) | H2 alpha (t) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ContinuousFuturesTrend (0.62) | 2019 | +4.9% | 2.45 | 0.11 | 0.43 | 1.19 | 7.0% | -8.0% | 20.2% | +5.7% (1.93) | +2.0% (0.94) |
| VolTargetMomentum (0.57) | 2006 | +4.7% | 1.87 | 0.22 | 0.39 | 0.68 | 7.8% | -30.9% | 15.4% | +7.0% (1.80) | +2.2% (0.68) |
| MonthlyRotation (0.53) | 2006 | +5.1% | 1.86 | 0.26 | 0.42 | 0.69 | 8.7% | -30.4% | 15.4% | +8.8% (2.02) | +1.0% (0.30) |
| SmaDaily (0.45) | 2000 | +4.8% | 2.27 | 0.32 | 0.61 | 0.61 | 7.7% | -35.4% | 8.1% | +5.3% (1.70) | +0.2% (0.08) |
| MonthlyInverseVol (0.45) | 2006 | +2.1% | 1.49 | 0.26 | 0.66 | 0.75 | 6.2% | -21.5% | 15.4% | +3.6% (1.76) | +0.8% (0.40) |
| RiskParityCapped (0.45) | 2000 | +4.8% | 2.16 | 0.46 | 0.73 | 0.60 | 8.8% | -49.8% | 8.1% | +5.9% (1.68) | +1.6% (0.64) |
| QuarterlyTeCostGate (0.44) | 2006 | +5.6% | 2.02 | 0.26 | 0.42 | 0.72 | 9.3% | -21.0% | 15.4% | +6.8% (1.57) | +4.3% (1.24) |
| BollingerReversion (0.42) | 2000 | +2.3% | 1.06 | 0.33 | 0.61 | 0.42 | 5.1% | -38.4% | 8.1% | +2.9% (0.85) | +0.2% (0.09) |
| VolTargetTrend (0.36) | 2006 | +1.6% | 0.89 | 0.23 | 0.53 | 0.57 | 5.1% | -20.5% | 15.4% | +2.4% (0.88) | +0.7% (0.28) |
| WeeklyRsiReversion (0.34) | 2000 | +3.3% | 1.41 | 0.41 | 0.67 | 0.49 | 6.7% | -52.0% | 8.1% | +3.8% (1.05) | +1.5% (0.55) |

## Reading it by the project bar (t >= 2 and positive in both halves)

- **Nothing passes cleanly.** Five bots clear t >= 2 on the full window (ContinuousFuturesTrend 2.45,
  SmaDaily 2.27, RiskParityCapped 2.16, QuarterlyTeCostGate 2.02) or sit just under it
  (VolTargetMomentum 1.87, MonthlyRotation 1.86). None has t >= 2 in both halves, and in every bot the
  second half is much weaker than the first. QuarterlyTeCostGate is the most even (H2 +4.3%, t 1.24).
- **Alpha is small and mostly a low-beta artefact.** Betas are 0.1-0.5, so these are cash-heavy books that
  lag QQQ by about 7-10 points. Alpha is positive because beta is low while QQQ rose. A rally-era
  alpha of ~5% at beta 0.3 is not evidence of skill.
- **Best candidates to watch (unproven, not promoted):** ContinuousFuturesTrend (beta 0.11, corr 0.43, max
  DD -8%, but only 2019 onward and long-only/proxy futures) and QuarterlyTeCostGate (beta 0.26, corr 0.42).
- **Weakest:** VolTargetTrend (t 0.89), BollingerReversion (1.06), WeeklyRsiReversion (1.41): high trade
  counts, no edge.
- **Source Sharpe does not carry over.** Ranking by source Sharpe (0.62 to 0.34) is not the ranking here:
  these are long-only, unlevered, with costs, and measured on alpha vs QQQ rather than Sharpe.

## Caveats

- Departures from the QuantJourney code (long-only, leverage capped at 1.0x, approximated TE/turnover
  gate, ERC risk parity) are listed in each bot's docstring. The TE-gate state resets on every live
  CronJob run, so its live behaviour is the quarterly rule only.
- ContinuousFuturesTrend uses yfinance continuous futures (no roll, no multiplier); signal proxy only.
- ETF universes are today's survivors of a fixed list; sector SMA / RSI bots start in 2000.
- None of these bots is scheduled (no `helm/values.yaml` entry).
