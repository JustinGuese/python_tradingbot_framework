# XAUSyntheticMetalTreeBot — backtest transcript

Recorded results from a local backtest run with best parameters, moved out
of `tradingbot/xauaisyntheticmetalbot.py`'s `if __name__ == "__main__":` block.

```
12:26:01 - utils.botclass - INFO - Backtesting with best parameters...
2026-03-26 12:26:01 - utils.botclass - INFO - ============================================================
2026-03-26 12:26:01 - utils.botclass - INFO -   dch_threshold: 350
2026-03-26 12:26:01 - utils.botclass - INFO -   dcl_threshold: 310
2026-03-26 12:26:01 - utils.botclass - INFO -   atr_threshold: 0.12
2026-03-26 12:26:01 - utils.botclass - INFO -   ichimoku_base_threshold: 310
2026-03-26 12:26:01 - utils.botclass - INFO -   kch_threshold: 315
[*********************100%***********************]  1 of 1 completed
2026-03-26 12:26:35 - utils.data_service - INFO - Adding only missing DataFrame rows to DB
2026-03-26 12:26:36 - utils.data_service - INFO - Rows to insert: 0
2026-03-26 12:26:50 - utils.backtest - INFO - QuantStats report → gs://tradingbotrunresults/XAUSyntheticMetalTreeBot/sharpewinner/report.html
2026-03-26 12:26:58 - utils.backtest - INFO - QuantStats report → gs://tradingbotrunresults/XAUSyntheticMetalTreeBot/yearlyreturnwinner/report.html
2026-03-26 12:26:58 - utils.botclass - INFO -
--- Backtest Results: XAUSyntheticMetalTreeBot ---
2026-03-26 12:26:58 - utils.botclass - INFO - Yearly Return: 7.21%
2026-03-26 12:26:58 - utils.botclass - INFO - Buy & Hold Return: -9.73%
2026-03-26 12:26:58 - utils.botclass - INFO - Outperformance vs B&H: +16.94%
2026-03-26 12:26:58 - utils.botclass - INFO - Sharpe Ratio: 7.58
2026-03-26 12:26:58 - utils.botclass - INFO - Number of Trades: 2
2026-03-26 12:26:58 - utils.botclass - INFO - Max Drawdown: 5.24%
```
