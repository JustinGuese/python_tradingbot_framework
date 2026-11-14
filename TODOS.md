I read the copier, the blend and event write-ups, and the last two days of live-trade job logs in the cluster. The short answer: the engineering is ahead of the evidence, and nothing here is sellable to a fund yet. Two of your four live legs are also placing no trades at all.

## What the live legs are actually doing (logs, 12–13 Nov)

| Leg         | Reality                                                                                                                                                                                                                                      |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Collective2 | Every order is rejected with error 1020 ("paid Trade Leader plan required"). Equity is $60,152, all cash, so the blend has never been published there.                                                                                       |
| MT5 / MQL5  | Every buy on 13 Nov failed with "result expired". Equity is $110,580, all cash.                                                                                                                                                              |
| IBKR paper  | Not dead any more: it connects and trades UCITS substitutes (CSPX, IB01, IDTM). But it runs at 21:20 UTC, after London closes, so orders are queued to the next London open and counted as rejected (EIMI on Friday was deferred to Monday). |
| eToro demo  | Working: orders accepted, about $119k equity, nearly fully invested.                                                                                                                                                                         |

The summary you pasted says C2 is publishing and IBKR is dead; both are out of date.

## Is the signal-platform approach a good idea?

The architecture is good: one target-weight book, thin broker adapters, and full reconciliation. The strategy of fanning out to five venues is not, for three reasons:

- **Demo records are worth nothing to an allocator.** They prove no fills, and each venue trades a different substitute universe at a different time, so you get five records of five slightly different strategies.
- **Retail platforms rank by return.** A blend with about 6.6% CAGR at 5.7% vol will attract no subscribers on C2 or MQL5, however good its alpha.
- **Each venue is a maintenance surface.** Two of them have been failing unnoticed, which is the failure you care most about.

I would cut to two legs:

1. **One small real-money account on IBKR** as the master record, with broker statements as the audit trail.
2. **Darwinex Zero through the existing MT5 bridge** as the distribution wrapper. Darwinex is the regulated manager and pays you a performance fee, so you need no licence of your own. Check its current terms and instrument list first.

Drop C2 unless you decide the plan fee is worth paying, and park the MQL5 demo and Hyperliquid legs.

## What I would do differently

- **Stop waiting for live paper data to prove alpha.** The blend's backtest t of 2.02 over 7.7 years is an information ratio of about 0.7. At that rate a live t of 2 takes about 7.5 years, and the March 2027 review will show a t of about 0.5 either way.
- **Use live data to check implementation instead.** Replay the backtest over the live window and compare daily returns. That answers "does live match the backtest" in weeks.
- **Raise the bar to t ≥ 3 for anything picked from a search.** With about 110 bots and hundreds of variants, the best of pure noise is expected to show a t of about 2.5. Keep a registry of every variant tried.
- **Add a second benchmark.** Alpha vs QQQ alone credits a multi-asset book for holding gold and bonds in a gold bull market. Also test each sleeve against a static hold of its own universe, as the PEAD study did, and against QQQ + TLT + GLD + DBC.
- **Cut the fleet to about ten bots.** A paper slot should require a passing backtest first. The 50 `Quantsmd*` templates and most option variants add noise and trial count, not information.
- **Extend the history to cover 2008.** The blend's backtest covers 2019–2026 only.
- **Align execution with the backtest.** The backtest fills at the signal bar's close; the copiers trade after the close or the next day. Measure that gap.
- **Fix the shared default weights.** In [values.yaml](helm/tradingbots/values.yaml) the default `botWeights` is still the paused 3x bot plus the QQQ clone, with `dryRun: "false"`. Any new broker without an override inherits it.

## Strategies to try next

1. **A cross-sectional equity model, scored by Numerai.** Rank about 2,000 stocks on reversal, residual momentum, buyback announcements, insider clusters and option-implied signals. With that many names and daily scoring, significance arrives in months, and Numerai is a free third-party out-of-sample test. The current price-only submission will likely score near zero once they neutralise it.
2. **Beta-hedged event sleeves.** Buyback drift is your strongest single result (t 2.56 in backtest). Hedge it with a short index leg, so it becomes a zero-beta stream, and widen the universe beyond the S&P 100.
3. **Long/short trend and carry on micro futures.** This replaces the three long-only ETF sleeves, which correlate 0.6–0.7 with each other. It is a diversifier for your own book, not a sellable product, because funds can already buy it cheaply in ETF form.
4. **Funding-rate carry on Hyperliquid.** It is market-neutral and accrues evidence hourly, and the adapter already exists. Yields have compressed and there is exchange risk, so treat it as a small experiment.

I would stop building single-asset indicator bots, LLM stock pickers, Kronos variants and intraday scalps on yfinance minute data.

## Selling to funds

Funds do not buy signals from individuals with paper records. What gets a first allocation:

- **Record:** two to three years of real money, verified by broker statements or an administrator.
- **Quality:** net Sharpe above 1, ideally 1.5, with correlation below 0.3 to both equities and trend-following indices.
- **Capacity:** at least $50–100M.

A long-only ETF trend blend at an information ratio of 0.7 does not meet this. The realistic routes, in order of friction:

1. **Numerai staking:** paid directly for alpha, no customers needed.
2. **Darwinex:** performance fee on allocator capital, under their licence.
3. **Managed accounts for family offices or seeders:** only after the real-money record exists. Managing other people's money is regulated almost everywhere, so this needs a licence or a regulatory umbrella, and legal advice for your jurisdiction.

The market-neutral equity book in item 1 above is the only candidate that could plausibly reach that bar. If the goal is income sooner, selling the framework as a hosted product is a more direct route than selling the strategies.

---

## 5. mql5 signal

It's a one-time manual step on the MQL5 website. There is no API for it. The steps are in docs/guides/live-trading.md, section 4.

Log in at mql5.com and go to Signals → Create Signal.
Enter the MT5 login (MT5_MQL5_LOGIN) and pick the MetaQuotes-Demo server.
Give it the investor password (read-only). Never enter the master trading password. It's in your .env.secret, so I'm not printing it here.
Choose Free signal. A free signal can be registered from a demo account. It gives you a public, audited track record with no real money at risk. A paid signal needs a real funded account and MQL5 seller verification (ID and address).
Do this after the bot has placed real trades. A signal with an empty history looks dead, and the audit starts counting from registration. So wait for the 15:00 UTC run to fill, then register.
