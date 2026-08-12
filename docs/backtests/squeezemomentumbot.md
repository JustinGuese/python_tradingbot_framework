# SqueezeMomentumBot — backtest transcript

Best parameters (from `local_development`, 2026-04-14) and the resulting
backtest, moved out of `tradingbot/squeezemomentumbot.py`'s
`if __name__ == "__main__":` block.

Tight grid: `rsi_low [35,38,40,42,45]`, `rsi_high [58,60,62,65]`,
`rsi_exit [75,78,80,83,85]`, `macd_hist [-1.0,-0.5,-0.2,0.0]`,
`sell_buffer [0.02,0.03,0.04,0.05]` — 400 combos, full search.

```
--- Backtest Results: SqueezeMomentumBot ---
Yearly Return:             53.91%
Buy & Hold Return (GLD):   48.56%
Outperformance vs B&H:     +5.35%
Sharpe Ratio:               2.19
Number of Trades:          19
Max Drawdown:               8.09%
```
