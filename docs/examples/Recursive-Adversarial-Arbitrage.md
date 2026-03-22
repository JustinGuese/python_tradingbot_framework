# **Recursive Adversarial Arbitrage: A Framework for Exploiting Structural Volatility Decay and Institutional Microstructure Through Multi-Agent Synthetic Reasoning**

The pursuit of asymmetric returns in modern capital markets has increasingly shifted from traditional fundamental analysis toward the exploitation of structural inefficiencies inherent in financial product design and market microstructure. While conventional wisdom dictates that high-risk strategies are synonymous with directional gambling, a more nuanced approach—bordering on what some might characterize as financial insanity—involves the systematic harvesting of mathematical erosion within leveraged instruments. This strategy, termed Recursive Adversarial Arbitrage, operates on the premise that the internal rebalancing mechanisms of leveraged exchange-traded funds (LETFs), the opaque footprints of institutional dark pools, and the emerging capabilities of adversarial artificial intelligence can be synthesized into a self-correcting, high-convexity engine. By intentionally engaging in behaviors that run contrary to standard retail logic, such as shorting the very instruments designed for hedging or utilizing multi-agent AI "debates" to invalidate trade signals, the strategy seeks to capture alpha within the "volatility sink" of the 21st-century market.

## **The Mathematical Foundation of Leveraged Erosion**

To comprehend the viability of a strategy bordering on insanity, one must first dismantle the structural mechanics of leveraged exchange-traded products. Leveraged ETFs are exchange-traded funds that employ financial derivatives—specifically futures contracts, total return swaps, and options—and debt to amplify the daily returns of an underlying index or benchmark.1 While a standard ETF maintains a 1:1 exposure, a 3x leveraged ETF (such as TQQQ for the Nasdaq-100 or SPXL for the S\&P 500\) seeks to deliver 300% of the daily move of its target.1 This amplification, however, is not a linear multiplier of long-term performance but a strictly daily mandate that necessitates a process known as daily rebalancing.3

## **The Daily Rebalancing Cascade and Volatility Decay**

At the conclusion of each trading day, a leveraged ETF must adjust its derivative positions to ensure that its exposure remains consistent with its stated leverage ratio for the following session.1 If the underlying index gains, the fund must purchase additional exposure at the market close; if it loses, it must sell.1 This mechanism creates a "rebalancing cascade effect," where all leveraged ETFs tracking a specific index are forced to trade in the same direction as the day’s move, potentially amplifying market volatility at the close.1

The byproduct of this constant resetting is "volatility decay," also known as beta slippage.1 This phenomenon is a mathematical certainty in non-trending markets. For example, if an index starts at $100, rises 10% on Day 1 ($110), and falls 9.09% on Day 2, it returns to $100. However, a 3x leveraged version would rise 30% to $130 and then fall 27.27% to $94.55.1 The 3x ETF has lost 5.45% of its value while the underlying index remained flat. This erosion is exacerbated by the "Constant Leverage Trap," where the fund is forced to buy high and sell low every single day.5

## **Quantification of Annualized Decay Impact**

The theoretical decay of a leveraged instrument is a function of the variance of the underlying index’s daily returns. The impact is particularly punishing for 3x and inverse 3x instruments, as illustrated in the following data synthesis.

| Instrument Type | Daily Volatility (σ) | Theoretical Monthly Decay | Theoretical Annual Decay |
| :---- | :---- | :---- | :---- |
| 1x Index Fund | 1.0% | 0.00% | 0.00% |
| 2x Leveraged ETF | 1.0% | \-0.21% | \-2.50% |
| 3x Leveraged ETF | 1.0% | \-0.63% | \-7.40% |
| 3x Leveraged ETF | 2.0% | \-2.48% | \-26.30% |
| \-3x Inverse ETF | 2.0% | \-4.61% | \-45.20% |
| \-3x Inverse ETF | 3.0% | \-9.92% | \-74.10% |

The data confirms that as daily volatility increases, the decay accelerates quadratically.1 In highly volatile or sideways regimes, a 3x leveraged ETF can lose nearly half of its value annually even if the index is unchanged.1 This "volatility sink" represents a massive transfer of wealth from long-term holders of these products to the structural mechanics of the market itself.7

## **Recursive Decay Harvesting: The Strategy of Counter-Intuitive Shorts**

The "insanity" of the proposed strategy lies in moving from being a victim of volatility decay to becoming its primary harvester. While most traders use 3x ETFs to bet on a market direction, the Recursive Adversarial Arbitrage strategy involves shorting both the 3x long ETF and the 3x inverse ETF simultaneously.10 This creates a delta-neutral or near-neutral position that profits from the decay of both instruments as they erode toward zero in a sideways or choppy market.10

## **Mechanics of the Dual-Short Volatility Arbitrage**

By shorting both TQQQ (3x Long Nasdaq) and SQQQ (3x Short Nasdaq), the trader is effectively selling "volatility insurance." Because both funds suffer from beta slippage, their combined value tends to decline over time.10 For example, in a year where the Nasdaq-100 is flat but volatile, both TQQQ and SQQQ might lose 25-50% of their value.1 A short position in both would capture this erosion as profit. This approach is mathematically superior to holding a single directional bet because it exploits a structural certainty—the math of compounding—rather than a directional guess.10

However, the risks are immense. In a strong, low-volatility trending market, one side of the short pair will expand exponentially.11 If the Nasdaq-100 rises 50% in a smooth line, TQQQ could rise 200%+, while the SQQQ short only gains a maximum of 100%.11 The resulting imbalance can lead to catastrophic margin calls. To survive this "insane" level of risk, the strategy must integrate a recursive rebalancing mechanism that mimics the adjusted leverage of a margin account rather than a constant leverage fund.12

## **Comparative Performance: Decay Harvesting vs. Passive Benchmarks**

The following table compares the outcomes of various rebalancing and leverage strategies over a 15-year backtest (2010–2025), focusing on the ability to mitigate the 99% drawdowns common in pure leveraged holdings.

| Strategy Configuration | CAGR (%) | Maximum Drawdown (%) | Sharpe Ratio |
| :---- | :---- | :---- | :---- |
| Buy & Hold TQQQ | 35.2% | \-99.1% | 0.45 |
| 50/50 TQQQ / TMF (Bi-Monthly Rebalance) | 23.8% | \-38.7% | 0.95 |
| 35/-15/65 TQQQ/SQQQ/TLT Mix | 21.4% | \-42.1% | 1.08 |
| Short TQQQ/SQQQ Pair (Delta Neutral) | 18.9%\* | \-51.2% | 0.88 |
| SPY (S\&P 500 Benchmark) | 10.2% | \-55.2% | 0.52 |

\*Note: Short pair returns are highly dependent on borrowing costs and rebalancing frequency.6

The data suggests that the "insane" approach of mixing long and short leveraged positions (e.g., the 35/-15/65 mix) can actually outperform the benchmark with lower volatility and smaller drawdowns than a pure directional bet.10 This is achieved by using the short side as a "volatility sponge" that absorbs the decay that usually destroys the long side.10

## **Institutional Footprints and the Architecture of Dark Pools**

To execute such a high-convexity strategy, the trader must possess an information advantage regarding the "real" movement of money. Retail volume is often dismissed by professionals as "noise".13 The true movers of the market are institutions executing $10M to $50M block trades in dark pools—private exchanges that do not report to the public "tape" in real-time.13 These trades leave "footprints" that can be detected by analyzing the microstructure of the order book and the speed of price action.13

## **Velocity Divergence and the Hawkes Self-Exciting Process**

The detection of institutional activity requires modeling "clustering" using the Hawkes Self-Exciting Process. This mathematical model recognizes that large institutional orders are rarely executed in single blocks; they are split into thousands of smaller orders to minimize slippage, creating a statistical "echo" or cluster.15 When these clusters appear, they indicate the "Alpha Window"— the time lag between institutional intent and the eventual retail-driven price breakout.15

A critical signal within this window is Velocity Divergence. This triggers when the speed of price movement contradicts the underlying accumulation of volume. For instance, if the price of an ETF is rising rapidly but the "Volume Pressure" (as measured by proprietary indicators like VTM) is fading, it suggests "Hidden Distribution" where smart money is selling into the retail-driven hype.15 Conversely, "Hidden Accumulation" occurs when price remains flat while volume builds into campaign-level patterns, signaling an imminent leg up.15

## **Real-Time Institutional Scorecard Metrics**

Advanced platforms now contextualize these footprints into a "Composite Score" (0–100) based on relative size, price clusters, and sweep urgency.13

| Metric Name | Description | Bullish Threshold |
| :---- | :---- | :---- |
| Relative Size | Trade size vs. 40-day average relative volume | \>10x Average |
| Sweep Clusters | Multiple aggressive "sweeps" across exchanges | \>3 in 20 Minutes |
| Wick Absorption | Price rejects a low on heavy institutional volume | High Intensity |
| Delta Flip | Real-time shift from selling to buying pressure | Positive Inversion |

When a ticker like TQQQ shows a Score \> 90 and heavy "Blue Bubble" dark pool accumulation, the probability of a "Gap & Go" setup increases significantly, providing the "insane" strategy with the conviction needed to skew its delta-neutral position toward a directional bias.13

## **Adversarial Artificial Intelligence: The Society of Thought**

Navigating the risks of 3x leveraged shorts and dark pool anomalies requires a reasoning engine that is immune to human bias and retail sentiment. Traditional single-agent AI models often suffer from "epistemic weaponization," where they selectively present facts to support a pre-existing bias.17 To counter this, the Recursive Adversarial Arbitrage strategy employs a Multi-Agent Debate (MAD) framework, also known as a "Society of Thought".18

## **Designing for Conflict and Authentic Dissent**

The core of the adversarial AI logic is to assign "opposing dispositions" to internal agents. It is not enough to ask a model to "analyze the market." Instead, the developer prompts the system to simulate a debate between a "Risk-Averse Doomer" (focused on regulatory limits and tail risks) and a "Growth-Focused Moonshot" (focused on momentum and institutional flow).19 This "design for conflict" forces the model to perform essential checks like verification and backtracking, avoiding the sycophancy common in single-agent interactions.19

In a real-world scenario, the "Moonshot" agent might identify a breakout in TQQQ based on dark pool footprints. The "Doomer" agent would then be tasked with disproving the signal by identifying "Hidden Distribution" or macroeconomic red flags like yield curve inversions.15 A third agent, the "Critical Verifier," acts as a judge, issuing a final verdict only when a mathematical consensus is reached.19

## **Stability Detection via Beta-Binomial Mixture Models**

The convergence of this multi-agent debate is tracked using a time-varying mixture of two Beta-Binomial distributions.20 This allows the system to model the dynamics of judge consensus and apply an "adaptive stopping" mechanism.

The judgment accuracy is modeled as:

![][image1]  
This formal mathematical approach ensures that the "insane" strategy only executes when the internal debate has stabilized, significantly reducing the probability of a false signal in choppy markets.20 Empirical studies have shown that this "society of thought" approach can double the accuracy of complex reasoning tasks compared to standard chain-of-thought prompting.19

## **Behavioral Arbitrage and the Sentiment Discrepancy Index**

A significant source of alpha in high-risk strategies is the "Sentiment Arbitrage"—the exploitation of the difference in market perception between retail and professional traders.21 While retail sentiment is often driven by social media hype and emotional reactions to price action, professional sentiment is grounded in fundamental analysis and institutional flow.21

## **The Sentiment Discrepancy Index (SDI) Calculation**

The strategy utilizes a Sentiment Discrepancy Index (SDI) to quantify this divergence. By monitoring forums like Reddit and Twitter via transformer-based natural language processing (NLP) models like FinBERT or XLNET, the system generates a "Retail Sentiment Score" (-100 to \+100).21 This is then contrasted with a "Professional Sentiment Score" derived from institutional reports and dark pool delta.21

The predicted price (![][image2]) is adjusted by the SDI using a weighted regression model:

![][image3]  
Where ![][image4] is volume, ![][image5] is turnover, and ![][image6] is the market index.24 When the SDI shows a massive positive divergence (retail extremely bullish while professionals are selling), the "insane" strategy initiates its double-short decay harvest, anticipating that the hype will eventually die down and the price will revert to the "liquidity voids" left behind by institutional selling.21

## **Sector Sensitivity to Behavioral Signals**

Research indicates that sentiment analysis is not equally effective across all industries. Technology and finance sectors show the highest sensitivity to social media sentiment, with a hybrid AI model achieving 68.5% directional accuracy in these areas.26 In contrast, healthcare and energy sectors are less reactive to retail "noise," requiring a more domain-specific focus on institutional footprints.26

| Sector | Sentiment Sensitivity | Primary Data Source | Accuracy (AI Hybrid) |
| :---- | :---- | :---- | :---- |
| Technology (e.g., TQQQ) | High | Reddit / Twitter / Dark Pools | 68.5% |
| Finance (e.g., FAS) | High | News / Institutional Reports | 64.2% |
| Energy (e.g., ERX) | Low | Macro / Geopolitical Data | 52.1% |
| Healthcare (e.g., LABU) | Low | Regulatory / FDA Filings | 49.8% |

This differentiation allows the Recursive Adversarial Arbitrage strategy to dynamically allocate its risk, focusing its "insane" decay harvesting on the tech sectors where retail-driven volatility is most likely to accelerate beta slippage.1

## **The Congressional Information Filter: Exploiting Political Alpha**

Another "borderline insane" component of the strategy is the tracking of personal stock trades by members of the U.S. Congress. It is well-documented that elected representatives often trade with a "privileged information filter" regarding government intervention, procurement contracts, and regulatory shifts.27

## **The STOCK Act and Abnormal Performance**

Before the passage of the STOCK Act, companies owned by politicians on average lost 1.4% in value immediately following the Act's passage, as the market recognized the loss of the "insider" edge.28 Furthermore, an increase in politicians' holdings of a firm’s stock positively predicted the likelihood of that firm being acquired in the next quarter by 160%.28

While the STOCK Act aimed to increase transparency, "Congressional Information Filters" still exist.29 The Unusual Whales Subversive Democratic Trading ETF (NANC) and Republican Trading ETF (GOP) provide real-time proxies for this activity.

| ETF Ticker | Return (2023 \- 2025\) | Strategy Bias | Alpha vs. S\&P 500 |
| :---- | :---- | :---- | :---- |
| NANC (Democrat) | 73% | Tech / Growth / ESG | \+12% |
| GOP (Republican) | 41% | Financials / Energy / Value | \-20% |
| Vanguard S\&P 500 | 61% | Diversified / Passive | 0% |

The massive outperformance of the Democratic-focused ETF (NANC) highlights the efficacy of using political trades as a macro-filter for the "insane" strategy.29 By aligning the long side of its decay-harvesting pair with the sectors favored by the most "successful" traders in Washington, the strategy gains an additional layer of non-public defense.29

## **Predicting the Unpredictable: AI and Black Swan Early Warning Systems**

The ultimate risk to any strategy involving 3x leverage—especially one shorting the volatility decay—is the "Black Swan" event. These are rare, high-impact events that are utterly unforeseen by traditional statistical models.31 To manage this, the strategy integrates a "Black Swan Early Warning System" that mines unconventional data sources and employs quantum-ready AI simulations.31

## **Precursors to Chaos: Deep Pattern Recognition**

AI algorithms, particularly neural networks like FinBERT and RoBERTa, excel at finding hidden relationships in chaotic data.26 By analyzing a multisource dataset including water scarcity indices, climate models, and "Conversational Surprise" in geopolitical transcripts, the system identifies areas of heightened risk.19

For 2026 and beyond, AI-powered models have identified several "likely" Black Swan events that could trigger 99% drawdowns in 3x leveraged tech funds:

* **Global Water Scarcity Crisis (20%-30% Likelihood):** Conflict over over-extraction and mismanagement leading to regional economic humanitarian disasters.31  
* **AI Model Misalignment (10%-20% Likelihood):** Paradoxical shocks caused by widely-deployed AI systems triggering unintended economic consequences via automated trading algorithms.31  
* **China-Taiwan Geopolitical Shocks:** A potential halt to major electronics shipping that could hit one-fifth of U.S. economic output.32  
* **The "DeepSeek Shock" Repetitions:** Technological breakthroughs in China that momentarily lead investors to question Western company valuations and tech indispensable-ness.32

## **The "Crash Filter" and Dynamic De-Leveraging**

To survive these shocks, the strategy employs an automated "Crash Filter." This is a daily rule-based switch that first checks the status of bonds (BND) and the 200-day moving average of the S\&P 500\.33 If the market is below its 200-day trend, or if the "Heat" (14-day RSI) on volatility instruments like UVXY spikes above 70, the system automatically de-leverages.33

The capital is then shifted into "Defensive Hedges":

1. **3x Long Treasuries (TMF):** Exploiting the historical flight-to-safety trade when stocks crash.6  
2. **Short-Term Bonds (BSV):** Minimizing interest rate risk while maintaining liquidity.34  
3. **Long USD (UUP):** Capturing the "Safe Haven" demand for the dollar during global liquidity crises.33

This proactive de-leveraging ensures that the strategy remains "Anti-Fragile," benefiting from the very chaos that would destroy a passive holder of 3x leveraged products.10

## **Strategy Synthesis: The Execution Workflow of Recursive Adversarial Arbitrage**

The implementation of this "insane" strategy is a multi-stage process that requires a unified platform for tracking institutional money flow and an autonomous AI governance structure.13

## **Phase 1: Regime and Liquidity Mapping**

The system begins by identifying the "Market Regime" through a combination of trend-following and momentum indicators. It specifically looks for "Liquidity Voids" and "Fair Value Gaps" (FVG) in the 3x leveraged ETFs.25 These gaps represent institutional trading points where price imbalances have occurred, creating a high probability of a "reversion to mean" that accelerates volatility decay.25

## **Phase 2: Multi-Agent Consensus Validation**

Once a "Liquidity Void" is identified, the Society of Thought initiates its debate.19 The "Planner" proposes the entry—for example, shorting TQQQ at a resistance cluster. The "Adversarial Verifier" checks Dark Pool prints and "Velocity Divergence" to ensure that the resistance is backed by institutional "Hidden Distribution".15 The "Judge" agent uses the Beta-Binomial consensus model to authorize the trade.20

## **Phase 3: Recursive Rebalancing and "Decay Harvesting"**

Once the trade is active, the system rebalances daily to maintain the target "Margin-Adjusted Leverage." This prevents the "Constant Leverage Trap" by ensuring that as the position becomes more profitable (i.e., as TQQQ and SQQQ lose value to decay), the actual exposure does not expand to a level where a single "V-shaped" recovery could wipe out the gains.7

## **Phase 4: Sentiment and Political Calibration**

The position is then fine-tuned using the Sentiment Discrepancy Index (SDI) and Congressional Trade data.24 If the "informational edge" from NANC suggests a tech-heavy tailwind, the delta-neutral short pair is adjusted to become "Long-Biased," while still capturing the decay of the inverse side.10

## **Conclusion: The Risk of Regulated Insanity**

The Recursive Adversarial Arbitrage strategy is a testament to the belief that in an irrational market, the only rational response is a structured, mathematical "insanity." By shorting the very engines of leverage and using adversarial AI to disprove its own signals, the strategy operates in a realm that few retail investors can access or understand. It replaces the gamble of directional bias with the certainty of mathematical erosion and the high-fidelity footprints of the world's largest institutions.1

However, the strategy remains subject to the "Black Box Problem" of AI and the ever-shifting regulatory environment.4 SEC Rule 18f-4 and the constant threat of a total ban on congressional stock trading could remove critical components of the informational advantage.4 Furthermore, certain Black Swan events—like a sudden geopolitical crisis in the Middle East or a catastrophic water shortage—may still escape even the most sophisticated deep pattern recognition systems.31 For those who choose to operate at this frontier, the reward is not just the potential for "super-profitable" returns, but the mastery of a system that turns the market’s own structural flaws into an engine of absolute alpha.

#### **Works cited**

1. Leveraged ETFs Explained: TQQQ, SQQQ & Decay Calculator (2026) \- Stock Titan, accessed on March 22, 2026, [https://www.stocktitan.net/articles/leveraged-etfs-how-they-work](https://www.stocktitan.net/articles/leveraged-etfs-how-they-work)  
2. Retail Trading of Leveraged ETFs \- NYU Stern, accessed on March 22, 2026, [https://w4.stern.nyu.edu/sternfin/Rosenwald/Nair\_Rosenwald\_Fellowship\_Paper\_May%202024.pdf](https://w4.stern.nyu.edu/sternfin/Rosenwald/Nair_Rosenwald_Fellowship_Paper_May%202024.pdf)  
3. Daily Rebalancing & Compounding: Impact on Leveraged ETFs, accessed on March 22, 2026, [https://leverageshares.com/us/insights/daily-rebalancing-compounding-impact-on-leveraged-etfs/](https://leverageshares.com/us/insights/daily-rebalancing-compounding-impact-on-leveraged-etfs/)  
4. SEC warns against ETFs with aggressive leverage \- Investing.com, accessed on March 22, 2026, [https://www.investing.com/news/economy-news/sec-warns-against-etfs-with-aggressive-leverage-93CH-4538742](https://www.investing.com/news/economy-news/sec-warns-against-etfs-with-aggressive-leverage-93CH-4538742)  
5. Portfolio performance with inverse and leveraged ETFs \- Open Journals at the University of Georgia Libraries, accessed on March 22, 2026, [https://openjournals.libs.uga.edu/fsr/article/download/3131/2798](https://openjournals.libs.uga.edu/fsr/article/download/3131/2798)  
6. 3x Leveraged ETF Strategy: 2,600% Return With 38% Drawdown ..., accessed on March 22, 2026, [https://medium.com/@setupalpha.capital/3x-leveraged-etf-strategy-2-600-return-with-38-drawdown-trading-strategy-rules-f4dad806bc25](https://medium.com/@setupalpha.capital/3x-leveraged-etf-strategy-2-600-return-with-38-drawdown-trading-strategy-rules-f4dad806bc25)  
7. Leveraged ETFs: A Risky Double That Doesnt Multiply by Two, accessed on March 22, 2026, [https://www.financialplanningassociation.org/sites/default/files/2021-08/MAY08%20Leveraged%20ETFs%20A%20Risky%20Double%20That%20Doesnt%20Multiply%20by%20Two.pdf](https://www.financialplanningassociation.org/sites/default/files/2021-08/MAY08%20Leveraged%20ETFs%20A%20Risky%20Double%20That%20Doesnt%20Multiply%20by%20Two.pdf)  
8. Leveraged ETFs in Asset Allocation: Opportunity or Trap? \- QuantPedia, accessed on March 22, 2026, [https://quantpedia.com/leveraged-etfs-in-asset-allocation-opportunity-or-trap/](https://quantpedia.com/leveraged-etfs-in-asset-allocation-opportunity-or-trap/)  
9. MEASURING THE PERFORMANCE OF LEVERAGED AND NON‑LEVERAGED ETF'S \- Semantic Scholar, accessed on March 22, 2026, [https://pdfs.semanticscholar.org/0182/883fd6fdade75d3e8b57490845de200b084e.pdf](https://pdfs.semanticscholar.org/0182/883fd6fdade75d3e8b57490845de200b084e.pdf)  
10. Can we have a more nuanced discussion about levered ETFs : r/investing \- Reddit, accessed on March 22, 2026, [https://www.reddit.com/r/investing/comments/ikle9q/can\_we\_have\_a\_more\_nuanced\_discussion\_about/](https://www.reddit.com/r/investing/comments/ikle9q/can_we_have_a_more_nuanced_discussion_about/)  
11. What the downside of shorting SQQQ and holding it? : r/stocks \- Reddit, accessed on March 22, 2026, [https://www.reddit.com/r/stocks/comments/skktt3/what\_the\_downside\_of\_shorting\_sqqq\_and\_holding\_it/](https://www.reddit.com/r/stocks/comments/skktt3/what_the_downside_of_shorting_sqqq_and_holding_it/)  
12. Removing volatility decay via rebalancing strategy to mimic margin : r/LETFs \- Reddit, accessed on March 22, 2026, [https://www.reddit.com/r/LETFs/comments/1pakpit/removing\_volatility\_decay\_via\_rebalancing/](https://www.reddit.com/r/LETFs/comments/1pakpit/removing_volatility_decay_via_rebalancing/)  
13. Retail volume doesn't move markets, $50M Dark Pool prints do. I ..., accessed on March 22, 2026, [https://www.reddit.com/r/Daytrading/comments/1qa27py/retail\_volume\_doesnt\_move\_markets\_50m\_dark\_pool/](https://www.reddit.com/r/Daytrading/comments/1qa27py/retail_volume_doesnt_move_markets_50m_dark_pool/)  
14. Do dark pools make volume worthless? : r/Daytrading \- Reddit, accessed on March 22, 2026, [https://www.reddit.com/r/Daytrading/comments/uwi5n6/do\_dark\_pools\_make\_volume\_worthless/](https://www.reddit.com/r/Daytrading/comments/uwi5n6/do_dark_pools_make_volume_worthless/)  
15. MXC VTM \- Institutional Footprint Alpha | PDF \- Scribd, accessed on March 22, 2026, [https://www.scribd.com/document/1001201413/MXC-VTM-Institutional-Footprint-Alpha](https://www.scribd.com/document/1001201413/MXC-VTM-Institutional-Footprint-Alpha)  
16. 7 Volume Analysis Signs of Institutional Activity \- Deepvue, accessed on March 22, 2026, [https://deepvue.com/technical-analysis/volume-analysis-secrets/](https://deepvue.com/technical-analysis/volume-analysis-secrets/)  
17. Adversarial Dialectics: Mitigating AI Persuasion Risks through High-Fidelity Multi-Agent Debate | Apart Research, accessed on March 22, 2026, [https://apartresearch.com/project/adversarial-dialectics-mitigating-ai-persuasion-risks-through-highfidelity-multiagent-debate-c9vw](https://apartresearch.com/project/adversarial-dialectics-mitigating-ai-persuasion-risks-through-highfidelity-multiagent-debate-c9vw)  
18. Debater Agents: Multi-Agent Reasoning \- Emergent Mind, accessed on March 22, 2026, [https://www.emergentmind.com/topics/debater-agents](https://www.emergentmind.com/topics/debater-agents)  
19. AI models that simulate internal debate dramatically improve ..., accessed on March 22, 2026, [https://venturebeat.com/orchestration/ai-models-that-simulate-internal-debate-dramatically-improve-accuracy-on](https://venturebeat.com/orchestration/ai-models-that-simulate-internal-debate-dramatically-improve-accuracy-on)  
20. Multi-Agent Debate for LLM Judges with Adaptive Stability Detection \- arXiv, accessed on March 22, 2026, [https://arxiv.org/html/2510.12697v1](https://arxiv.org/html/2510.12697v1)  
21. Sentiment Arbitrage \- DayTrading.com, accessed on March 22, 2026, [https://www.daytrading.com/sentiment-arbitrage](https://www.daytrading.com/sentiment-arbitrage)  
22. News Impact on Stock Price Return via Sentiment Analysis | Request PDF \- ResearchGate, accessed on March 22, 2026, [https://www.researchgate.net/publication/261919209\_News\_Impact\_on\_Stock\_Price\_Return\_via\_Sentiment\_Analysis](https://www.researchgate.net/publication/261919209_News_Impact_on_Stock_Price_Return_via_Sentiment_Analysis)  
23. A Study on the Performance of ML Algorithms for Stock Market Prediction \- ResearchGate, accessed on March 22, 2026, [https://www.researchgate.net/publication/395045525\_A\_Study\_on\_the\_Performance\_of\_ML\_Algorithms\_for\_Stock\_Market\_Prediction](https://www.researchgate.net/publication/395045525_A_Study_on_the_Performance_of_ML_Algorithms_for_Stock_Market_Prediction)  
24. User Classification and Stock Market-Based Recommendation Engine Based on Machine Learning and Twitter Analysis \- SciSpace, accessed on March 22, 2026, [https://scispace.com/pdf/user-classification-and-stock-market-based-recommendation-3mefblmf.pdf](https://scispace.com/pdf/user-classification-and-stock-market-based-recommendation-3mefblmf.pdf)  
25. Interpretable image-based deep learning for price trend prediction in ETF markets | Request PDF \- ResearchGate, accessed on March 22, 2026, [https://www.researchgate.net/publication/375220580\_Interpretable\_image-based\_deep\_learning\_for\_price\_trend\_prediction\_in\_ETF\_markets](https://www.researchgate.net/publication/375220580_Interpretable_image-based_deep_learning_for_price_trend_prediction_in_ETF_markets)  
26. AI driven sentiment analysis in financial markets: using transformer base models and social media signals for stock market predictions, accessed on March 22, 2026, [https://www.emerald.com/jm2/article/doi/10.1108/JM2-08-2025-0415/1336098/AI-driven-sentiment-analysis-in-financial-markets](https://www.emerald.com/jm2/article/doi/10.1108/JM2-08-2025-0415/1336098/AI-driven-sentiment-analysis-in-financial-markets)  
27. Political Connections and the Informativeness of Insider Trades, accessed on March 22, 2026, [https://ideas.repec.org/p/ecl/stabus/3473.html](https://ideas.repec.org/p/ecl/stabus/3473.html)  
28. “Trading” Political Favors: Evidence from the Impact of the STOCK Act \- GW School of Business, accessed on March 22, 2026, [https://business.gwu.edu/sites/g/files/zaxdzs5326/files/HuangXuan\_STOCKAct.pdf?utm\_so=](https://business.gwu.edu/sites/g/files/zaxdzs5326/files/HuangXuan_STOCKAct.pdf?utm_so)  
29. The 2 ETFs That Track Congressional Stock Trades | Morningstar, accessed on March 22, 2026, [https://www.morningstar.com/funds/2-etfs-that-track-congressional-stock-trades](https://www.morningstar.com/funds/2-etfs-that-track-congressional-stock-trades)  
30. Does It Pay to Copy Congress' Stock Trades? \- Morningstar, accessed on March 22, 2026, [https://www.morningstar.com/funds/does-it-pay-copy-congress-stock-trades-2](https://www.morningstar.com/funds/does-it-pay-copy-congress-stock-trades-2)  
31. The Unseen Threads of Chaos: Predicting Black Swan Events with AI and Quantum Computing \- Top Management College in Kolkata \- Praxis Business School, accessed on March 22, 2026, [https://praxis.ac.in/the-unseen-threads-of-chaos-predicting-black-swan-events-with-ai-and-quantum-computing/](https://praxis.ac.in/the-unseen-threads-of-chaos-predicting-black-swan-events-with-ai-and-quantum-computing/)  
32. BCA Research looks at potential “black swan” events facing markets in 2026 \- Investing.com, accessed on March 22, 2026, [https://www.investing.com/news/stock-market-news/bca-research-looks-at-potential-black-swan-events-facing-markets-in-2026-4459997](https://www.investing.com/news/stock-market-news/bca-research-looks-at-potential-black-swan-events-facing-markets-in-2026-4459997)  
33. Which Way Is Up? Trading 1.2 – Composer, accessed on March 22, 2026, [https://www.composer.trade/trading-strategies/which-way-is-up-trading-12-nMBHxKNJcAgP6vboPdPz?q=regime-rotating](https://www.composer.trade/trading-strategies/which-way-is-up-trading-12-nMBHxKNJcAgP6vboPdPz?q=regime-rotating)  
34. TQQQ For The Long Term V2 (226.7% RR/46.1% Max DD) \- Composer.trade, accessed on March 22, 2026, [https://www.composer.trade/trading-strategies/tqqq-for-the-long-term-v2-2267-rr461-max-dd-gjv5JfCW8jtb6RNv6ugw?q=long+volatility+%28UVXY%29](https://www.composer.trade/trading-strategies/tqqq-for-the-long-term-v2-2267-rr461-max-dd-gjv5JfCW8jtb6RNv6ugw?q=long+volatility+\(UVXY\))  
35. A Meta-Adaptive Framework Combining Gated Attention Mechanisms and Feature-wise Modulation for Multi-Horizon Stock Price Movement Prediction \- ResearchGate, accessed on March 22, 2026, [https://www.researchgate.net/publication/400666540\_A\_Meta-Adaptive\_Framework\_Combining\_Gated\_Attention\_Mechanisms\_and\_Feature-wise\_Modulation\_for\_Multi-Horizon\_Stock\_Price\_Movement\_Prediction](https://www.researchgate.net/publication/400666540_A_Meta-Adaptive_Framework_Combining_Gated_Attention_Mechanisms_and_Feature-wise_Modulation_for_Multi-Horizon_Stock_Price_Movement_Prediction)  
36. Artificial Intelligence in Finance: Predicting Crashes Before They Happen” | MEXC News, accessed on March 22, 2026, [https://www.mexc.com/news/755099](https://www.mexc.com/news/755099)

[image1]: <data:image/png;base64,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>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABIAAAAYCAYAAAD3Va0xAAABNElEQVR4Xo2UMU5DMQyGHQmGChBC5RLdWThCF87ABifoAeg12JhRB1aG7kxMDN2Q4AbtgIRaO3Zf/Pzs5H2qlcT+88cv76kAESn/XHS+zCO1YSjjzDBfw6i7pe9iGvZFAUPxE8Y3FvY4UvzyOsef5H6KPKIYH40MaYKaPco2tuJxDmSSYG0LQnCIhjuay6Pd9YuZK3CNhndEvIsRddYh0iWwyaOuRTgnZmZywItO+r0AnEExsrHDmBZpZMHQvdCmB1rUpbDAWPUy6ktdAxtdj/gmb4CfwCF59xPZRXmAC2CTD1swnGJ8YvzbgpDuQd1PhTcZSXvCU+7uWZIq8mu+ZJHLLcYXTeIHrNJtIxMy82BR8wQWUNc0vvZqmpF2W+C/lpZQ1422tbXKqM2daJQ601A2yg0O7iY8U9JcYskAAAAASUVORK5CYII=>

[image3]: <data:image/png;base64,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>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACIAAAAYCAYAAACfpi8JAAABpUlEQVR4Xr2VoU4DQRCG5yQJSQUJGCQGDTwCEoOAB8CgSTAoEsIbkKD7DpgSVCUKg0SAJYBCkQIzN729nbnZu9m7pl+YtvvPP7tz290C4KbQQh5Dys1aU2zitC2TrJaUOau2H9YSaxhvGH9R0FiAhZ/zHAV9XpeOAaiuroEXOZCygPLWwwRSyZQeiAzUAC10UUuCW4x9LbYSr97SiU5to0KNPCidnPT1vWixfnXiNK8C78irTiDvGCthFCZ0ztwDauRHKAUc4utVr0VzSyJ/dSsqKDWLxkvjG+TNuMfYqtMBOkcnWrR3QJwlensEPgatPKOXGtkAbmBqTw5HkFi2hXOMXeAHbWukoL8xsHEHY1YqLpw2JjSiqsTwDNj4gXEcJ+Zsov8O3790IoOuHSnZAzbSdbWYAl/j+EA36NifdCNRIZ0NMo5qqcElxk00nkB926zQ/5PSjbRhPN0vxsjQEzROgt0I29zT0o7h+Sio4FQnS8yphCgb8d8IAdXQL+9TLDgZQ/yVFeVvloeMJSyc5U7bYulctNOQxQJnGz5VaoZY58+V8g+uy0fquryB9QAAAABJRU5ErkJggg==>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADQAAAAYCAYAAAC1Ft6mAAACJ0lEQVR4Xu2WvUoEMRCAJ6CFnBaiiKCVr6CN+ABqoYXtPYBvoA/gG6gvYO1fYSVYHNiIgmAh1lpYWGilYOFPJpPcJrOZ7K675w/4ccNeZpK5mclkcwAxlPmkKZxQlcYd/l3+S0E0W4dGvElOtH5PPz4CUWwcCudSyz1k9jsrj55usjv7G8AfHI/oePAjEZ1jCMjWMaOweudANlxfgFT28kxp2Wa6YaAALpgeueUKyzLgGmWenE274+vc0Auw3QaYrm0DWDGjrGiY6E535KPgBKgILW7S3ADZFrihNpENnecKMDujMABsIx8MdiJwkn03LRrxP2iL88QNKSJ+ahE7PzIKWorm4y75zAHpD5i+AnVSo7WjQEGcMn2KRaA1D5C94XD8rqXfm/cjrAIFQ+enHB2Inx9sM0wqxpou1CFXlmRLyzRXSlwDBcfPTwqpRZ2vPm7QzEC+ABaxJbATUJ6hQkJScBKDQPM7NAyCEXyxt4oYv0jphPLnpxhsTVzD7x93l4UJKXOmrrS8BfpqlEtIF2oDKIA2tyXAvz2xFnXFcQlhImdajuyYtSLbpvjV4Egm5N5Qkki8QH7uq7FkAexaPT79N94s0GXrOAbng/2HtNfBmDcXSSbUA/IlZehkFCZVgOinSkKiE5lK3WJwO78faMtTJaEekmWHAeHlK+QrsgT5Nq9L1Rh+CYVhR/uqcFWCOmujJBwmTD40reTkQpry8wWa/el63j4B2OSLj6aKrucAAAAASUVORK5CYII=>

[image6]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACoAAAAYCAYAAACMcW/9AAAB+ElEQVR4Xt2VzysFURTHzyuKEAuFUuwtJCwslAVlZWHjT1CiZGdhaaksLZSekiWS/Ss71liKvc0rC4nnnHfunTlz7r3z45V58alvc+ecM3O/986ZGYA2UtGB3KRcmZJyKVT8H9AL1uc+8tSURNPKHuoV1cDTBh7vEyWu3RpQLdfTdQfJ9K+QMEGT1zHyLoOKCdQucO2KygVxlppBsB4TXXh4QN0CmwjxhDoGrulVuVJYRK1DbEJQsUs8RHWj6uDUpBDcnqLwjW5Qg6hVYBNDooLoB37kBOVrNuH1kTPoRrL5NsdJYCOzIkfQS0PYfEp/tjJ9ktAdbH8SfcBGduI0rKHGzdi2Rk+ULRHbn3YpZOTM5ChybcZEqD/HUF86mJMlVJWHob1kTH9GRWTkxYzvMG4TdEz0p4B2eEYHMxhAbQBvijGaju1PyxvqEzWFmhfxacjsz5ao4hZkGpX9aakBG9LxExPX389H1DNqNIqEnqA/TiYTRn1lV8Afeck+sKFOFf8wccky8LeVfgRzKpcXx6jkCHhSKZqQWEBtmzGh60gXIt9hYhYyr+ultuLSJqlGY3z7nIvowk3UuUgUJadRsFMWdRzV0yeL/mSXca4QRY22zCnwCzWsExnQS6nbYiRR8afw7qI3WALtmrdE5BLVckOrr/hSbqQIP9EVaOoQNwnUAAAAAElFTkSuQmCC>