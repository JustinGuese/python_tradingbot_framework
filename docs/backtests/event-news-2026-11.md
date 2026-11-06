# Event study E2: stock_news headline drift (2026-11)

**Verdict:** `no edge`

Script: `scripts/onetime_event_news_study.py`. Data: 11,502 `stock_news` headlines, 84 symbols
(no duplicates found per symbol per day), scored by a fixed finance word list with negation
handling (no LLM). Entry = first daily close after the headline's session (no look-ahead).
Abnormal return = stock return - beta x QQQ return (beta from 250 days before the event).
Pass rule: t >= 2, alpha > 0 in both halves, beta < 0.5. The variant grid was fixed in the script
header before the run, and all 12 strategy variants are reported.

## Results (verbatim script output)

```
--- EVENT-LEVEL ABNORMAL RETURNS (%) ---
          bucket horizon    n  mean_abn_pct    t_stat  h1_mean_pct     h1_t  h2_mean_pct      h2_t flag
        positive     +1d 3674     -0.039632 -1.301201     0.136681 3.001890    -0.208983 -5.187793   ok
        positive     +3d 3674     -0.041500 -0.834888     0.385824 5.329239    -0.451950 -6.752000   ok
        positive     +5d 3674     -0.099796 -1.629425     0.496559 5.757018    -0.672603 -7.926384   ok
        negative     +1d 1533     -0.033103 -0.771901     0.054909 0.872486    -0.126195 -2.184074   ok
        negative     +3d 1533      0.035798  0.545731     0.256931 2.678551    -0.198100 -2.243914   ok
        negative     +5d 1533     -0.027928 -0.321203     0.177966 1.608917    -0.245707 -1.820288   ok
         neutral     +1d 6295     -0.021654 -0.964962     0.096141 2.912327    -0.140615 -4.650662   ok
         neutral     +3d 6295      0.006557  0.184325     0.332615 6.380562    -0.322727 -6.769149   ok
         neutral     +5d 6295     -0.050624 -1.077461     0.495605 7.580257    -0.602260 -9.114517   ok
     top_tercile     +1d 3834     -0.053526 -1.818156     0.136681 3.001890    -0.221851 -5.872836   ok
     top_tercile     +3d 3834     -0.082688 -1.693829     0.385824 5.329239    -0.497300 -7.687546   ok
     top_tercile     +5d 3834     -0.110443 -1.841669     0.496559 5.757018    -0.647613 -7.940013   ok
  bottom_tercile     +1d 3834      0.002876  0.098053     0.034004 1.011838    -0.126195 -2.184074   ok
  bottom_tercile     +3d 3834      0.114437  2.471231     0.189814 3.560946    -0.198100 -2.243914   ok
  bottom_tercile     +5d 3834      0.200719  3.360086     0.308387 4.638832    -0.245707 -1.820288   ok
top_minus_bottom     +1d 3834     -0.056402 -1.357306     0.102677 1.814365    -0.095657 -1.385680   ok
top_minus_bottom     +3d 3834     -0.197125 -2.929621     0.196009 2.180202    -0.299200 -2.733752   ok
top_minus_bottom     +5d 3834     -0.311162 -3.676102     0.188172 1.727940    -0.401906 -2.548367   ok

--- PORTFOLIO STRATEGY VARIANTS (vs QQQ) ---
          variant  hold_days direction  passes     alpha         t      beta   n  h1_alpha      h1_t  h2_alpha      h2_t
 long_positive_h1          1      long   False  0.034150  0.939662  0.036533 964  0.000000  0.000000  0.078439  1.083360
 long_positive_h3          3      long   False -0.015892 -0.500450  0.046964 964  0.000000  0.000000 -0.018597 -0.294835
 long_positive_h5          5      long   False  0.022823  0.728528  0.048405 964  0.000000  0.000000  0.059137  0.952022
   top_tercile_h1          1      long   False  0.034320  0.944245  0.036549 964  0.000000  0.000000  0.078784  1.088012
   top_tercile_h3          3      long   False -0.015738 -0.495526  0.047030 964  0.000000  0.000000 -0.018270 -0.289621
   top_tercile_h5          5      long   False  0.024145  0.774614  0.048022 964  0.000000  0.000000  0.061670  0.997833
short_negative_h1          1     short   False -0.017778 -0.627562 -0.025210 964  0.000000  0.000000 -0.042568 -0.753339
short_negative_h3          3     short   False -0.019940 -0.537817 -0.047989 964  0.000000  0.000000 -0.053262 -0.722462
short_negative_h5          5     short   False  0.009411  0.212068 -0.081423 964  0.000000  0.000000 -0.003996 -0.045526
bottom_tercile_h1          1     short   False -0.015498 -0.366657 -0.062669 964  0.001376  0.964867 -0.049910 -0.594929
bottom_tercile_h3          3     short   False  0.015521  0.412577 -0.091550 964  0.000481  0.065562  0.005042  0.068697
bottom_tercile_h5          5     short   False  0.009561  0.269574 -0.110995 964 -0.005128 -0.422141 -0.004621 -0.068135

Final Verdict: no edge
```

## Reading it

- **No portfolio variant passes.** Best long-only alpha is +3.4%/yr at t 0.94 (hold 1 day).
- **The strong-looking event-level t-stats are mostly not sentiment.** The `neutral` bucket shows
  the same pattern as the positive one (H1 up, H2 sharply down, t down to -9), so the half-split
  mostly measures the market regime and the beta adjustment, not the headlines.
- **The only sentiment-specific signal is top minus bottom:** -0.31% at +5d (t -3.7), a reversal
  (positive headlines underperform). It flips sign between halves (H1 +0.19%, H2 -0.40%) and
  needs a short leg the framework cannot execute. Not usable.
- **Strategy halves are partly empty** (H1 alpha 0.000 for most variants): yfinance only returns
  recent news, so most headlines are from late in the sample and the early daily series is flat.
  Treat the half-split of the portfolio rows as thin.
- Short variants are shown for completeness; the framework is long-only.
