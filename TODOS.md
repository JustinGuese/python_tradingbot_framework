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

Keep Collective2 ENABLED per user decision (requires activating a paid Trade Leader plan to resolve error 1020), and park the MQL5 demo and Hyperliquid legs.

## What I would do differently

- **Stop waiting for live paper data to prove alpha.** The blend's backtest t of 2.02 over 7.7 years is an information ratio of about 0.7. At that rate a live t of 2 takes about 7.5 years, and the March 2027 review will show a t of about 0.5 either way.
- **Use live data to check implementation instead.** Replay the backtest over the live window and compare daily returns. That answers "does live match the backtest" in weeks.
- **Raise the bar to t ≥ 3 for anything picked from a search.** With about 110 bots and hundreds of variants, the best of pure noise is expected to show a t of about 2.5. Keep a registry of every variant tried.
- **Add a second benchmark.** Alpha vs QQQ alone credits a multi-asset book for holding gold and bonds in a gold bull market. Also test each sleeve against a static hold of its own universe, as the PEAD study did, and against QQQ + TLT + GLD + DBC.
- **Cut the fleet to about ten bots.** A paper slot should require a passing backtest first. The 50 `Quantsmd*` templates and most option variants add noise and trial count, not information.
- **Extend the history to cover 2008.** The blend's backtest covers 2019–2026 only.
- **Align execution with the backtest.** The backtest fills at the signal bar's close; the copiers trade after the close or the next day. Measure that gap.
- **Fix the shared default weights.** (Done in S2) Updated `liveTrade.botWeights` in [values.yaml](helm/tradingbots/values.yaml) to the validated blend (`TARegimeMultiAssetBot: 0.39, TSMOMTrendBot: 0.24, GoldenButterflyMomBot: 0.37`), removing the paused 3x bot and QQQ clone.

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
Do this after the bot has placed real trades. A signal with an empty history looks dead, and the audit starts counting from registration. So wait for the 15:10 UTC run to fill, then register.

---

## 6. Real-Money IBKR Master Account

Establish a live, real-money Interactive Brokers account as the primary master track record and audit trail:

1. **Account Provisioning:** Open and fund a dedicated live IBKR account (U-account prefix, distinct from the paper `DU6347896` account).
2. **Credentials & Gateway:** Configure live account credentials in IB Gateway (`interactivebrokersgateway` namespace) with dedicated API access to prevent mobile/TWS session displacement.
3. **Instrument Approvals & UCITS Mapping:** For EU/MiFID retail accounts, ensure USD-denominated UCITS ETF trading permissions are granted (CSPX, IDTL, IDTM, IB01). Verify all mapped lines via `discover_symbols --broker ib`.
4. **Helm Configuration:** Update `helm/tradingbots/values.yaml` under `liveTrade.interactiveBrokers` (or configure a parallel `interactiveBrokersLive` entry) with the real `accountId` and verified `portfolioFraction`.
5. **Institutional Audit Trail:** Export and store monthly/annual broker activity statements as cryptographically verifiable performance proof for allocators. Monitor live vs paper execution tracking and slippage using `scripts/live_vs_paper_tracking.py`.

---

## 7. Collective2 Trade Leader Subscription

- **User Decision:** Collective2 stays **ENABLED** (`collective2XauZerifine`).
- **Resolution for Error 1020:** Log into Collective2 account settings and upgrade strategy `155809898` to an active paid **Trade Leader** plan.
- The copier is active and running at 21:50 UTC; once the Trade Leader plan is active, target orders will be accepted without API rejection.

---

## 8. Retail book: real-money accounts and live checks

Retail venues (eToro, MT5/MQL5, Collective2) copy `InstitutionalFlowBot` alone since 2026-11-14 (docs/backtests/retail-blend-2026-11.md; unproven, lags QQQ in rallies).

1. Open real-money eToro and MT5 broker accounts, otherwise no real track record is built (both are demo today).
2. Fill the live rows of docs/backtests/retail-venue-coverage-2026-11.md (eToro search, MT5 symbol scan). Check that eToro `is_tradeable` returns False for unlisted names instead of raising.
3. After 60 and 120 live days, check `alphareport` for InstitutionalFlowBot and the live-vs-paper tracking script.

---

## Strategy review, 2026-11-18

**Short version:** nothing live has a proven edge yet, and more single-asset or indicator variants will not change that. The t-stat is limited by how few independent bets each bot makes, and CAGR by low beta and idle cash; these need different fixes.

## Where the bots stand (live `portfolio_worth`, computed today)

- **No bot with 100+ live days has a positive t ≥ 2.** The best are XAUZenbotTreeBot at 1.31 (which CLAUDE.md already marks dead) and TARegimeAdaptiveBot at about 1.0.
- **The C2 blend is negative so far:** alpha −8.5%/yr, t −1.04, beta 0.19 over 40 days. TSMOMTrendBot alone is at t −1.97.
- **Three "different" bots are one bet.** EarningsInsiderTiltBot and RegimeAdaptiveBot have a residual correlation of 0.93, and DeepSeekToolBot is at 0.77 with both.
- **Six option bots have had zero P&L for 35 days**, so they collect no evidence: CatalystCall, CreditSpread, CrossVol, Dispersion, EarningsCalendar and IndexVol.

## Improving t on existing bots

t is roughly (alpha ÷ residual volatility) × √years. The blend's ratio is about 0.7, so a live t of 2 takes about 8 years; only more independent bets or less residual noise shortens that.

1. **Fix two bots whose live version is not what was backtested.**
   - [quantsmd_quarterly_te_cost_gate.py](tradingbot/quantsmd_quarterly_te_cost_gate.py) keeps its rebalance state on the instance. Each CronJob run starts fresh and rebuilds the book, so live it is a daily momentum rotation, not the quarterly strategy that scored t 1.94.
   - [quantsmd_continuous_futures_trend.py](tradingbot/quantsmd_continuous_futures_trend.py) trades `MES=F`-style futures tickers. The t 2.36 result since 2008 was on ETF proxies (SPY, QQQ, IEF, USO, GLD, DBA). It is 100% cash since launch, probably because it launched a day after the monthly rebalance bar.
2. **Swap TSMOMTrendBot for the ETF-proxy trend book in the blend.** Rebuilt on 2010–2026, TSMOM has alpha +0.8% at t 0.41 and a negative first half. The five-sleeve blend in [sleeve-blend-2026-11.md](docs/backtests/sleeve-blend-2026-11.md) scored t 3.01 out of sample, but no bot or copier runs it.
3. **Hedge buybacks with sector ETFs, not QQQ.** A QQQ short leaves alpha-vs-QQQ t unchanged by construction, so BuybackHedgedBot only lowers beta. Shorting each name's sector ETF removes noise QQQ does not explain, which is what raises t.
4. **InstitutionalFlowBot: cut turnover and widen the universe.**
   - It makes 13 fills a week, and going from 5 bp to 20 bp costs 4.8 points of CAGR. Keep a holding until it drops out of the top 35 or so, instead of the top 20.
   - Move from the S&P 100 to the S&P 500 with point-in-time membership. Only `SP100` exists in [universes.py](tradingbot/utils/universes.py).
5. **Rebuild option_ImpliedSignalBot as long/short inside its universe.** Its "pass" is mostly the universe: an equal-weight hold of the 50 names scores t 3.83 on its own, and the bot's alpha against that is t 0.66. It also runs beta 1.08 with a −49.5% drawdown. The `rr25` signal alone looks real (t 1.68 against equal-weight), so test top 10 long against bottom 10 short.

## Improving CAGR

CAGR is roughly T-bill yield on cash + beta × QQQ + alpha. The blend makes about 6.6% because beta is 0.13–0.26 and 40–50% sits in cash at 0%.

- **Hold SHV for the unallocated weight inside the paper bots.** The lift study measured +0.1 to +0.3 in t and about +1 point of alpha for the mostly-cash bots.
- **Scale the blend to a volatility target.** Leverage does not change t. At 2× gross the inverse-vol blend would have roughly 8% alpha, beta 0.5 and a −27% drawdown before financing cost. That is my arithmetic from the 1× backtest, not a new backtest. `MAX_GROSS` now allows it.
- **Add plain QQQ on top for the retail venues.** This raises return but adds no alpha, so it is a product decision, not an edge.

## What to try next

| # | Idea | Why | What exists |
|---|---|---|---|
| 1 | Market-neutral stock book: residual momentum plus 1-month reversal, sector-neutral, about 50 names per side | Hundreds of independent bets a year, where every current bot has 20 or fewer | Signal code in [numerai_features.py](tradingbot/utils/numerai_features.py); no bot, no backtest vs QQQ |
| 2 | `rr25` skew long/short (item 5 above) | Only signal here with stock-level evidence | Data and bot exist |
| 3 | Sector-hedged buyback sleeve (item 3 above) | Strongest event result (t 2.56) with less noise | Bot and shorts exist |
| 4 | 13F holdings signal with the 45-day filing lag | Untested data source | Backfill script only; I did not check the tables |
| 5 | Volatility-managed QQQ | One-file test; published evidence is disputed | Nothing |
| 6 | Finish the Hyperliquid funding-carry study | It never produced numbers (API errors) | Script exists |

I would do 1 first, with the variants and the kill rule written down before the run. TODOS.md already notes that with about 110 bots the best of pure noise is t ≈ 2.5, so each new variant raises the bar.

I would not retry large-cap earnings drift, long-only FX carry, insider clusters, calendar legs, wider TradingView universes, broad buybacks or cross-vol. Each has a write-up in [docs/backtests/](docs/backtests/) showing no edge.