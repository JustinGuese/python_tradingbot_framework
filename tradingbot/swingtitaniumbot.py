import numpy as np
from scipy.signal import find_peaks
from sklearn.linear_model import LinearRegression, RANSACRegressor
from utils.botclass import Bot


class SwingTitaniumBot(Bot):
    # Define the hyperparameter search space for this bot
    param_grid = {
        "order": [6, 8, 10],
        "prominence": [0.3, 0.5, 0.7],
        "rebalance_bars": [10, 12, 14],
        "touch_tolerance": [0.005, 0.01, 0.015],
        "min_points_for_trend": [3, 4, 5],
    }
    
    def __init__(
        self,
        order: int = 8,
        prominence: float = 0.5,
        rebalance_bars: int = 12,
        touch_tolerance: float = 0.01,
        min_points_for_trend: int = 4,
        **kwargs
    ):
        """
        Initialize the Swing Titanium Bot with configurable hyperparameters.
        
        Args:
            order: Distance parameter for peak/trough detection (default: 8)
            prominence: Prominence parameter for peak/trough detection (default: 0.5)
            rebalance_bars: Number of bars for rebalancing window (default: 12)
            touch_tolerance: Tolerance for price touching trendline (default: 0.01 = 1%)
            min_points_for_trend: Minimum points required to fit a trendline (default: 4)
            **kwargs: Additional parameters passed to base class
        """
        super().__init__(
            "SwingTitaniumBot",
            "600456.SS",
            order=order,
            prominence=prominence,
            rebalance_bars=rebalance_bars,
            touch_tolerance=touch_tolerance,
            min_points_for_trend=min_points_for_trend,
            **kwargs
        )
        # Store parameters as instance variables for easy access
        self.order = order
        self.prominence = prominence
        self.rebalance_bars = rebalance_bars
        self.touch_tolerance = touch_tolerance
        self.min_points_for_trend = min_points_for_trend
        self.last_trend = None
        self.active_trend_info = None
        self.trend_switches = []
        self.window = self.rebalance_bars * 6

    def decisionFunction(self, row):
        # This function will be called for each row in the dataframe
        # We'll use a rolling window to fit trendlines and decide
        df = self.data
        if df is None:
            # If data is not available, return hold
            return 0
        idx = df.index.get_loc(row.name)
        start_idx = max(0, idx - self.window)
        sub_prices = df["close"].iloc[start_idx : idx + 1]
        sub_dates = df["timestamp"].iloc[start_idx : idx + 1]
        troughs, peaks = (
            find_peaks(
                -sub_prices.values, distance=self.order, prominence=self.prominence
            )[0],
            find_peaks(
                sub_prices.values, distance=self.order, prominence=self.prominence
            )[0],
        )
        troughs_full = start_idx + troughs
        peaks_full = start_idx + peaks
        support_info = (
            self._trendline_from_extrema(sub_dates, sub_prices, troughs_full)
            if len(troughs_full) >= self.min_points_for_trend
            else None
        )
        resistance_info = (
            self._trendline_from_extrema(sub_dates, sub_prices, peaks_full)
            if len(peaks_full) >= self.min_points_for_trend
            else None
        )
        # Determine trend
        uptrend = support_info and support_info["slope"] > 0
        downtrend = resistance_info and resistance_info["slope"] < 0
        # Only one trend active at a time
        if uptrend and not downtrend:
            active_trend = "uptrend"
            active_trend_info = support_info
        elif downtrend and not uptrend:
            active_trend = "downtrend"
            active_trend_info = resistance_info
        else:
            active_trend = self.last_trend if self.last_trend else None
            active_trend_info = self.active_trend_info if active_trend else None
        self.last_trend = active_trend
        self.active_trend_info = active_trend_info
        # Compute current trendline value
        trendline_price = (
            self._line_value_at(active_trend_info, row["timestamp"])
            if active_trend_info
            else np.nan
        )
        price = row["close"]
        # Decision logic
        if active_trend == "uptrend" and self._is_touching_line(price, trendline_price):
            return 1  # Buy
        elif active_trend == "downtrend" and self._is_touching_line(
            price, trendline_price
        ):
            return -1  # Sell/Short
        else:
            return 0  # Hold

    def _trendline_from_extrema(self, dates, prices, extrema_idx):
        if len(extrema_idx) < 2:
            return None
        x = np.array(
            [dates.iloc[i].toordinal() + dates.iloc[i].hour / 24.0 for i in extrema_idx]
        )
        y = prices.iloc[extrema_idx].values
        model = RANSACRegressor(
            LinearRegression(),
            min_samples=max(2, int(len(x) * 0.3)),
            residual_threshold=np.std(y) * 1.5,
        )
        model.fit(x.reshape(-1, 1), y)
        m = model.estimator_.coef_[0]
        b = model.estimator_.intercept_
        return {"slope": m, "intercept": b, "model": model, "used_points": extrema_idx}

    def _line_value_at(self, model_info, dt):
        if model_info is None or model_info["model"] is None:
            return np.nan
        x = np.array([[dt.toordinal() + dt.hour / 24.0]])
        pred = model_info["model"].predict(x)
        return float(pred.item())

    def _is_touching_line(self, price, line_price):
        if np.isnan(line_price):
            return False
        return abs(price - line_price) <= self.touch_tolerance * line_price


bot = SwingTitaniumBot()

# bot.local_development()
bot.run()
