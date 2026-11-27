# TS151 Trading Strategies Framework

Implementation of tradeable strategies from **Kakushadze & Serur (2018), *151 Trading Strategies*, SSRN 3247865**.

> [!NOTE]
> All bots inherit from `TS151Bot` (`tradingbot/utils/ts151/base.py`) which implements the standard `targetWeights` interface. Every strategy is fully parametrized, supports backtesting via `backtest_bot` / `local_optimize` (except forward-only contracts), and is deployed as a paper bot on Kubernetes CronJob.

---

## Important Notices

### Paper Short Notice
> [!WARNING]
> **Shorts are paper-only until a short-capable broker copier is live.**
> Long/Short (LS) strategies compute signed target weights (`ALLOW_SHORT = True`, `MAX_GROSS = 2.0`). In paper trading, negative target weights simulate short equity/ETF exposures. Live trade copier configurations currently mirror only long-only positions or venues with borrow facilities.

### Execution & Staggered Scheduling
- **Schedule Window**: Daily market-close strategies run on weekdays staggered between **21:05 and 21:55 UTC** (after the 21:00 UTC US market close) so that full daily OHLCV bars are processed before portfolio valuation at 22:00 UTC.
- **S&P 500 Isolation**: To avoid concurrent memory/CPU spikes, all 11 S&P 500 universe bots are allocated dedicated single-bot minutes (21:45 to 21:55 UTC).
- **Concurrency Limit**: Non-S&P 500 bots are capped at a maximum of 2 jobs per minute across 21:05–21:44 UTC.
- **Continuous/Weekend Assets**: Crypto bots run daily at `10 0 * * *`.
- **Intraday Bar Timing**: `ts151_ibsbot` runs at `50 20 * * 1-5` (20:50 UTC, 10 minutes prior to regular US market close) to capture the closing range without waiting for post-session settlement.

---

## Ingestion & Backfill Pipelines

Three specialized data ingestion pipelines provide point-in-time auxiliary data for fundamental, commitment-of-traders, and corporate event strategies:

| Job Name | Source | Schedule | Backfill Command | Purpose / Consumers |
|---|---|---|---|---|
| `ts151_secfundamentalssnapshot` | SEC EDGAR XBRL companyfacts API | `0 6 * * 0` (Weekly Sun 06:00 UTC) | `python -m tradingbot.ts151_secfundamentalssnapshot` | Point-in-time financial statement facts (Balance Sheet, Income, Shares) for S&P 500/100 (`ts151_valuebot`, `ts151_earningsmomentumbot`, `ts151_distressbot`, `ts151_multifactorbot`, `ts151_alphacombobot`). |
| `ts151_cotsnapshot` | CFTC Public Reporting API (Legacy Futures) | `30 6 * * 6` (Weekly Sat 06:30 UTC) | `python -m tradingbot.ts151_cotsnapshot --backfill` | Commercial and non-commercial open interest positions for commodities (`ts151_hedgingpressurebot`). Auto-backfills to 1986 if empty. |
| `ts151_mergerscan` | EDGAR Daily Form Index (SC TO-T, SC 14D9, DEFM14A, PREM14A) | `15 13-21 * * 1-5` (Hourly 13:15–21:15 UTC) | `python -m tradingbot.ts151_mergerscan --backfill 2005` | Target company M&A announcements and tender offers (`ts151_mergerarbbot`). Auto-backfills to 2005 if empty. Writes heartbeat. |

---

## Deployed TS151 Strategy Bots

The table below lists all 69 deployed strategy bots across Sections 3–19 of Kakushadze & Serur (2018):

| § | File / Bot Name | Class | Universe | Rebal | Type | Data Needs | Backtestable | Description & Key Parameters |
|---|---|---|---|---|---|---|---|---|
| §3.1 | `tradingbot/ts151_pricemomentumbot.py` | `TS151PriceMomentumBot` | `sp500` | `M` | LS | OHLCV | yes | **Price Momentum**. Defaults: `window=252, skip=21, frac=0.1` |
| §3.2 | `tradingbot/ts151_earningsmomentumbot.py` | `TS151EarningsMomentumBot` | `sp500` | `M` | LS | DB | yes | **Earnings Momentum (SUE)**. Defaults: `n_q=8, frac=0.1` |
| §3.3 | `tradingbot/ts151_valuebot.py` | `TS151ValueBot` | `sp500` | `M` | LS | DB | yes | **Value (Book-to-Price)**. Defaults: `frac=0.1` |
| §3.4 | `tradingbot/ts151_lowvolatilitybot.py` | `TS151LowVolatilityBot` | `sp500` | `M` | LS | OHLCV | yes | **Low Volatility**. Defaults: `window=252, frac=0.1` |
| §3.5 | `tradingbot/ts151_impliedvolatilitybot.py` | `TS151ImpliedVolatilityBot` | `(NVDA, AAPL, GOOGL, MSFT, AMZN, META, AVGO, TSLA, MU, LLY, AMD, JPM, WMT, V, XOM, JNJ, INTC, MA, ABBV, PLTR, CSCO, ORCL, COST, CVX, BAC, LRCX, AMAT, KO, CAT, MRK, DELL, PG, GE, UNH, MS, PANW, PM, NFLX, HD, GS, ANET, SNDK, RTX, GEV, TXN, WFC, TMO, C, AMGN, LIN)` | `M` | LS | DB | no (forward-only) | **Implied Volatility**. Defaults: `window=21, frac=0.2` |
| §3.6 | `tradingbot/ts151_multifactorbot.py` | `TS151MultifactorBot` | `sp500` | `M` | LS | DB | yes | **Multifactor**. Defaults: `mom_window=252, mom_skip=21, vol_window=252, frac=0.1, factor_weights=[1.0, 1.0, 1.0]` |
| §3.7 | `tradingbot/ts151_residualmomentumbot.py` | `TS151ResidualMomentumBot` | `sp500` | `M` | LS | OHLCV | yes | **Residual Momentum**. Defaults: `lookback_bars=756, momentum_bars=252, skip_bars=21, frac=0.1` |
| §3.8 | `tradingbot/ts151_pairstradingbot.py` | `TS151PairsTradingBot` | `sp500` | `M` | LS | OHLCV | yes | **Pairs Trading**. Defaults: `formation=252, n_pairs=10, entry_z=2.0, exit_z=0.5` |
| §3.9 | `tradingbot/ts151_meanreversionclusterbot.py` | `TS151MeanReversionClusterBot` | `sp500` | `M` | LS | OHLCV | yes | **Mean Reversion with Industry Clusters**. Defaults: `window=5, gross=2.0` |
| §3.10 | `tradingbot/ts151_weightedregressionbot.py` | `TS151WeightedRegressionBot` | `sp500` | `M` | LS | OHLCV | yes | **Weighted Regression Mean Reversion**. Defaults: `window=5, vol_window=60, gross=2.0` |
| §3.11 | `tradingbot/ts151_singlemabot.py` | `TS151SingleMABot` | `(SPY)` | `M` | LS | OHLCV | yes | **Single Moving Average**. Defaults: `window=200` |
| §3.12 | `tradingbot/ts151_twomabot.py` | `TS151TwoMABot` | `(SPY)` | `M` | LS | OHLCV | yes | **Two Moving Averages**. Defaults: `fast=50, slow=200, stop_loss=0.0` |
| §3.13 | `tradingbot/ts151_threemabot.py` | `TS151ThreeMABot` | `(SPY)` | `M` | LS | OHLCV | yes | **Three Moving Averages**. Defaults: `s1=3, s2=10, s3=21` |
| §3.14 | `tradingbot/ts151_supportresistancebot.py` | `TS151SupportResistanceBot` | `(SPY)` | `M` | LS | OHLCV | yes | **Support and Resistance (Pivot Points)**. Defaults: `hold_bars=1` |
| §3.15 | `tradingbot/ts151_channelbot.py` | `TS151ChannelBot` | `(SPY)` | `M` | LS | OHLCV | yes | **Channel (Donchian Mean Reversion)**. Defaults: `window=20` |
| §3.16 | `tradingbot/ts151_mergerarbbot.py` | `TS151MergerArbBot` | `(SPY)` | `M` | LO | DB | yes | **Merger Arbitrage**. Defaults: `hold=120, max_weight=0.1` |
| §3.17 | `tradingbot/ts151_knnbot.py` | `TS151KNNBot` | `sectors` | `M` | LO | OHLCV | yes | **K-Nearest Neighbors (KNN)**. Defaults: `k=10, train=504, horizon=5` |
| §3.18 | `tradingbot/ts151_statarbbot.py` | `TS151StatArbBot` | `sp100` | `M` | LS | OHLCV | yes | **Statistical Arbitrage**. Defaults: `n_factors=5, window=60, entry=1.25, exit=0.5` |
| §3.20 | `tradingbot/ts151_alphacombobot.py` | `TS151AlphaComboBot` | `sp500` | `M` | LS | DB | yes | **Alpha Combination**. Defaults: `train=252, frac=0.1` |
| §4.1 | `tradingbot/ts151_sectormomentumbot.py` | `TS151SectorMomentumBot` | `sectors` | `M` | LO | OHLCV | yes | **Sector Momentum strategy bot**. Defaults: `window=252, skip=21, k=3, gross=1.0` |
| §4.2 | `tradingbot/ts151_alpharotationbot.py` | `TS151AlphaRotationBot` | `sectors` | `M` | LO | OHLCV | yes | **Alpha Rotation strategy bot**. Defaults: `window=252, k=3, benchmark=SPY, gross=1.0` |
| §4.3 | `tradingbot/ts151_rsquaredbot.py` | `TS151RSquaredBot` | `sectors` | `M` | LO | OHLCV | yes | **R-squared Rotation strategy bot**. Defaults: `window=126, frac=0.5, k=3, benchmark=SPY, gross=1.0` |
| §4.4 | `tradingbot/ts151_ibsbot.py` | `TS151IBSBot` | `(SPY, QQQ)` | `M` | LO | OHLCV | yes | **Internal Bar Strength (IBS) bot**. Defaults: `low=0.2, high=0.8, gross=1.0` |
| §4.5 | `tradingbot/ts151_leveragedetfbot.py` | `TS151LeveragedEtfBot` | `leveraged_pairs` | `M` | LS | OHLCV | yes | **Leveraged ETF Pairs strategy bot**. Defaults: `band=0.1, gross=1.0` |
| §4.6 | `tradingbot/ts151_multiassettrendbot.py` | `TS151MultiAssetTrendBot` | `multi_asset` | `M` | LO | OHLCV | yes | **Multi-asset Trend strategy bot**. Defaults: `window=252, vol_window=63, gross=1.0` |
| §5.2 | `tradingbot/ts151_bulletsbot.py` | `TS151BulletsBot` | `treasury_curve` | `M` | LO | OHLCV | yes | **Bullet Portfolio Bot (§5.2)**. Defaults: `target_maturity=8.5, gross=1.0` |
| §5.3 | `tradingbot/ts151_barbellsbot.py` | `TS151BarbellsBot` | `(SHY, TLT)` | `M` | LO | OHLCV | yes | **Barbell Portfolio Bot (§5.3)**. Defaults: `target_duration=7.5, short_asset=SHY, long_asset=TLT, gross=1.0` |
| §5.4 | `tradingbot/ts151_laddersbot.py` | `TS151LaddersBot` | `treasury_curve` | `M` | LO | OHLCV | yes | **Ladder Portfolio Bot (§5.4)**. Defaults: `gross=1.0` |
| §5.5 | `tradingbot/ts151_immunizationbot.py` | `TS151ImmunizationBot` | `treasury_curve` | `M` | LO | OHLCV | yes | **Cash Flow Immunization Bot (§5.5)**. Defaults: `horizon_years=7.0, start=None, gross=1.0` |
| §5.6 | `tradingbot/ts151_ddbutterflybot.py` | `TS151DdButterflyBot` | `(SHY, IEF, TLT)` | `M` | LS | Network | yes | **Dollar-Duration Butterfly Bot (§5.6)**. Defaults: `window=252, entry=1.0, exit=0.25, body=IEF, wings=['SHY', 'TLT'], gross=1.0` |
| §5.7 | `tradingbot/ts151_fiftyfiftybutterflybot.py` | `TS151FiftyFiftyButterflyBot` | `(SHY, IEF, TLT)` | `M` | LS | Network | yes | **Fifty-Fifty Butterfly Bot (§5.7)**. Defaults: `window=252, entry=1.0, exit=0.25, body=IEF, wings=['SHY', 'TLT'], gross=1.0` |
| §5.8 | `tradingbot/ts151_regressionbutterflybot.py` | `TS151RegressionButterflyBot` | `(SHY, IEF, TLT)` | `M` | LS | Network | yes | **Regression-Weighted Butterfly Bot (§5.8)**. Defaults: `window=252, entry=1.0, exit=0.25, body=IEF, wings=['SHY', 'TLT'], gross=1.0` |
| §5.9 | `tradingbot/ts151_lowriskbondbot.py` | `TS151LowRiskBondBot` | `bond_etfs` | `M` | LS | OHLCV | yes | **Low-Risk Bond Bot (§5.9)**. Defaults: `frac=0.33, vol_window=63, gross=2.0` |
| §5.10 | `tradingbot/ts151_bondvaluebot.py` | `TS151BondValueBot` | `treasury_curve` | `M` | LS | Network | yes | **Bond Value Bot (§5.10)**. Defaults: `window=1260, k=2, gross=1.0` |
| §5.11 | `tradingbot/ts151_bondcarrybot.py` | `TS151BondCarryBot` | `treasury_curve` | `M` | LS | Network | yes | **Bond Carry and Roll-Down Bot (§5.11)**. Defaults: `horizon=1.0, k=2, gross=1.0` |
| §5.12 | `tradingbot/ts151_rollingdownbot.py` | `TS151RollingDownBot` | `treasury_curve` | `M` | LO | Network | yes | **Riding the Yield Curve (Rolling Down) Bot (§5.12)**. Defaults: `horizon=1.0, gross=1.0` |
| §5.13 | `tradingbot/ts151_curvespreadbot.py` | `TS151CurveSpreadBot` | `(SHY, TLT)` | `M` | LS | Network | yes | **Yield Curve Slope / Spread Bot (§5.13)**. Defaults: `window=252, entry=1.0, exit=0.25, short_asset=SHY, long_asset=TLT, gross=1.0` |
| §6.5 | `tradingbot/ts151_voltargetbot.py` | `TS151VolTargetBot` | `(SPY)` | `M` | LO | OHLCV | yes | **Volatility Targeting strategy bot**. Defaults: `target=0.15, window=21, cap=1.0, band=0.1, annualization_factor=252.0` |
| §7.2 | `tradingbot/ts151_vixbasisbot.py` | `TS151VixBasisBot` | `(VIXY, SVXY)` | `M` | LS | OHLCV | yes | **VIX Basis / Term Structure strategy bot**. Defaults: `contango=0.95, backwardation=1.05, weight=0.3, vix_ticker=^VIX, vix3m_ticker=^VIX3M, trade_ticker=VIXY, inverse_ticker=SVXY` |
| §7.3 | `tradingbot/ts151_volcarrybot.py` | `TS151VolCarryBot` | `(VIXY, SPY)` | `M` | LS | OHLCV | yes | **Hedged Volatility Carry strategy bot**. Defaults: `contango=0.95, weight=0.3, window=63, max_hedge=1.0, vix_ticker=^VIX, vix3m_ticker=^VIX3M, vixy_ticker=VIXY, spy_ticker=SPY` |
| §7.6 | `tradingbot/ts151_varianceswapbot.py` | `TS151VarianceSwapBot` | `(VIXY, SVXY)` | `M` | LS | OHLCV | yes | **Replicating Variance Swap / VRP strategy bot**. Defaults: `entry=0.01, exit=0.0, scale=0.04, cap=0.5, window=21, vix_ticker=^VIX, spy_ticker=SPY, vixy_ticker=VIXY, inverse_ticker=SVXY` |
| §8.1 | `tradingbot/ts151_fxmabot.py` | `TS151FxMaBot` | `fx_etfs` | `M` | LS | OHLCV | yes | **FX Moving Average crossover strategy bot**. Defaults: `fast=50, slow=200, gross=1.0` |
| §8.2 | `tradingbot/ts151_fxcarrybot.py` | `TS151FxCarryBot` | `fx_etfs` | `M` | LS | Network | yes | **FX Carry Trade strategy bot**. Defaults: `k=2, gross=1.0, lag_days=25` |
| §8.3 | `tradingbot/ts151_dollarcarrybot.py` | `TS151DollarCarryBot` | `fx_etfs` | `M` | LS | Network | yes | **Dollar Carry Trade strategy bot**. Defaults: `gross=1.0, lag_days=25` |
| §8.4 | `tradingbot/ts151_fxmomcarrybot.py` | `TS151FxMomCarryBot` | `fx_etfs` | `M` | LS | Network | yes | **FX Momentum + Carry Blend strategy bot**. Defaults: `window=63, w_mom=0.5, k=2, gross=1.0, lag_days=25` |
| §9.1 | `tradingbot/ts151_rollyieldbot.py` | `TS151RollYieldBot` | `(USO, UNG, GLD, SLV, CPER, CORN, WEAT, SOYB, CANE)` | `M` | LS | Network | no (forward-only) | **Roll Yield Bot (§9.1)**. Defaults: `k=3, gross=1.0` |
| §9.2 | `tradingbot/ts151_hedgingpressurebot.py` | `TS151HedgingPressureBot` | `commodities` | `M` | LS | DB | yes | **Hedging Pressure Bot (§9.2)**. Defaults: `weeks=4, k=3, gross=1.0` |
| §9.3 | `tradingbot/ts151_commoddiversificationbot.py` | `TS151CommodDiversificationBot` | `(SPY, IEF, GLD, DBC)` | `M` | LO | OHLCV | yes | **Commodity Diversification Bot (§9.3)**. Defaults: `window=252, method=minvar` |
| §9.4 | `tradingbot/ts151_commodvaluebot.py` | `TS151CommodValueBot` | `commodities` | `M` | LS | OHLCV | yes | **Commodity Value Bot (§9.4)**. Defaults: `lag_start=1386, lag_end=1134, k=3, gross=1.0` |
| §9.5 | `tradingbot/ts151_skewnessbot.py` | `TS151SkewnessBot` | `commodities` | `M` | LS | OHLCV | yes | **Commodity Skewness Bot (§9.5)**. Defaults: `window=252, k=3, gross=1.0` |
| §10.1 | `tradingbot/ts151_hedgedholdingbot.py` | `TS151HedgedHoldingBot` | `(XLK, SPY)` | `M` | LS | OHLCV | yes | **Hedged Holding Bot (§10.1)**. Defaults: `asset=XLK, hedge=SPY, window=63` |
| §10.3 | `tradingbot/ts151_contrarianbot.py` | `TS151ContrarianBot` | `trend_etfs` | `M` | LS | OHLCV | yes | **Contrarian Bot (§10.3)**. Defaults: `window=5, gross=2.0` |
| §10.4 | `tradingbot/ts151_trendfollowingbot.py` | `TS151TrendFollowingBot` | `trend_etfs` | `M` | LS | OHLCV | yes | **Trend Following Bot (§10.4)**. Defaults: `window=252, vol_window=63, target_vol=0.4, max_gross=2.0` |
| §11.7 | `tradingbot/ts151_mbsbot.py` | `TS151MbsBot` | `(MBB, IEF)` | `M` | LS | OHLCV | yes | **MBS Arbitrage Bot (§11.7)**. Defaults: `window=252, entry=1.0, exit=0.0, duration_mbb=6.0, duration_ief=7.5, gross=1.0` |
| §12.1 | `tradingbot/ts151_convertiblearbbot.py` | `TS151ConvertibleArbBot` | `(CWB, SPY)` | `M` | LS | OHLCV | yes | **Convertible Arbitrage Bot (§12.1)**. Defaults: `window=126, asset=CWB, hedge=SPY, gross=1.0` |
| §13.1 | `tradingbot/ts151_munibot.py` | `TS151MuniBot` | `(MUB, IEF)` | `M` | LS | OHLCV | yes | **Municipal Bond Spread Bot (§13.1)**. Defaults: `window=252, entry=0.5, duration_mub=6.5, duration_ief=7.5, gross=1.0` |
| §14.1 | `tradingbot/ts151_breakevenbot.py` | `TS151BreakevenBot` | `(TIP, IEF)` | `M` | LS | Network | yes | **Inflation Breakeven Arbitrage Bot (§14.1)**. Defaults: `mode=breakeven, window=252, entry=1.0, exit=0.25, fair=2.0, duration_tip=7.0, duration_ief=7.5, gross=1.0` |
| §15.1 | `tradingbot/ts151_distresseddebtbot.py` | `TS151DistressedDebtBot` | `(HYG, IEF, SHV)` | `M` | LO | OHLCV | yes | **Distressed Debt Strategy Bot (§15.1)**. Defaults: `entry=0.08, exit=0.02, window=252, asset=HYG, benchmark=IEF, cash_asset=SHV, gross=1.0` |
| §15.3 | `tradingbot/ts151_distressbot.py` | `TS151DistressBot` | `sp500` | `M` | LS | DB | yes | **Distressed Securities**. Defaults: `vol_window=63, ret_window=21, frac=0.1` |
| §16.2 | `tradingbot/ts151_realestatediversitybot.py` | `TS151RealEstateDiversityBot` | `reits` | `M` | LO | OHLCV | yes | **Real Estate Diversity Bot (§16.2)**. Defaults: `window=252, min_weight=0.05, max_weight=0.5, max_vol=0.15` |
| §16.3 | `tradingbot/ts151_intraassetdivbot.py` | `TS151IntraAssetDivBot` | `reits` | `M` | LO | OHLCV | yes | **Intra-Asset Diversification Bot (§16.3)**. Defaults: `window=252` |
| §16.4 | `tradingbot/ts151_realestatemomentumbot.py` | `TS151RealEstateMomentumBot` | `reits` | `M` | LO | OHLCV | yes | **Real Estate Momentum Bot (§16.4)**. Defaults: `lookbacks=[21, 63, 126, 252], k=2` |
| §16.5 | `tradingbot/ts151_realestateinflationbot.py` | `TS151RealEstateInflationBot` | `(VNQ, IEF)` | `M` | LO | Network | yes | **Real Estate Inflation Bot (§16.5)**. Defaults: `window=3, asset_rising=VNQ, asset_falling=IEF` |
| §18.2 | `tradingbot/ts151_cryptoannbot.py` | `TS151CryptoANNBot` | `crypto` | `M` | LO | OHLCV | yes | **Crypto ANN Bot (§18.2)**. Defaults: `lags=[1, 3, 7, 14, 30], horizon=1, train=730, threshold=0.55, hidden_layer_sizes=[16, 8]` |
| §18.3 | `tradingbot/ts151_cryptosentimentbot.py` | `TS151CryptoSentimentBot` | `(BTC-USD)` | `M` | LO | Network | yes | **Crypto Sentiment Bot (§18.3)**. Defaults: `long_above=50.0, buy_below=25.0, contrarian=False, asset=BTC-USD` |
| §19.2 | `tradingbot/ts151_fundamentalmacrobot.py` | `TS151FundamentalMacroBot` | `multi_asset` | `M` | LO | Network | yes | **Fundamental Macro Bot (§19.2)**. Defaults: `window=3, allocations={'growth_up_infl_down': ['SPY', 'EFA'], 'growth_up_infl_up': ['DBC', 'GLD', 'EEM'], 'growth_down_infl_up': ['GLD', 'TIP', 'IEF'], 'growth_down_infl_down': ['TLT', 'IEF']}` |
| §19.3 | `tradingbot/ts151_globalinflationbot.py` | `TS151GlobalInflationBot` | `(TIP, GLD, DBC, VNQ, XLE, SPY, IEF)` | `M` | LO | Network | yes | **Global Inflation Bot (§19.3)**. Defaults: `threshold=3.0, window=60, trend_window=3, hedge_universe=['TIP', 'GLD', 'DBC', 'VNQ', 'XLE'], benchmark_weights={'SPY': 0.6, 'IEF': 0.4}` |
| §19.4 | `tradingbot/ts151_globalfixedincomebot.py` | `TS151GlobalFixedIncomeBot` | `country_bonds` | `M` | LO | OHLCV | yes | **Global Fixed Income Bot (§19.4)**. Defaults: `mom_window=126, rev_window=1260, k=2, gross=1.0` |
| §19.5 | `tradingbot/ts151_econannouncementbot.py` | `TS151EconAnnouncementBot` | `(SPY)` | `M` | LO | DB | yes | **Economic Announcement Bot (§19.5)**. Defaults: `kinds=['FOMC'], days_before=1, days_after=0, asset=SPY` |

---

## Excluded Strategies

Strategies from the book that were explicitly excluded from the framework, along with the technical or architectural rationale:

### 1. Latency-Sensitive High-Frequency Trading
- **§3.19 Market Making**: Requires sub-millisecond passive order placement, order-book queue position priority, and real-time microstructure signals. Incompatible with CronJob architecture.
- **§6.4 Intraday Index Arbitrage**: Requires microsecond cross-venue execution between index baskets and underlying cash equities. Unviable for batch cron jobs.
- **§8.5 Triangular FX Arbitrage**: Pure latency race across interbank FX venues where mispricings vanish in milliseconds.

### 2. Unavailable Venues & Illiquid OTC Instruments
- **§5.14 CDS Basis**: Requires access to bilateral OTC Credit Default Swap contracts and sovereign/corporate debt obligations.
- **§5.15 Swap Spread Arbitrage**: Requires direct OTC interest rate swaps (IRS) trading against cash government bond desks.
- **§11.2–11.6 CDO Tranche Arbitrage**: Structured finance synthetic CDO and bespoke tranche trading; no retail/institutional API execution venue available.
- **§12.2 Convertible OAS Arbitrage**: Requires proprietary convertible bond pricing models with credit spread curves and OTC hybrid desk quotes.
- **§13.2 Cross-Border Tax Arbitrage**: Involves bilateral tax treaties, withholding tax reclamation dividend arbitrage, and OTC swap manufacturing.
- **§14.2 Spark Spread Arbitrage**: Electricity generation spark and dark spreads require physical power and natural gas delivery contracts.
- **§14.3 Weather Derivatives**: Trades CME weather degree day (HDD/CDD) contracts with illiquid OTC quotes and specialized meteorological models.
- **§15.2 Active Distressed Debt**: Involves participation in creditor committees, bankruptcy reorganization plans, and illiquid pre-packaged Chapter 11 workouts.
- **§16.6 Fix-and-Flip**: Physical residential/commercial real estate acquisition and renovation; not exchange tradeable.
- **§17.x Cash / Repo / Pawnbroking**: Bilateral institutional repo lending, reverse repo facilities, and physical collateralized lending.

### 3. Contract Roll Bias & Data Constraints
- **§6.2 Index Cash-and-Carry**: Requires holding explicit single-expiry futures contracts vs the spot basket. Continuous futures series on Yahoo Finance introduce fictitious roll jumps that distort P&L.
- **§9.6 Commodity Pricing Models**: Requires term structure estimation across multiple individual expired futures deliveries.
- **§10.2 Commodity Calendar Spreads**: Calendar spreads require concurrent trading of front and deferred single-expiry physical futures.

*(Note: Hyperliquid crypto funding carry is being evaluated in a separate dedicated module with perp-short support, per `docs/backtests/funding-carry-2026-11.md`.)*

