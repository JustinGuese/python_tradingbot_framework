"""
Rough draft: Swing trading strategy using trendlines on 4-hour chart
- Data source: yfinance
- Chart timeframe: 4H (resampled)
- Trendlines based on local minima/maxima
- Fit trendlines using RANSAC (robust to outliers)
- Basic backtester that simulates long/short entries when price "touches" support/resistance

Notes / improvements to consider later:
- Better extremum detection (multi-scale, prominence tuning)
- Use non-linear / piecewise trendlines for complex structures
- Use order book / volume for confirmation (if available)
- Add transaction costs, slippage, position sizing rules
- Use walk-forward parameter tuning (avoid lookahead)

Required packages:
  pip install pandas numpy yfinance scipy scikit-learn matplotlib

Replace TICKER with your target (e.g. a titanium ETF future ticker) and run.
"""

# Best median trade PnL: -3.16 with params: {'order': 6, 'prominence': 0.5, 'rebalance_bars': 24, 'touch_tolerance': 0.005, 'min_points_for_trend': 2}
# Best final equity: 147057.00 with params: {'order': 8, 'prominence': 0.5, 'rebalance_bars': 12, 'touch_tolerance': 0.01, 'min_points_for_trend': 4}

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.signal import find_peaks
from sklearn.linear_model import LinearRegression, RANSACRegressor
from tqdm import tqdm


def download_ohlc(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Download daily data and resample to 4H bars using yfinance.
    yfinance returns timezone-aware datetimes; we keep them as-is.
    """
    df = yf.download(ticker, start=start, end=end, progress=False)
    df = df.swaplevel(axis=1)[ticker]
    if df.empty:
        raise ValueError(f"No data for {ticker} between {start} and {end}")

    # Ensure datetime index and timezone naive for simplicity
    df = df.tz_localize(None) if hasattr(df.index, "tz") else df

    # If the data is intraday already, prefer it; else resample daily->4H using business hours assumption
    try:
        # if there are intraday timestamps, just resample to 4H
        df_4h = (
            df.resample("4H")
            .agg(
                {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }
            )
            .dropna()
        )
    except Exception:
        # fallback: upsample daily to 4H via forward-fill (not ideal)
        df_4h = df.resample("4H").ffill()

    df_4h.columns = [c.replace(" ", "_") for c in df_4h.columns]
    return df_4h


# ---------- Extremum detection ----------


def detect_local_extrema(
    series: pd.Series, order: int = 5, prominence: float = 0.0
) -> Tuple[np.ndarray, np.ndarray]:
    """Return indices of local minima and maxima.

    order: look-around window size (in bars). Increase for smoother detection.
    prominence: minimum vertical distance to neighbors.
    """
    # find local maxima
    peaks, _ = find_peaks(series.values, distance=order, prominence=prominence)
    # invert series to find minima
    troughs, _ = find_peaks(-series.values, distance=order, prominence=prominence)
    return troughs, peaks


# ---------- Trendline fitting ----------


def fit_trendline_ransac(
    x: np.ndarray, y: np.ndarray
) -> Tuple[float, float, RANSACRegressor]:
    """Fit a robust linear trendline y = m*x + b using RANSAC.
    x should be numeric (e.g., timestamps converted to floats or simple integer indices).
    Returns slope m and intercept b and the ransac model.
    """
    if len(x) < 2:
        raise ValueError("Need at least 2 points to fit a line")
    X = x.reshape(-1, 1)
    model = RANSACRegressor(
        LinearRegression(),
        min_samples=max(2, int(len(x) * 0.3)),
        residual_threshold=np.std(y) * 1.5,
    )
    model.fit(X, y)
    m = model.estimator_.coef_[0]
    b = model.estimator_.intercept_
    return m, b, model


def trendline_from_extrema(
    dates: pd.DatetimeIndex, prices: pd.Series, extrema_idx: np.ndarray
) -> Dict:
    """Given extrema indices (e.g., troughs for support or peaks for resistance),
    fit a trendline and return useful info.
    """
    if len(extrema_idx) < 2:
        return {
            "slope": 0.0,
            "intercept": prices.iloc[extrema_idx[0]] if len(extrema_idx) else np.nan,
            "model": None,
            "used_points": extrema_idx,
        }

    # convert datetimes to ordinal numbers for regression
    x = np.array([dates[i].toordinal() + dates[i].hour / 24.0 for i in extrema_idx])
    y = prices.iloc[extrema_idx].values
    m, b, model = fit_trendline_ransac(x, y)
    return {"slope": m, "intercept": b, "model": model, "used_points": extrema_idx}


def line_value_at(model_info: Dict, dt: pd.Timestamp) -> float:
    if model_info["model"] is None:
        return np.nan
    x = np.array([[dt.toordinal() + dt.hour / 24.0]])
    pred = model_info["model"].predict(x)
    return float(pred.item())  # fix for deprecated numpy scalar conversion


# ---------- Signal logic ----------


def is_touching_line(price: float, line_price: float, tolerance: float = 0.01) -> bool:
    """Return True if price 'touches' line_price within relative tolerance.
    tolerance is relative fraction (e.g., 0.01 = 1%).
    """
    if np.isnan(line_price):
        return False
    return abs(price - line_price) <= tolerance * line_price


# ---------- Simple backtester ----------


def backtest_trendline_strategy(
    df: pd.DataFrame,
    order: int = 5,
    prominence: float = 0.0,
    rebalance_bars: int = 24,
    touch_tolerance: float = 0.01,
    min_points_for_trend: int = 3,
    verbose: bool = False,
) -> pd.DataFrame:
    """A rolling backtest:
    - every `rebalance_bars` bars we detect extrema on the recent window and fit trendlines
    - generate long entries when price touches support in an uptrend (slope>0)
    - exit long when price closes below support
    - symmetric rules for short / downtrend
    Returns a trades log DataFrame and basic equity curve as a series in df (cheap simulation)
    """
    prices = df["Close"]
    dates = df.index
    n = len(df)

    position = 0  # +1 long, -1 short, 0 flat
    entry_price = 0.0
    cash = 100000.0
    position_size = 0.0  # will be set to all-in on entry
    equity = []
    trades = []

    # Track only the currently active trend and its info
    active_trend = None  # 'uptrend' or 'downtrend'
    active_trend_info = {
        "model": None,
        "slope": 0.0,
        "intercept": np.nan,
        "used_points": [],
    }
    last_trend = None
    trend_switches = []  # for debugging/plotting

    # window size for trend detection (bars)
    window = rebalance_bars * 6  # e.g., look back some multiples of rebalance interval

    for i in range(n):
        # Use a rolling window for trend detection at every bar
        start_idx = max(0, i - window)
        sub_prices = prices.iloc[start_idx : i + 1]

        troughs, peaks = detect_local_extrema(
            sub_prices, order=order, prominence=prominence
        )
        troughs_full = start_idx + troughs
        peaks_full = start_idx + peaks

        support_info = (
            trendline_from_extrema(dates, prices, troughs_full)
            if len(troughs_full) >= min_points_for_trend
            else {
                "model": None,
                "slope": 0.0,
                "intercept": np.nan,
                "used_points": troughs_full,
            }
        )
        resistance_info = (
            trendline_from_extrema(dates, prices, peaks_full)
            if len(peaks_full) >= min_points_for_trend
            else {
                "model": None,
                "slope": 0.0,
                "intercept": np.nan,
                "used_points": peaks_full,
            }
        )

        slope_up = support_info.get("slope", 0.0)
        slope_down = resistance_info.get("slope", 0.0)

        uptrend = (slope_up > 0) and (
            len(support_info.get("used_points", [])) >= min_points_for_trend
        )
        downtrend = (slope_down < 0) and (
            len(resistance_info.get("used_points", [])) >= min_points_for_trend
        )

        # Only one trend active at a time
        if uptrend and not downtrend:
            active_trend = "uptrend"
            active_trend_info = support_info
        elif downtrend and not uptrend:
            active_trend = "downtrend"
            active_trend_info = resistance_info
        else:
            active_trend = last_trend if last_trend else None
            active_trend_info = (
                active_trend_info
                if active_trend
                else {
                    "model": None,
                    "slope": 0.0,
                    "intercept": np.nan,
                    "used_points": [],
                }
            )

        # Detect trend reversal at every bar
        if active_trend != last_trend:
            trend_switches.append((dates[i], active_trend))
        last_trend = active_trend

        cur_price = prices.iloc[i]
        cur_date = dates[i]

        # compute current trendline value at this datetime
        trendline_price = (
            line_value_at(active_trend_info, cur_date)
            if active_trend_info["model"] is not None
            else np.nan
        )

        # ENTRY logic
        if position == 0:
            if active_trend == "uptrend" and is_touching_line(
                cur_price, trendline_price, tolerance=touch_tolerance
            ):
                # enter long, all-in
                position = 1
                entry_price = cur_price
                position_size = cash  # invest all available cash
                cash = 0.0
                trades.append(
                    {
                        "date": cur_date,
                        "type": "buy",
                        "price": cur_price,
                        "size": position_size,
                    }
                )
                if verbose:
                    print(f"{cur_date} BUY at {cur_price:.2f}")
            elif active_trend == "downtrend" and is_touching_line(
                cur_price, trendline_price, tolerance=touch_tolerance
            ):
                # enter short, all-in
                position = -1
                entry_price = cur_price
                position_size = cash  # invest all available cash
                cash = 0.0
                trades.append(
                    {
                        "date": cur_date,
                        "type": "sell_short",
                        "price": cur_price,
                        "size": position_size,
                    }
                )
                if verbose:
                    print(f"{cur_date} SHORT at {cur_price:.2f}")

        # EXIT logic
        elif position == 1:
            # exit long if price closes below support line (trendline)
            if not np.isnan(trendline_price) and (cur_price < trendline_price):
                pnl = (cur_price - entry_price) / entry_price * position_size
                cash = position_size + pnl  # sell all
                trades.append(
                    {
                        "date": cur_date,
                        "type": "exit_long",
                        "price": cur_price,
                        "pnl": pnl,
                    }
                )
                if verbose:
                    print(f"{cur_date} EXIT LONG at {cur_price:.2f} pnl={pnl:.2f}")
                position = 0
                entry_price = 0.0
                position_size = 0.0
        elif position == -1:
            # exit short if price closes above resistance (trendline)
            if not np.isnan(trendline_price) and (cur_price > trendline_price):
                pnl = (entry_price - cur_price) / entry_price * position_size
                cash = position_size + pnl  # cover all
                trades.append(
                    {
                        "date": cur_date,
                        "type": "exit_short",
                        "price": cur_price,
                        "pnl": pnl,
                    }
                )
                if verbose:
                    print(f"{cur_date} EXIT SHORT at {cur_price:.2f} pnl={pnl:.2f}")
                position = 0
                entry_price = 0.0
                position_size = 0.0

        # mark-to-market equity
        if position == 0:
            eq = cash
        elif position == 1:
            # long: all-in, current market value
            eq = position_size * (cur_price / entry_price)
        else:
            # short: all-in, current market value
            eq = position_size * (entry_price / cur_price)

        equity.append(eq)

    equity_series = pd.Series(equity, index=dates)
    trades_df = pd.DataFrame(trades)
    trades_df.set_index("date", inplace=False)
    result = pd.DataFrame({"equity": equity_series, "close": prices})
    return result, trades_df, active_trend_info, trend_switches


# ---------- Plotting helper ----------


def plot_with_trendlines(
    df: pd.DataFrame, active_trend_info: Dict, trend_switches: list, title: str = ""
):
    plt.figure(figsize=(14, 6))
    plt.plot(df.index, df["Close"], label="Close")

    # Only plot the active trendline (support for uptrend, resistance for downtrend)
    if active_trend_info["model"] is not None:
        xs = np.array([d.toordinal() + d.hour / 24.0 for d in df.index])
        ys = active_trend_info["model"].predict(xs.reshape(-1, 1))
        label = (
            "Support (uptrend)"
            if active_trend_info["slope"] > 0
            else "Resistance (downtrend)"
        )
        plt.plot(df.index, ys, label=label, linewidth=2)
        plt.scatter(
            df.index[list(active_trend_info.get("used_points", []))],
            df["Close"].iloc[list(active_trend_info.get("used_points", []))],
            marker="v" if active_trend_info["slope"] > 0 else "^",
            color="orange" if active_trend_info["slope"] > 0 else "red",
            s=60,
            label="Extrema Points",
        )

    # Mark trend reversals on the plot
    for dt, trend in trend_switches:
        plt.axvline(dt, color="magenta", linestyle="--", alpha=0.5)
        plt.text(
            dt,
            df["Close"].max(),
            f"{trend}",
            color="magenta",
            rotation=90,
            va="top",
            ha="right",
        )

    plt.title(title)
    plt.legend()
    plt.show()


# Helper for parallel grid search


def run_backtest_combo(combo, param_names, df):
    # Re-import everything needed for multiprocessing
    params = dict(zip(param_names, combo))
    result, trades, _, _ = backtest_trendline_strategy(
        df,
        order=params["order"],
        prominence=params["prominence"],
        rebalance_bars=params["rebalance_bars"],
        touch_tolerance=params["touch_tolerance"],
        min_points_for_trend=params["min_points_for_trend"],
        verbose=False,
    )
    median_pnl = trades["pnl"].median() if "pnl" in trades.columns else float("-inf")
    final_equity = result["equity"].iloc[-1]
    return (combo, median_pnl, final_equity)


# ---------- Example usage ----------
if __name__ == "__main__":
    # Replace with your titanium ETF/future ticker. Example placeholder below.
    TICKER = "600456.SS"  # <-- replace
    START = "2023-01-01"
    END = "2025-08-01"

    df = download_ohlc(TICKER, START, END)

    if not df.empty:
        result, trades, active_trend_info, trend_switches = backtest_trendline_strategy(
            df,
            order=6,
            prominence=0.5,
            rebalance_bars=24,
            touch_tolerance=0.008,
            verbose=True,
        )

        # Buy-and-hold benchmark (all-in)
        initial_cash = 100000.0
        buyhold_entry = df["Close"].iloc[0]
        buyhold_shares = initial_cash / buyhold_entry
        buyhold_equity = buyhold_shares * df["Close"]
        buyhold_final = buyhold_equity.iloc[-1]

        # Median PnL of trades
        median_pnl = trades["pnl"].median() if "pnl" in trades.columns else None

        print("\nTrades:")
        print(trades.tail(20))
        print(f"\nFinal equity (strategy): {result['equity'].iloc[-1]:.2f}")
        print(f"Final equity (buy & hold): {buyhold_final:.2f}")
        print(
            f"Median trade PnL: {median_pnl:.2f}"
            if median_pnl is not None
            else "Median trade PnL: N/A"
        )
        print("\nTrend switches:")
        print(trend_switches)

        # Plot both equity curves
        plt.figure(figsize=(14, 6))
        plt.plot(result.index, result["equity"], label="Strategy Equity")
        plt.plot(df.index, buyhold_equity, label="Buy & Hold Equity", linestyle="--")
        plt.title(f"{TICKER} 4H Trendlines: Strategy vs Buy & Hold")
        plt.legend()
        plt.show()

        # Plot price and trendlines as before
        plot_with_trendlines(
            df, active_trend_info, trend_switches, title=f"{TICKER} 4H Trendlines"
        )

        # Parameter optimization grid
        from itertools import product

        param_grid = {
            "order": [4, 6, 8],
            "prominence": [0.2, 0.5, 1.0],
            "rebalance_bars": [12, 24, 48],
            "touch_tolerance": [0.005, 0.008, 0.01],
            "min_points_for_trend": [2, 3, 4],
        }
        param_names = list(param_grid.keys())
        param_combos = list(product(*param_grid.values()))
        best_median = None
        best_median_params = None
        best_equity = None
        best_equity_params = None
        best_median_val = float("-inf")
        best_equity_val = float("-inf")
        print("\nOptimizing parameters (parallel)...")
        results = []
        with ProcessPoolExecutor() as executor:
            futures = [
                executor.submit(run_backtest_combo, combo, param_names, df)
                for combo in param_combos
            ]
            for f in tqdm(
                as_completed(futures), total=len(futures), desc="Grid Search"
            ):
                results.append(f.result())
        # Find best results
        for combo, median_pnl, final_equity in results:
            params = dict(zip(param_names, combo))
            if median_pnl > best_median_val:
                best_median_val = median_pnl
                best_median = median_pnl
                best_median_params = params.copy()
            if final_equity > best_equity_val:
                best_equity_val = final_equity
                best_equity = final_equity
                best_equity_params = params.copy()
        print(
            f"\nBest median trade PnL: {best_median:.2f} with params: {best_median_params}"
        )
        print(f"Best final equity: {best_equity:.2f} with params: {best_equity_params}")
        # Optionally rerun and plot best equity settings
        result, trades, active_trend_info, trend_switches = backtest_trendline_strategy(
            df,
            order=best_equity_params["order"],
            prominence=best_equity_params["prominence"],
            rebalance_bars=best_equity_params["rebalance_bars"],
            touch_tolerance=best_equity_params["touch_tolerance"],
            min_points_for_trend=best_equity_params["min_points_for_trend"],
            verbose=True,
        )

        # Buy-and-hold benchmark (all-in)
        initial_cash = 100000.0
        buyhold_entry = df["Close"].iloc[0]
        buyhold_shares = initial_cash / buyhold_entry
        buyhold_equity = buyhold_shares * df["Close"]
        buyhold_final = buyhold_equity.iloc[-1]

        # Median PnL of trades
        median_pnl = trades["pnl"].median() if "pnl" in trades.columns else None

        print("\nTrades:")
        print(trades.tail(20))
        print(f"\nFinal equity (strategy): {result['equity'].iloc[-1]:.2f}")
        print(f"Final equity (buy & hold): {buyhold_final:.2f}")
        print(
            f"Median trade PnL: {median_pnl:.2f}"
            if median_pnl is not None
            else "Median trade PnL: N/A"
        )
        print("\nTrend switches:")
        print(trend_switches)

        # Plot both equity curves
        plt.figure(figsize=(14, 6))
        plt.plot(result.index, result["equity"], label="Strategy Equity")
        plt.plot(df.index, buyhold_equity, label="Buy & Hold Equity", linestyle="--")
        plt.title(f"{TICKER} 4H Trendlines: Strategy vs Buy & Hold (Optimized)")
        plt.legend()
        plt.show()

        # Plot price and trendlines as before
        plot_with_trendlines(
            df,
            active_trend_info,
            trend_switches,
            title=f"{TICKER} 4H Trendlines (Optimized)",
        )
