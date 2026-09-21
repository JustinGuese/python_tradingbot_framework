@AGENTS.md

## The target: alpha vs QQQ, not return

**Judge, rank, promote and blend bots by their annualised alpha against QQQ and
its t-stat. Do not use raw return, and do not use Sharpe on its own.** This
applies whenever you evaluate a bot, pick C2 / copier weights, or decide whether
a new strategy is worth building.

**Why:** QQQ exposure costs nothing, because anyone can buy QQQ. A bot only earns
its place if it adds return that QQQ does not already provide. Raw return rewards
the wrong things:

- **In a rally it rewards beta.** A beta-1 bot "beats" everything and is just
  QQQ with extra moving parts. A leveraged bot looks even better until the
  drawdown. For example, `RecursiveDecayHarvestBot` had beta 2.6 and alpha
  −81%/yr, yet matched QQQ on return.
- **It punishes low-beta bots for doing their job.** A beta-0.25 bot in a +17%
  tape *should* make about 4%. That is not underperformance.
- **Only uncorrelated alpha improves a portfolio.** A high-correlation bot adds
  nothing to a QQQ holding. A low-correlation positive-alpha bot raises the
  Sharpe of the whole book. Since beta can be bought directly, alpha is the only
  scarce ingredient.

**How to compute it:** use `portfolio_worth` daily series and follow the
weekday/gap rules in the AGENTS.md "PortfolioWorth Model" section. Drop weekend
rows, and drop day-pairs more than 4 days apart (recorder outages). Then:

```python
beta = cov(r_bot, r_qqq) / var(r_qqq)
resid = r_bot - beta * r_qqq
alpha = resid.mean() * 252  # annualised
t_stat = resid.mean() / resid.std() * sqrt(n)  # n = daily obs
```

Report alpha, t-stat, beta, correlation and max drawdown together, each over the
bot's own live window, with QQQ over that same window.

**Backtests use the same method.** `backtest_bot` returns `alpha`, `alpha_t`, `beta`
and `benchmark_corr` (see `_compute_alpha_metrics` in `tradingbot/utils/backtest.py`),
and `local_optimize` / `tune_hyperparameters` rank by `alpha_t` by default. Don't
tune a bot on `yearly_return` or `sharpe_ratio`, because that selects for beta.
When you pass `data=` yourself, also pass `benchmark_close=`. Otherwise the alpha
keys are `None`, and that means "not measured", not zero.

**Idle cash earns T-bill yield on the broker side.** The live copier parks
uninvested weight in `SHV` (`LIVETRADE_CASH_PROXY`; see
`LiveTradeCopier._park_idle_cash`). Paper bots still hold 0%-yield `USD`, so their
`portfolio_worth` alpha understates what the copied book earns by roughly
rf × the cash share.

**The bar:**
- **Real edge requires t ≥ 2.** Below that it is noise, however good it looks.
  At 75–120 live days (typical as of Sep 2026) almost nothing clears it, so say
  "unproven" rather than "working".
- **Prefer beta < 0.5 / correlation < 0.5.** A bot with beta ≈ 1 and ~0 alpha is
  a QQQ clone. Replace it with QQQ, don't blend it.
- **Negative alpha with t ≤ −2 is a real result:** pause the bot.
- Prefer a lower-alpha, lower-correlation bot over a higher-return,
  higher-beta one.

**Snapshot, 2026-09-21** (live, per-bot window; none reached t ≥ 2 positive):

| Bot | Alpha/yr | t | Beta | Verdict |
|---|---|---|---|---|
| TARegimeAdaptiveBot | +14% | 1.30 | 0.31 | likely luck: 6y backtest alpha +0.4% (t 0.10) |
| XAUZenbotTreeBot | +20% | 0.88 | 0.05 | dead: flat on every bar since ^XAU > 325, not refittable |
| AdaptiveMeanReversionBot | ~0% | 0.19 | 0.99 | QQQ clone |
| FearGreedBotQQQInverse | +5% | 0.56 | 0.91 | QQQ clone |
| RecursiveDecayHarvestBot | −81% | −1.93 | 2.61 | levered QQQ, bleeds — paused |
| EURUSDTreeBot | −11% | −2.32 | 0.02 | significantly negative — paused |

The C2 blend at the time (Kronos / RegimeAdaptive / EarningsInsiderTilt) made
+5.5% vs QQQ +17.4%, with alpha +3% at t = 0.25. That is weak on the metric that
matters.

Follow-ups from that snapshot: SqueezeMomentumBot and StockNewsSentimentBot are also
paused (`suspend: true` in values.yaml). FearGreedBotQQQInverse is now flat by
default (buy at ≤30, exit at ≥50). TARegimeMultiAssetBot runs as paper only. Both
of those reliably cut beta by about half or more in backtests, but neither has
alpha that holds up in both halves of the sample. See
docs/backtests/taregimemultiassetbot.md and the comment in feargreedbot.py.
