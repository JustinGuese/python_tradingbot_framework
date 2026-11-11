# option_PutWriteBot with T-bill yield on collateral (2026-11)

Pre-declared configurations only (no grid): a-priori defaults and the shipped H1 winner, SPY and QQQ.
Interest credited at ^IRX on the whole book. Alpha vs QQQ; the benchmark is not yield-adjusted.

| config                           | mode                 | full_alpha   |   full_t |   full_beta | cagr   | max_dd   | H1_alpha   |   H1_t |   H1_beta | H2_alpha   |   H2_t |   H2_beta |
|:---------------------------------|:---------------------|:-------------|---------:|------------:|:-------|:---------|:-----------|-------:|----------:|:-----------|-------:|----------:|
| SPY defaults                     | synthetic 2000-26    | -0.7%        |    -0.76 |        0.2  | +1.7%  | -28.1%   | +0.5%      |   0.34 |      0.17 | -3.1%      |  -2.55 |      0.26 |
| SPY defaults + T-bill            | synthetic 2000-26    | +1.3%        |     1.34 |        0.2  | +3.7%  | -26.5%   | +2.7%      |   1.86 |      0.17 | -1.2%      |  -1.02 |      0.26 |
| SPY shipped (H1 winner)          | synthetic 2000-26    | +0.9%        |     1.22 |        0.07 | +1.8%  | -20.5%   | +2.5%      |   3.87 |      0.03 | -2.0%      |  -1.58 |      0.15 |
| SPY shipped (H1 winner) + T-bill | synthetic 2000-26    | +2.9%        |     3.91 |        0.07 | +3.8%  | -20.4%   | +4.7%      |   7.23 |      0.03 | -0.2%      |  -0.13 |      0.15 |
| SPY defaults                     | real surface 2019-26 | -4.1%        |    -1.64 |        0.18 | +1.3%  | -22.0%   |            |        |           |            |        |           |
| SPY defaults + T-bill            | real surface 2019-26 | +0.0%        |     0.01 |        0.19 | +4.1%  | -21.8%   |            |        |           |            |        |           |
| SPY shipped (H1 winner)          | real surface 2019-26 | -0.9%        |    -0.43 |        0.08 | +1.1%  | -8.3%    |            |        |           |            |        |           |
| SPY shipped (H1 winner) + T-bill | real surface 2019-26 | +3.2%        |     1.56 |        0.08 | +3.9%  | -8.2%    |            |        |           |            |        |           |
| QQQ defaults                     | synthetic 2000-26    | +0.9%        |     1.07 |        0.33 | +4.8%  | -24.0%   | +4.0%      |   3.58 |      0.32 | -2.4%      |  -2.07 |      0.36 |
| QQQ defaults + T-bill            | synthetic 2000-26    | +2.7%        |     3.24 |        0.33 | +6.6%  | -22.6%   | +5.7%      |   5.1  |      0.32 | -0.6%      |  -0.49 |      0.36 |
| QQQ shipped (H1 winner)          | synthetic 2000-26    | +1.6%        |     1.79 |        0.1  | +2.8%  | -16.9%   | +3.4%      |   3.53 |      0.05 | -1.5%      |  -1.13 |      0.18 |
| QQQ shipped (H1 winner) + T-bill | synthetic 2000-26    | +3.3%        |     3.83 |        0.1  | +4.6%  | -15.3%   | +5.1%      |   5.27 |      0.05 | +0.4%      |   0.31 |      0.18 |
| QQQ defaults                     | real surface 2019-26 | +0.0%        |     0    |        0    | +0.0%  | +0.0%    |            |        |           |            |        |           |
| QQQ defaults + T-bill            | real surface 2019-26 | +4.2%        |    18.03 |        0    | +2.8%  | +0.0%    |            |        |           |            |        |           |
| QQQ shipped (H1 winner)          | real surface 2019-26 | +0.0%        |     0    |        0    | +0.0%  | +0.0%    |            |        |           |            |        |           |
| QQQ shipped (H1 winner) + T-bill | real surface 2019-26 | +4.2%        |    18.03 |        0    | +2.8%  | +0.0%    |            |        |           |            |        |           |
