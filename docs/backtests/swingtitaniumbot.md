# SwingTitaniumBot — backtest / hyperparameter-tuning transcript

Recorded results from a local `local_development` hyperparameter search,
moved out of `tradingbot/swingtitaniumbot.py`'s `if __name__ == "__main__":`
block.

```
- INFO - ============================================================
2026-03-23 18:02:21 - utils.hyperparameter_tuning - INFO - Best parameters: {'order': 6, 'prominence': 0.3, 'rebalance_bars': 10, 'touch_tolerance': 0.005, 'min_points_for_trend': 3}
2026-03-23 18:02:21 - utils.hyperparameter_tuning - INFO - Best sharpe_ratio: 0.1841
2026-03-23 18:02:21 - utils.hyperparameter_tuning - INFO - ============================================================
2026-03-23 18:02:21 - utils.botclass - INFO -
============================================================
2026-03-23 18:02:21 - utils.botclass - INFO - Best parameters (paste into __init__ defaults):
2026-03-23 18:02:21 - utils.botclass - INFO - ============================================================
2026-03-23 18:02:21 - utils.botclass - INFO -     order: 6,
2026-03-23 18:02:21 - utils.botclass - INFO -     prominence: 0.3,
2026-03-23 18:02:21 - utils.botclass - INFO -     rebalance_bars: 10,
2026-03-23 18:02:21 - utils.botclass - INFO -     touch_tolerance: 0.005,
2026-03-23 18:02:21 - utils.botclass - INFO -     min_points_for_trend: 3,
2026-03-23 18:02:21 - utils.botclass - INFO -
```
