# TSMOM agreement filter with real C2 sleeves (idea 6 redo)

Replica check: corr(vectorised TSMOM base, real TSMOMTrendBot sleeve) = 0.956.
Blend = 0.39 real TARegimeMultiAsset + 0.37 real GoldenButterfly + 0.24 TSMOM variant. Holdout (2024+) untouched; n_variants=3 for t_adj.

| level   | variant               | period                      | alpha   |    t |   t_adj |   beta | max_dd   |
|:--------|:----------------------|:----------------------------|:--------|-----:|--------:|-------:|:---------|
| sleeve  | tsmom_base            | fit                         | +0.54%  | 0.25 |   -2.15 |   0.15 | -24.1%   |
| sleeve  | tsmom_base            | validate                    | +2.68%  | 0.67 |   -1.72 |   0.1  | -14.9%   |
| sleeve  | tsmom_and_st          | fit                         | +0.78%  | 0.41 |   -1.98 |   0.06 | -24.0%   |
| sleeve  | tsmom_and_st          | validate                    | +0.82%  | 0.26 |   -2.14 |   0.05 | -16.0%   |
| sleeve  | tsmom_and_st_at       | fit                         | +0.67%  | 0.36 |   -2.03 |   0.05 | -25.3%   |
| sleeve  | tsmom_and_st_at       | validate                    | +1.82%  | 0.57 |   -1.82 |   0.04 | -16.0%   |
| blend   | tsmom_base            | H1 2017-06-02..2020-09-15   | +1.01%  | 0.48 |   -1.92 |   0.1  | -8.7%    |
| blend   | tsmom_base            | H2 2020-09-16..2023-12-29   | +0.78%  | 0.3  |   -2.1  |   0.14 | -8.5%    |
| blend   | tsmom_base            | full 2017-06-02..2023-12-29 | +0.81%  | 0.48 |   -1.91 |   0.12 | -8.7%    |
| blend   | tsmom_and_st          | H1 2017-06-02..2020-09-15   | +1.63%  | 0.83 |   -1.56 |   0.09 | -7.0%    |
| blend   | tsmom_and_st          | H2 2020-09-16..2023-12-29   | +0.07%  | 0.03 |   -2.36 |   0.12 | -8.0%    |
| blend   | tsmom_and_st          | full 2017-06-02..2023-12-29 | +0.78%  | 0.51 |   -1.88 |   0.1  | -8.0%    |
| blend   | tsmom_and_st_at       | H1 2017-06-02..2020-09-15   | +1.57%  | 0.79 |   -1.6  |   0.08 | -7.2%    |
| blend   | tsmom_and_st_at       | H2 2020-09-16..2023-12-29   | +0.30%  | 0.13 |   -2.27 |   0.12 | -8.1%    |
| blend   | tsmom_and_st_at       | full 2017-06-02..2023-12-29 | +0.87%  | 0.57 |   -1.83 |   0.1  | -8.1%    |
| blend   | real_tsmom_blend(ref) | H1 2017-06-02..2020-09-15   | +0.75%  | 0.36 |   -2.03 |   0.09 | -7.8%    |
| blend   | real_tsmom_blend(ref) | H2 2020-09-16..2023-12-29   | +0.76%  | 0.29 |   -2.1  |   0.14 | -8.5%    |
| blend   | real_tsmom_blend(ref) | full 2017-06-02..2023-12-29 | +0.66%  | 0.39 |   -2.01 |   0.11 | -8.5%    |

## Verdict (holdout 2024+ untouched)

**The agreement filter does not improve the C2 blend once the real sleeves are used.** The earlier proxy-based result
(blend alpha +0.5% -> +2.1% on Validate) does not reproduce.

- Replica check passes (corr 0.956 with the real TSMOMTrendBot sleeve), so the vectorised TSMOM is a fair stand-in.
- Sleeve level: the filtered sleeves are not better than base on Validate (base +2.7% t 0.67; and_st +0.8% t 0.26; and_st_at +1.8% t 0.57). Fit is also ~0 for all (t 0.25-0.41).
- Blend level (real TARegimeMultiAsset + GoldenButterfly, fixed 0.39/0.37/0.24, 2017-06..2023-12): full-window alpha +0.8% (base) vs
  +0.8% (and_st) vs +0.9% (and_st_at), t about 0.5 in every case. The filter helps in H1 and hurts in H2, a sign of noise, not edge.
- Nothing is near t 2 and nothing is shipped. The earlier positive result came from the equal-weight proxy sleeves.
