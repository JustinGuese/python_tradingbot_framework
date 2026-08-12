# TARegimeAdaptiveBot — backtest transcript

Best parameters (max-sharpe) and the resulting backtest, moved out of
`tradingbot/taregimebot.py`'s `if __name__ == "__main__":` block.

Best parameters:
```
hurst_window: 50
hurst_trend_threshold: 0.46
adx_threshold: 16
rsi_oversold: 36
rsi_overbought: 66
bbp_low: 0.0
bbp_high: 0.8
zscore_window: 15
zscore_entry: 1.5
```

```
--- Backtest Results: TARegimeAdaptiveBot ---
Yearly Return: 12.58%
Buy & Hold Return: 16.32%
Outperformance vs B&H: -3.74%
Sharpe Ratio: 2.65
Number of Trades: 7
Max Drawdown: 2.62%
```
