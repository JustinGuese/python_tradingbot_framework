# **The Synthesized Hyper-Convexity Engine: Exploiting Path-Dependency, Gamma Cascades, and Sentiment Discrepancies in 3x Leveraged Equity Derivatives**

The modern financial landscape is increasingly dominated by derivative-embedded instruments that exhibit non-linear behavior, path-dependency, and extreme sensitivity to liquidity-driven flows. Among these, triple-leveraged Exchange Traded Funds (LETFs) such as ProShares UltraPro QQQ (TQQQ) and UltraPro Short QQQ (SQQQ) represent a unique frontier for sophisticated quantitative strategies. While traditional investment mandates view these assets as high-risk tactical tools suitable only for intra-day exposure, a deeper analysis reveals that their mathematical structural inefficiencies—specifically the interaction between volatility drag, gamma-neutral hedging, and stage-based cyclical transitions—can be synthesized into a high-potential, albeit high-risk, algorithmic framework. This report proposes the Synthesized Hyper-Convexity Engine (SHCE), a strategy that integrates Stan Weinstein’s Stage Analysis, options market gamma mechanics, and AI-driven sentiment discrepancy indexing to harvest alpha from the structural "slippage" and "ignition" phases of 3x leveraged instruments.

## **The Mathematical Architecture of Leveraged Convexity and Path-Dependent Decay**

To understand the viability of a high-leverage strategy, one must first deconstruct the mechanical engine of the 3x LETF. Unlike a static margin position, which maintains a fixed dollar-value debt, a leveraged ETF must rebalance its exposure at the end of every trading session to maintain a constant leverage ratio.1 This daily reset mechanism is the source of both the "compounding bonus" in trending markets and the "volatility drag" in sideways or choppy regimes.2 The SHCE strategy is predicated on the ability to distinguish between these two environments using high-density technical and sentiment filters.

## **Volatility Drag and the Quadratic Cost of Leverage**

The performance of a 3x leveraged ETF is not a simple linear function of its underlying index. The daily rebalancing creates a geometric effect where the fund’s value is eroded by the variance of the underlying returns.2 This erosion, known as beta slippage or leverage decay, is particularly punishing for 3x funds because the decay factor grows quadratically with the leverage multiplier.4

![][image1]  
In this approximation, ![][image2] represents the leverage factor, ![][image3] is the daily volatility, and ![][image4] is the time duration.2 For a 3x fund, the drag term involves a coefficient of 3, meaning the mathematical penalty for volatility is significantly higher than that of a 2x or 1x fund.5 The implication for the SHCE strategy is that exposure must be strictly avoided during periods of high "Volatility of Volatility" (Vol-of-Vol), as the uncertainty in the variance estimate further compounds the expected shortfall.6

## **Shortfall from Maximum Convexity (SMC) as a Volatility Proxy**

Traditional measures of risk, such as standard deviation, often fail to capture the true risk profile of LETFs because they assume a normal distribution of returns and independence of price action, neither of which holds true for path-dependent 3x instruments.7 To address this, the SHCE utilizes the "Shortfall from Maximum Convexity" (SMC) metric. SMC measures the gap between the actual return of an LETF and the theoretical "Maximum Convexity" return that would be achieved if the underlying index moved in a perfectly constant, low-volatility trend.7

![][image5]  
Where ![][image6] represents the upper bound of a leveraged, daily-compounded return for a given period, achieved when daily index returns are equal to the geometric mean.7 High SMC values serve as a leading indicator of tracking error and structural slippage, signaling the strategist to rotate out of 3x leverage before the decay becomes terminal.7

| Leverage Type | Leverage Factor (L) | Volatility Drag Coefficient (2L2−L​) | Path Sensitivity | Maximum Convexity Potential |
| :---- | :---- | :---- | :---- | :---- |
| Standard (1x) | 1 | 0 | Neutral | Low |
| Leveraged (2x) | 2 | 1 | Moderate | Medium |
| Hyper-Leveraged (3x) | 3 | 3 | Extreme | High |
| Inverse ( \-3x) | \-3 | 6 | Absolute | High (Short Side) |

The table above demonstrates the escalating cost of leverage. The jump from 2x to 3x is not linear; it is a tripling of the volatility drag.4 The SHCE strategy seeks to capture the "High" potential of 3x leverage only when the "Path Sensitivity" is biased toward a persistent, low-volatility trend.2

## **The Structural Framework: Stan Weinstein’s Stage Analysis as a Trend Filter**

A "wacky" or "insane" strategy involving 3x leverage cannot succeed as a static "buy-and-hold" play.1 It requires a robust macro-cyclical filter to identify regimes where momentum is sufficiently strong to overcome volatility drag. The SHCE utilizes Stan Weinstein’s Stage Analysis—a methodology originally developed for weekly charts—to categorize the lifecycle of the Nasdaq-100 index (the "Forest") and individual 3x ETFs (the "Trees").10

## **Identifying the Transition from Stage 1 to Stage 2**

The core profit engine of the SHCE is the "Stage 2 Advancing" phase. This phase begins when the 30-week Simple Moving Average (SMA) flattens out and the price breaks above a defined resistance level on expanding volume.10 During Stage 1 (Basing), supply and demand are in equilibrium, and the 3x ETF is likely to suffer from sideways churn and beta slippage.12 The strategy mandates that 3x long exposure (TQQQ) is only initiated when the following conditions are met:

1. The 30-week SMA is sloping upward or has transitioned from a downward slope to a flat profile.14  
2. The price has closed above the Stage 1 resistance range.10  
3. The Mansfield Relative Strength (RS) has crossed into positive territory, indicating that the asset is outperforming the broader market.10  
4. Volume expansion on the breakout is at least 50% above the average weekly volume, confirming institutional accumulation.10

## **The Danger of Stage 3 and the Pivot to Stage 4**

Conversely, the strategy utilizes Stage 3 (Topping) as an exit signal for long positions and Stage 4 (Declining) as an entry signal for 3x inverse positions (SQQQ).14 Stage 3 is characterized by erratic price swings, high volatility, and a flattening 30-week SMA—a lethal combination for a leveraged long position due to the compounding of volatility drag.12 When the price breaks below the 30-week SMA on heavy volume, the "Markdown" phase (Stage 4\) begins, and the SHCE pivots to SQQQ to exploit the downward momentum.14

| Stage | Phase | 30-Week SMA Slope | Volatility Environment | Strategic Action |
| :---- | :---- | :---- | :---- | :---- |
| Stage 1 | Accumulation | Flat | Low / Contracting | Neutral (Wait for Breakout) |
| Stage 2 | Advancing | Rising | Moderate / Trending | Long (Max Convexity TQQQ) |
| Stage 3 | Distribution | Flat / Topping | High / Erratic | Neutral (Exit to Cash) |
| Stage 4 | Declining | Falling | High / Downward | Short (Hyper-Leveraged SQQQ) |

The transitions between these stages represent the most critical inflection points for the SHCE. By aligning the 3x leverage with the "Smart Money" cycle identified by Weinstein, the strategy avoids the "retail noise" of Stage 1 and the "capitulation risk" of Stage 3\.13

## **The Momentum Ignition: BB/KC Squeeze and Volatility Contraction**

To pinpoint the exact entry for a hyper-leveraged trade, the SHCE incorporates the Bollinger Band (BB) and Keltner Channel (KC) Squeeze indicator.17 This technical overlay identifies periods where volatility is unusually low, suggesting that the market is "coiling" for an explosive move.18

## **The Mechanics of the Squeeze**

A "Squeeze" occurs when the Bollinger Bands—which are based on standard deviation—move inside the Keltner Channels, which are based on Average True Range (ATR).17 This condition signals that the current volatility is extremely low relative to the historical average.20 For a 3x leveraged asset, this is the ideal entry point, as it precedes a volatility expansion where momentum can generate the "compounding bonus" before the drag becomes excessive.2

The SHCE monitors the relationship between the EMA centerline and the channel widths. When the Bollinger Band Width reaches multi-period lows, the strategist looks for a "Momentum Trigger," such as an RSI cross or a volume spike, to confirm the direction of the breakout.20

## **Avoiding the Head-Fake**

One of the primary risks of a squeeze-based strategy is the "False Breakout" or "Head-Fake," where the price briefly breaks out of the channel only to reverse sharply.17 To mitigate this, the SHCE requires a confluence of the BB/KC Squeeze with the Stage 2 criteria.16 A squeeze that breaks upward *while* the 30-week SMA is rising and Mansfield RS is positive is considered a high-probability "Hyper-Ignition" setup.10

## **The Gamma Turbine: Liquidity Cascades and Dealer Hedging Dynamics**

The most "insane" element of the proposed strategy involves the exploitation of the options market’s plumbing—specifically the "Gamma Squeeze." This phenomenon occurs when market maker hedging behavior creates a self-reinforcing feedback loop that pushes prices vertically, regardless of fundamentals.22

## **Delta-Neutral Hedging and Negative Gamma**

Market makers, who sell call options to retail traders, must remain delta-neutral to manage their risk. If a stock’s price rises, the delta of the call options they sold increases, forcing them to buy more of the underlying stock to stay hedged.22 When market makers are "Short Gamma," they are forced to buy into rising markets and sell into falling ones, which amplifies price momentum.23

The SHCE identifies "Negative Gamma Zones" by analyzing the net gamma exposure of dealers across various strike prices on the Nasdaq-100.23 If the price approaches a strike cluster with heavy negative gamma while the market is in a Stage 2 environment, the strategy enters TQQQ with a "Max Convexity" sizing.23

## **The VIX Catalyst and the Air Pocket**

The Volatility Index (VIX) acts as a critical proxy for the cost of option premiums. When the VIX falls, the delta of out-of-the-money (OTM) options tends to shrink, allowing dealers to unwind their hedges.23 This mechanical unwinding creates "buying pressure" that can trigger a gamma squeeze.23 The SHCE looks for the "Air Pocket" setup: a breakout through a gamma-loaded level accompanied by falling volatility (VIX).23 In this environment, the 3x leverage of TQQQ is perfectly positioned to capture the non-linear "Buying Cascade" that results from forced dealer rebalancing.22

| Indicator | Bullish Gamma Context | Bearish Gamma Context | Strategy Implication |
| :---- | :---- | :---- | :---- |
| Dealer Positioning | Short Gamma (Buy on Rises) | Long Gamma (Sell on Rises) | Momentum Acceleration vs. Stabilization |
| VIX Trend | Declining (Unwinding Hedges) | Rising (Increased Hedging) | Mechanical Support vs. Panic Selling |
| Strike Proximity | Near OTM Call Clusters | Near OTM Put Clusters | Upside Squeeze vs. Downside Cascade |
| Price Action | Breakout with Vol Expansion | Breakdown with Vol Expansion | High-Convexity Entry Signal |

By monitoring these "mechanical flows," the SHCE can anticipate moves that are driven by the structure of the market rather than by news or earnings.23 This is the "wacky" advantage: trading the machine, not the asset.

## **The Sentiment Sentinel: AI-Driven Discrepancy Indexing**

Retail investor behavior, often driven by fear and greed, creates sentiment cycles that precede major market reversals.27 The SHCE utilizes AI and Natural Language Processing (NLP) to monitor social media platforms—Telegram, Reddit, Twitter—and news feeds to construct a Sentiment Discrepancy Index (SDI).28

## **Quantifying Euphoria and Panic**

The SDI is a normalized Z-score that compares the intensity of social media volume and sentiment polarity against the actual momentum of the price.28

1. **Retail Euphoria (Stage 2/3 Peak):** High social volume \+ record sentiment scores \+ stalling price action. This discrepancy indicates that the "Smart Money" is distributing shares to the "Dumb Money," signaling a potential Stage 3 top.13  
2. **Retail Panic (Stage 4/1 Bottom):** High social volume \+ extreme negative sentiment \+ selling climax in volume. This suggests a transition from Stage 4 to Stage 1, where institutions begin "quietly building positions".15

## **The Discrepancy Trading Logic**

The SHCE uses the SDI as a contrarian filter. If the Nasdaq-100 is in Stage 2 and the SDI shows "Extreme Euphoria," the strategy prepares to exit TQQQ, even if technical indicators are still bullish.27 This prevents the strategist from being "caught in the herd" when a distribution phase turns into a breakdown.16 Conversely, an "Extreme Panic" reading in the SDI during a Stage 4 decline is the signal to close SQQQ and wait for the Stage 1 basing phase to develop.10

## **Position Sizing and Optimization: The Vol-of-Vol Kelly Criterion**

A strategy involving 3x leverage and gamma squeezes is essentially a high-stakes bet. Traditional "naive" position sizing is the most common cause of bankruptcy for leveraged traders.30 To manage this, the SHCE employs a modified Kelly Criterion that incorporates "Uncertainty about Risk" (Volatility of Volatility).6

## **The Kelly Fraction in a Leveraged Context**

The Kelly Criterion formula determines the optimal fraction (![][image7]) of the portfolio to risk on a single trade to maximize the long-term geometric growth rate of wealth.31

![][image8]  
Where ![][image9] is the probability of winning, ![][image10] is the probability of losing, and ![][image11] is the odds (ratio of amount won to amount lost).31 In a 3x leveraged environment, the "odds" are distorted by the path-dependency of the instrument. If the win-loss ratio is unfavorable, the Kelly formula will suggest betting nothing (![][image12]), effectively protecting the portfolio from "Gambler's Ruin".3

## **Adjusting for Vol-of-Vol**

Recent research into quantitative risk management suggests that the "Naïve Kelly" leverage multiplier should be reduced as the variance of volatility (Vol-of-Vol) increases.6 For the SHCE, this means that even if a Gamma Squeeze signal is triggered, the position size in TQQQ will be scaled down if the historical uncertainty of the Nasdaq’s volatility is rising.6

| Sizing Strategy | Basis | Risk Tolerance | 3x Leverage Suitability |
| :---- | :---- | :---- | :---- |
| Naïve (Full) Kelly | Historical Returns/Vol | High (Agressive) | Low (Risk of Ruin) |
| Fractional (Half) Kelly | 50% of Full Kelly | Moderate (Conservative) | Medium |
| Double Kelly | 200% of Full Kelly | Extreme (Reckless) | Failure (Guaranteed Ruin) |
| Vol-of-Vol Kelly (SHCE) | Variance of Volatility | Dynamic (Quant-Led) | High |

By betting only a fraction of the Kelly-optimal stake during uncertain periods, the SHCE achieves smoother equity curves with significantly fewer dramatic swings.4

## **The Synthesized Execution Architecture: The Hyper-Convexity Engine (SHCE)**

The synthesis of these disparate elements results in a coherent, rules-based algorithmic strategy. The SHCE operates on a daily "Cronjob" interval, processing signals at the close of the market to determine the positioning for the following session.1

## **The "Long Ignition" Signal Sequence**

The "Long Ignition" setup is the primary profit driver of the strategy, combining macro trend alignment with micro liquidity squeezes.

1. **Macro Filter:** The Nasdaq-100 (Forest) must be in Stage 2 (Price \> Rising 30-week SMA).10  
2. **Volatility Squeeze:** The Bollinger Band width for TQQQ must be within the Keltner Channels, indicating a compression of volatility.17  
3. **Liquidity Trigger:** The price of the Nasdaq-100 must be within 1.5% of a "Short Gamma" strike cluster with a declining VIX trend.23  
4. **Sentiment Confirmation:** The SDI must show "Healthy Optimism" (Z-score between 0.5 and 1.5), indicating room for further retail participation.27  
5. **Execution:** Initiate TQQQ long with a position size determined by the Vol-of-Vol Kelly calculation.6

## **The "Black Swan" Circuit Breaker**

Because 3x leverage is prone to "Gap Risk"—where the price opens significantly lower than the previous close—the SHCE incorporates a hard "Black Swan Filter".9 If TQQQ experiences a daily decline of 20% or more, or if the Nasdaq-100 drops 7% (triggering a market-wide circuit breaker), the strategy immediately liquidates all 3x positions and moves 100% of capital into 7-10 Year Treasury ETFs (IEF) or cash.9 This "crash filter" reduces the catastrophic drawdowns that typically plague leveraged buy-and-hold strategies.9

## **Simulation and Performance Analysis**

Historical simulations of the SHCE components suggest a significant advantage over passive Nasdaq-100 exposure. A 50/50 rebalanced portfolio of TQQQ and Treasury bonds (TMF) with a simple crash filter has historically delivered returns exceeding 2,600% over 15 years, with manageable drawdowns compared to the 69% peak-to-trough decline of pure TQQQ.9

## **Impact of SMC-Based Volatility Adjustments**

By utilizing the SMC metric to rank 3x funds, the SHCE can rotate out of the most "decay-prone" assets. For instance, in periods of high volatility for gold miners or the Russia index, funds like DUST or RUSS exhibit significantly higher SMC values than TQQQ, indicating that their structural slippage is far more aggressive.7 The SHCE restricts its "Hyper-Leverage" to indices with the highest "Maximum Convexity" potential and the lowest structural shortfall.7

## **The Role of Mansfield RS in Alpha Generation**

The inclusion of Mansfield Relative Strength ensures that the 3x leverage is only applied to assets that are leading the market. In a "Bifurcated" market—where small caps or regional banks are lagging while tech is leading—the SHCE will naturally gravitate toward TQQQ, capturing the institutional flow that gravitates toward high-quality, large-cap growth.10

## **Conclusion: The Case for a Disciplined, Non-Linear Strategy**

The Synthesized Hyper-Convexity Engine (SHCE) is designed for a market regime where mechanical flows, sentiment cascades, and structural volatility play a larger role than fundamental valuation. While the use of 3x leverage is inherently "insane" by the standards of traditional asset management, the integration of Stan Weinstein’s cyclical stages with the modern reality of gamma-hedging and AI sentiment provides a sophisticated "edge" that mitigates the most toxic effects of beta slippage.

By strictly adhering to the "Modified Kelly" sizing and the "Black Swan" circuit breakers, the SHCE seeks to transform the 3x LETF from a "dangerous" tactical tool into a "precision" wealth-generation engine. The strategy acknowledges that while quiet periods in the market do not last forever, they provide the necessary coiling energy for explosive, high-convexity moves. For the quantitative strategist who can remain patient during the Basing phase (Stage 1\) and disciplined during the Distribution phase (Stage 3), the SHCE offers a robust framework for exploiting the recursive nature of modern liquidity and the non-linear potential of derivative-driven equity markets. The future of alpha generation lies not in the rejection of leverage, but in the mastery of its mathematical and psychological constraints.

#### **Works cited**

1. What Is TQQQ? How This 3x Leveraged ETF Works \- TradingKey, accessed on March 22, 2026, [https://www.tradingkey.com/learn/intermediate/etf/what-is-tqqq-how-3x-leveraged-etf-works-tradingkey](https://www.tradingkey.com/learn/intermediate/etf/what-is-tqqq-how-3x-leveraged-etf-works-tradingkey)  
2. Leveraged ETFs and Volatility: SPXL and TQQQ Guide \- MenthorQ, accessed on March 22, 2026, [https://menthorq.com/guide/leveraged-etfs-and-volatility-spxl-and-tqqq/](https://menthorq.com/guide/leveraged-etfs-and-volatility-spxl-and-tqqq/)  
3. Is volatility drag a real phenomenon? : r/investing \- Reddit, accessed on March 22, 2026, [https://www.reddit.com/r/investing/comments/1n6egw6/is\_volatility\_drag\_a\_real\_phenomenon/](https://www.reddit.com/r/investing/comments/1n6egw6/is_volatility_drag_a_real_phenomenon/)  
4. The Kelly Criterion \- Quantitative Trading \- Nick Yoder, accessed on March 22, 2026, [https://nickyoder.com/kelly-criterion/](https://nickyoder.com/kelly-criterion/)  
5. Leveraged ETFs Explained: TQQQ, SQQQ & Decay Calculator (2026) \- Stock Titan, accessed on March 22, 2026, [https://www.stocktitan.net/articles/leveraged-etfs-how-they-work](https://www.stocktitan.net/articles/leveraged-etfs-how-they-work)  
6. The Kelly criterion in the presence of uncertainty about risk \- Outcast Beta, accessed on March 22, 2026, [https://outcastbeta.com/the-kelly-criterion-in-the-presence-of-uncertainty-about-risk/](https://outcastbeta.com/the-kelly-criterion-in-the-presence-of-uncertainty-about-risk/)  
7. Shortfall from Maximum Convexity, accessed on March 22, 2026, [https://arxiv.org/pdf/1510.00941](https://arxiv.org/pdf/1510.00941)  
8. (PDF) Simulation of Leveraged ETF Volatility Using Nonparametric ..., accessed on March 22, 2026, [https://www.researchgate.net/publication/285216939\_Simulation\_of\_Leveraged\_ETF\_Volatility\_Using\_Nonparametric\_Density\_Estimation](https://www.researchgate.net/publication/285216939_Simulation_of_Leveraged_ETF_Volatility_Using_Nonparametric_Density_Estimation)  
9. 3x Leveraged ETF Strategy: 2600% Return With 38% Drawdown (Trading Strategy Rules), accessed on March 22, 2026, [https://medium.com/@setupalpha.capital/3x-leveraged-etf-strategy-2-600-return-with-38-drawdown-trading-strategy-rules-f4dad806bc25](https://medium.com/@setupalpha.capital/3x-leveraged-etf-strategy-2-600-return-with-38-drawdown-trading-strategy-rules-f4dad806bc25)  
10. Stage Analysis Trading: Master Stan Weinstein's 4-Stage Method \- Aron Groups, accessed on March 22, 2026, [https://arongroups.co/forex-articles/stage-analysis-trading/](https://arongroups.co/forex-articles/stage-analysis-trading/)  
11. Stage Analysis: Stocks Trading & Investing Stan Weinstein's Four Stage Method, accessed on March 22, 2026, [https://www.stageanalysis.net/](https://www.stageanalysis.net/)  
12. The Ultimate Guide to Breakout Momentum Investing \- Stockopedia, accessed on March 22, 2026, [https://www.stockopedia.com/academy/articles/breakout-momentum/](https://www.stockopedia.com/academy/articles/breakout-momentum/)  
13. The Complete Guide to Stan Weinstein's Stage Analysis \- TraderLion, accessed on March 22, 2026, [https://traderlion.com/trading-strategies/stage-analysis/](https://traderlion.com/trading-strategies/stage-analysis/)  
14. Master Market Trends with AI-Powered Weinstein Stage Analysis | TrendSpider Blog, accessed on March 22, 2026, [https://trendspider.com/blog/master-market-trends-with-ai-powered-weinstein-stage-analysis/](https://trendspider.com/blog/master-market-trends-with-ai-powered-weinstein-stage-analysis/)  
15. Stage analysis: an overview \- AlphaTarget, accessed on March 22, 2026, [https://alphatarget.com/resources/stage-analysis-an-overview/](https://alphatarget.com/resources/stage-analysis-an-overview/)  
16. Stan Weinstein Trading Strategy: A Forex Trader's Guide \- ThinkCapital, accessed on March 22, 2026, [https://www.thinkcapital.com/stan-weinstein-trading-strategy-forex-funded-traders/](https://www.thinkcapital.com/stan-weinstein-trading-strategy-forex-funded-traders/)  
17. BB/KC Squeeze: A Powerful Indicator for Trading Range Breakouts \- TrendSpider, accessed on March 22, 2026, [https://trendspider.com/learning-center/bb-kc-squeeze-a-powerful-indicator-for-trading-range-breakouts/](https://trendspider.com/learning-center/bb-kc-squeeze-a-powerful-indicator-for-trading-range-breakouts/)  
18. Are Keltner Channels Better Than Bollinger Bands? \- TradersPost, accessed on March 22, 2026, [https://blog.traderspost.io/article/keltner-channel-trading-strategies](https://blog.traderspost.io/article/keltner-channel-trading-strategies)  
19. TTM\_Squeeze \- thinkorswim Learning Center, accessed on March 22, 2026, [https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/T-U/TTM-Squeeze](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/T-U/TTM-Squeeze)  
20. Keltner Channels vs Bollinger Bands: 7 Proven Differences Crypto Traders Need \- Mudrex, accessed on March 22, 2026, [https://mudrex.com/learn/keltner-channels-vs-bollinger-bands-crypto/](https://mudrex.com/learn/keltner-channels-vs-bollinger-bands-crypto/)  
21. The Keltner Channel Strategy: How it Works and When it's Used \- PU Prime, accessed on March 22, 2026, [https://www.puprime.com/the-keltner-channel-strategy-how-it-works-and-when-its-used/](https://www.puprime.com/the-keltner-channel-strategy-how-it-works-and-when-its-used/)  
22. Understanding the Gamma Squeeze | Charles Schwab, accessed on March 22, 2026, [https://www.schwab.com/learn/story/understanding-gamma-squeeze](https://www.schwab.com/learn/story/understanding-gamma-squeeze)  
23. Gamma Squeeze Explained Guide \- MenthorQ, accessed on March 22, 2026, [https://menthorq.com/guide/gamma-squeeze-explained/](https://menthorq.com/guide/gamma-squeeze-explained/)  
24. What is a gamma squeeze and how does it affect stock prices? \- IG Group, accessed on March 22, 2026, [https://www.ig.com/en/trading-strategies/what-is-a-gamma-squeeze-and-how-does-it-affect-stock-prices--211006](https://www.ig.com/en/trading-strategies/what-is-a-gamma-squeeze-and-how-does-it-affect-stock-prices--211006)  
25. How a Gamma Squeeze Works \- Simpler Trading, accessed on March 22, 2026, [https://www.simplertrading.com/blog/trading-psychology/how-a-gamma-squeeze-works](https://www.simplertrading.com/blog/trading-psychology/how-a-gamma-squeeze-works)  
26. Understanding the Gamma Squeeze | TrendSpider Learning Center, accessed on March 22, 2026, [https://trendspider.com/learning-center/understanding-the-gamma-squeeze/](https://trendspider.com/learning-center/understanding-the-gamma-squeeze/)  
27. Impact of Social Media on Financial Market Trends: Combining Sentiment, Emotion, and Text Mining \- ResearchGate, accessed on March 22, 2026, [https://www.researchgate.net/publication/390056364\_Impact\_of\_Social\_Media\_on\_Financial\_Market\_Trends\_Combining\_Sentiment\_Emotion\_and\_Text\_Mining](https://www.researchgate.net/publication/390056364_Impact_of_Social_Media_on_Financial_Market_Trends_Combining_Sentiment_Emotion_and_Text_Mining)  
28. Cryptocurrency Sentiment Analysis Trading Strategy \- Sourcetable, accessed on March 22, 2026, [https://sourcetable.com/ai-trading-strategies/sentiment-analysis-cryptocurrency](https://sourcetable.com/ai-trading-strategies/sentiment-analysis-cryptocurrency)  
29. Genetic Algorithm Based Approach for Algorithmic Trading in ..., accessed on March 22, 2026, [https://www.researchgate.net/publication/373462878\_Genetic\_Algorithm\_Based\_Approach\_for\_Algorithmic\_Trading\_in\_Financial\_Markets](https://www.researchgate.net/publication/373462878_Genetic_Algorithm_Based_Approach_for_Algorithmic_Trading_in_Financial_Markets)  
30. Why Do Even Excellent Traders Go Broke? The Kelly Criterion and Position Sizing Risk, accessed on March 22, 2026, [https://medium.com/@idsts2670/why-do-even-excellent-traders-go-broke-the-kelly-criterion-and-position-sizing-risk-62c17d279c1c](https://medium.com/@idsts2670/why-do-even-excellent-traders-go-broke-the-kelly-criterion-and-position-sizing-risk-62c17d279c1c)  
31. The Kelly Criterion: Optimal Bet Sizing for Investing and Gambling \- proficient-project.eu, accessed on March 22, 2026, [https://proficient-project.eu/the-kelly-criterion-optimal-bet-sizing-for-investing-and-gambling/](https://proficient-project.eu/the-kelly-criterion-optimal-bet-sizing-for-investing-and-gambling/)  
32. Kelly Criterion Position Sizing for Optimal Returns \- QuantifiedStrategies.com, accessed on March 22, 2026, [https://www.quantifiedstrategies.com/kelly-criterion-position-sizing/](https://www.quantifiedstrategies.com/kelly-criterion-position-sizing/)  
33. Kelly criterion \- Wikipedia, accessed on March 22, 2026, [https://en.wikipedia.org/wiki/Kelly\_criterion](https://en.wikipedia.org/wiki/Kelly_criterion)  
34. The Optimal Leverage Indicator : r/LETFs \- Reddit, accessed on March 22, 2026, [https://www.reddit.com/r/LETFs/comments/1l9krm3/the\_optimal\_leverage\_indicator/](https://www.reddit.com/r/LETFs/comments/1l9krm3/the_optimal_leverage_indicator/)  
35. Wealth Management Perspectives \- Morgan Stanley, accessed on March 22, 2026, [https://advisor.morganstanley.com/the-shoreline-group-13529244/documents/field/s/sh/shoreline-group/MSWM\_Slides\_20241113\_160746\_763\_Andrew.Plocker.pdf](https://advisor.morganstanley.com/the-shoreline-group-13529244/documents/field/s/sh/shoreline-group/MSWM_Slides_20241113_160746_763_Andrew.Plocker.pdf)

[image1]: <data:image/png;base64,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>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA0AAAAYCAYAAAAh8HdUAAAA1klEQVR4XpWQvQ0CMQyFXwo6mpNYBoagRrqSlinoGYjuJEpqFqBnhiOJzeHYTgKf5Mh+/kkc4E9C3SFfhi7dApiaWgvp9lzI7zlH5xltFpbii6ip8mlg7Hx1YojOHP2JS5jGFZERdMteJwxizh25KayV3kTtU0FMG0ANk/9RVkn09tlpIc3hfZD3Kcm3vLSaSF+t9lneeWAr2CA1BNwoLN6/yjkxgXzgxIlR7Xtk/fGVAq4s9mzL9Yx03F91RXgTXNpZRWOmVVx+LFtw662oFAprZbWfBN5jhCk8a2KwFAAAAABJRU5ErkJggg==>

[image3]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAwAAAAXCAYAAAA/ZK6/AAAAwElEQVR4XmNgQAaMKDwEQBHHpYgBSQqPGgQgShEyYMSmh4DbcIpglcAUpABADCPfVCkgDsGCRSDSCFM1gfg/HDMisSG4F1mxGVTQFe48BoY9QPwPrgIOICpAiiegiftCxTHAJAYMCbApW5HFkcPjABB/gnGQQgykeAqqOAQ0APFbGAcqUQZk/EURQQMgyRgGiOx0IP4IZSMoDDYDgzYQBwAxK4YcdkvwA6TgRgWYElgUEQ9I1oxTA6bDsAAC0lAAAIv3HI7dPXyBAAAAAElFTkSuQmCC>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAcAAAAYCAYAAAA20uedAAAAr0lEQVR4XmNgwACM2NjECyIDrIK4AFbFjG+BxAl0UZBikPL/QFyELAgDxgwQSXG4CFDWGUiEAPE+qCSIDcJgjQFAMgTI+APkPoRKBEB1ghWAEEhXOlwQDhhh9jGKoEuBwHwg/g9xG6YfvwIFr6ILAgEj2H+MCP+xMIADAmICD0gSai8I3AKyWZENB9sJwkBBbiRxTAdAAMSDyAw0cawAjxQEwBWgmYkmggZwWYokCABynxfW1MncGgAAAABJRU5ErkJggg==>

[image5]: <data:image/png;base64,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>

[image6]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADUAAAAYCAYAAABa1LWYAAAChUlEQVR4XtWWT6hNURTGv13InydDKUUGSpKkFCMDyksZGBGDlwkDUzEmA2UgGZhLZmavDN7gjhEGSEoiGShJYUBc67tr73P2WWfvc+89zn33+tW6Z+9vrbP32n/PBdrgMuUco8TEjBvfHd323G1rndKQWoOrNZNo83/hsth7sb63j77+IdJOFtExMz9rrhhARRROe53PzsjNR06vMzxyzmniPesQdkB976xj1pmHJs6nwZ2H+h5Yz6yzBE183aBWXdnf3reyohqGb4blJ3GesFHsp9hXTC3n9t1ydTggDoC3Hu2b17ZHcYb2HRouWKELjiF9nm55fY3RCSfiFNS/WMrFQLdCffdQbzfmBtIX0EGx+9A2rnltl6+/QnObA3qIz1PJWa8fCIJZm21i55BO6jb03dXWETEn9ljse63lErZxwpeZx77I10jqPJFHUH23dXiuim1C/d1L0M/AH6NXcXgqv3tRfz+GK/1FbIvELxhflvXQRntGJ2GwvDAKojl9JrYC1aS4VfeI3URlW9ZgzE6kJyWGfdN/veg3u6glC9CXjhud9KH/Mjhw8iN2QhNnJ4wJnwLOLPksdsiXUzzxT27BpkHxM0J/aLdxTHdQrkRsnHnF4ajXnot9gm6pAM9TqP8S2yx2GOX7fC93ni6i/v8yTFwM83+J8iIbhaYxD4XnKcCLgh2H/4bZ8+R05q8Yue90kixvozIHdSSq4x/zT/IiKt+FHuaAbEu3mOnzoRUwSNjZrfra1MOFMS4mi3RS+1Fu1bAavPo3iK2KfLQ33k/CLUfreW1tpNF4RuP6vM/hjNErF9fkSE9Anob4nCun1xg5sA3L1ngoT6LDsdocK3jaTC/Zv+AJkKoZ/4A7AAAAAElFTkSuQmCC>

[image7]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABMAAAAYCAYAAAAYl8YPAAABQElEQVR4Xq2SP0oDURDG54GCoBGsbOzFwsYUgniBNBYqaOUlcgYvIOQA9mlzgdwhYCOkiGUQBQUrndlv/7yZefvMRn/wsfPmz/dmlyVqIbjA0JbvivfxmX8kbX7Iemad2oIQj/hxnbnh4ys/h6xv1nZVQFv4qM6rIAYPrPcyru6S+DZg4xHrpTRHNcEeYehcZZv+E0Jd+iLShgNCc88WCPk7wmaPPD4vsgmfY9Y1a0oyFIpYtKm6gP9m5n3PCMNvhObKrCaxQIHJq6O8yjhOrIv8AmJ2aQt56mXUVn2C2b4v/YZvvqfyv1oPbThjfarMH5CtJgj92j4To6sbBLMLlTXkDRuOCGZbtqCI3RLOYrCk4uOHhS12YYdgNuRbvviiA9tgN8ExsVLJFeuJtWsLHrjlDdvyq5Cfzd8L8lVPx/4ftj0t7W0qPrsAAAAASUVORK5CYII=>

[image8]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmwAAAA/CAYAAABdEJRVAAAEFUlEQVR4Xu3dPagcVRQA4BNMQImgIUEJBoSAARFUUBTBUmxshNhpEbAQW+2CbQpbKwshKIiFdmpnkVJIoYUhjUUigiAEIaCgwZ97uLO+2fvyfnbf/szO+z44zJsz+/t237wzZ2buRDSOtAkAAACAcdMOAQAA2Dy25YCDsh4BAIBlUnEDAADjdcRGDwCwB8UCAAAAAAAAwNrYZQsAAAAAAACMgF2fAAAjpMgDluhcib9K/FviTLMMAICB+ChqwQYAwEDdLvFzmwQAYDiyu3axTQIAMJ+bUQusd7vpx930lf6NZpT3/6rEdyV+LPH09OKFuy+2XvvfUV/741O3AADYUI90069j+pizZ5r5WZyIet9He7l5H2s/Hojpx3+hmYeN5ARBACYud9M/Slzr5bM7tVPRk2eBft8me16PWgD25WOdbnKLkh21/vFyWYTu9NoBAPZjcNvNp6IWOC/2cp92ubv5IXZelm6VONubn3TcjvdyfVnI7RUn/7/1tEtRH7u/+7PtFgKs2IHW8we6MzBeb0UtcI72cne63Dza+2XH7c8mtyhXYvvz5Wu/2uQAADZa7grNImfi4ahF0PlebhZtAfVrieeb3KJ8E9PPl0VnzmeRCACHkz7tKGWBkzH5eH8r8fnW4pl9G1uPdSG2F3CL1J5g8Hs3n7thAQAWKjtF97bJFckCJ7tqGQ81y+b1bImX2+QSPVfiWIkbUYs2AIC5fRi1e5XX2kxvRx1D7IuoXamcX6V8zmV2wFYt38vlNglAj91ljNuBv+FPRC0o/umm6Z6og8tej1rE5fwq5ZmhN9rkkO3yKeSZqfl7zTHkYH92+UIBC+cvjo3wSxd92W3LYi1H6H+yxCfTi5cqrwaQuy1f62KTPVXi1RKPRX0vrnIAsGzKL0Yquz85vtndXClxf5sEGBT/oIERy47PG1ELtve7eas9AIAByQLtYtSC7UI3D8Aq2DwGZjDEyyVNxl8bcwAA7NvtEjfbJAATWmHA+mW354M2uamsVtk4vrQA7CGHmMiC7Uy7AGD8VMvAZngnHE8FrIViCWC/robrWz4YdVDbU+0CAIAhyEtRjeb4tTmci3rZLbuFAWAbnfB1Oh21QLnWTQ+77DJm4QrsZhTr7VG8CeCQOBq1UPsydJVS/i5yLDoAAAYqC7aX2iQAAMORBdtPUfeVHCtxfXoxABxmDiVg/XIcuvYs2Szgjjc5AADWJM+QvdTksmDLEzMAgF3pvrEat0qc7c2fCB02AIBBaYc1+Szq2bMAwKjoBm6yO72f85PMAs4nCgAwIOdLvFniZNRi7b3pxeulcgQAAAAAAIbIPgyAQ8IKHwCAWakhAQAA4EBsWgMAAAAAAAAAAOPk6CgAAAAAAAAAAAAA1s5BrbAg039M/wFpdsvP/rG51gAAAABJRU5ErkJggg==>

[image9]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAXCAYAAAAyet74AAAA4klEQVR4XmNgAANGCIUEMEXgAE0Kj0pSAcQoOEmMycSowQUw9cJFMG3HVAwHSFLWQHwXiGWg4lZA6hEQR0GkISr5gXgVEJsCuf+hCrQgChgOAdXsgSleDlXsAmSDFCog2eXLANEMFvCGCm4F8kGCyE5qZYAo5EEIQQQmofnyH1QcDqShAppIYiDngMQmIIkxlEMFBZFM2wzEfxFcCHjAAFbI+AhoLyNQ8TQg/ysDlsAFmTYHiJmBOIQBzfEwAApkkGn6KPpRPQUGsCCAiKPLIgGQaSCFIOyJLonQiGYdjQAW6wDVdyRHSptqogAAAABJRU5ErkJggg==>

[image10]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAkAAAAZCAYAAADjRwSLAAAA7ElEQVR4XpVRKw7CQBTcDZBwAC5AgyFBcIhaFE0QnAOH5QJoDDdB4rgAipBUEAQSQ5nd997+2hWddNo3s/O6r1ulAG1uHSA/XXVat9faCBNc55q8nyRyDZkBu5w+6Ncdpb0YoP7geQPv4B58gKUERkg3CK3FAJ5gA38sxgv8Jmd4UTYkksRVBOMN1iK2ml5bJp9mGg8izmT4vYECDSZUiHFSwd4Ebd4QeVM2hqyXrOv0YDf2CGhxx7WbxyLpmCsKu3liaHsdFc/jm12lJ7hV4I9Zafsn4tCMQwtwxTUHfEgKV3dt56y8w1OHUvAHjcYmihek9iUAAAAASUVORK5CYII=>

[image11]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAZCAYAAAAMhW+1AAAAzElEQVR4XmNgYGSAAjgDDjBFkAARkjiUwCXR5J8A8X8otgEJIMnD9SRAFXBgmAKlTwPxP6gQVgDSvRVdEAYEGSAKXNAlYCCaAaJgCxCfB+I7QPwI2SMg+/8DBeRhAoxgDYwNECYjWBJiP0LbfyD7IYLDwNAKl4KLMYIUMGpCFSghSUIczciwB2RkEVQBMoA52hKZgwxeAfFXmIP4GSAKYCGcAOWzQtRCZHyAJMhOkMRxmCARAIcqHMIM2GWwiWEVhHkBQRIBMBTCBZAYAGMXJ1jgB5uZAAAAAElFTkSuQmCC>

[image12]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADcAAAAYCAYAAABeIWWlAAACFUlEQVR4Xu2WPUsEMRCGJ6Ag+IWVCIKlWNh4hSBqb2OhgoLgT7CxEOxtLQQbsbG3tbE6LK0VbAQLLUUUFBTknLnZ7CZz2Wz260T04Ya7e2cmmdnkkgNoo/itEOG54ZF/lv9HFMQk2h3arBbqe271jexiHe0ZbQethdZvu+FNfHdTb81U0ztwfWSLtjsdCj5Ee40+m/oG8IoeoT0avoIUegLDwLVMGNoX8GJ4GQFOnJeOqIwZYD/FFaytNNdoN0JbBnshnCwBBw1KB7C+Bbxyp2j3ljc3/ifj8VIdNL/JeKQ3EikZYRptDe0SOIg+k/XGEUmw+M2llWHqaTG5GQWu70ToY5G+J/Q2c8DNvAAXr5vzU1nNNp5hcWVUCwN2ha6bOxO6RWaAd+pONtGOc9gBp6VC2w5rVB3NKa69KfQYOl4pYEU60sjVZiZBo0XNRSuXpOiVa8aKoIHBFED7OnCuekibWiVN7AuX1uVBE0MJFJCB0i8/HJB3W9L9atAxCwmuJvRpSae9E7o76NYvR0c9lUP/nuQ9p6+wPqHHkPNciqWrLZnuYAG4VnNkunsvjO8WPcAJdNPnq6e+YA9qG5Lt+Yl2ZfttpiBjWbtCVb1HUENPwIfJg/AFkaseZ7BTLIA9zgBwc/Rv+gP4xLFRMuXnMevJqm0V7RZtyFKzsn4fXegobYo8yyEpv7vKpJfJ/XECiw8Mq4JvdjZmjk864pwAAAAASUVORK5CYII=>