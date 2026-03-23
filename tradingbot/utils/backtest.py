"""Backtesting functionality for trading bots."""

import logging
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from .botclass import Bot

logger = logging.getLogger(__name__)



def _get_periods_per_year(interval: str) -> float:
    """
    Calculate approximate number of periods per trading year for a given interval.

    Args:
        interval: Data interval string (e.g., "1d", "1h", "1m")

    Returns:
        Approximate number of periods per trading year
    """
    # Trading year assumptions:
    # - 252 trading days per year
    # - ~6.5 trading hours per day (9:30 AM - 4:00 PM ET)
    # - ~390 trading minutes per day (6.5 hours * 60 minutes)

    if interval == "1d":
        return 252.0
    elif interval == "1wk":
        return 52.0
    elif interval == "1mo":
        return 12.0
    elif interval in ["1h", "60m"]:
        return 252.0 * 6.5  # ~1,638 periods per year
    elif interval == "4h":
        return 252.0 * 1.625  # ~409.5 periods per year
    elif interval == "1m":
        return 252.0 * 390  # ~98,280 periods per year
    elif interval == "5m":
        return 252.0 * 78  # ~19,656 periods per year
    elif interval == "15m":
        return 252.0 * 26  # ~6,552 periods per year
    elif interval == "30m":
        return 252.0 * 13  # ~3,276 periods per year
    else:
        # Default: assume daily frequency
        return 252.0


def _get_backtest_period(interval: str) -> str:
    """
    Get appropriate backtest period based on interval, respecting Yahoo Finance limits.

    Yahoo Finance limits:
    - 1m, 2m, 5m, 15m, 30m, 60m, 90m: max 60 days
    - 1h: max 730 days (2 years)
    - 1d, 5d, 1wk, 1mo, 3mo: max available (years)

    Args:
        interval: Data interval string (e.g., "1d", "1h", "1m")

    Returns:
        Period string suitable for backtesting (e.g., "7d", "60d", "1y")
    """
    # For minute-level data, Yahoo Finance limits to 60 days, but we use 7d to be safe
    if interval in ["1m", "2m", "5m", "15m", "30m", "60m", "90m"]:
        return "7d"  # Safe limit for minute data
    elif interval in ["1h", "60m"]:
        return "60d"  # 60 days for hourly data
    elif interval in ["1d", "5d", "1wk", "1mo", "3mo"]:
        return "1y"  # 1 year for daily/weekly/monthly data
    else:
        # Default: use 1 year for unknown intervals
        return "1y"


def _upload_quantstats_report(
    bot_name: str,
    portfolio_values: list,
    portfolio_timestamps: list,
    data: pd.DataFrame,
    metric_folder: str,
) -> None:
    import os
    import tempfile

    access_key = os.environ.get("GCS_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("GCS_SECRET_ACCESS_KEY", "")
    if not access_key or not secret_key:
        return

    import boto3
    from botocore.config import Config
    import quantstats as qs
    bucket = os.environ.get("GCS_BUCKET_NAME", "tradingbotrunresults")

    tmp_path = None
    try:
        if all(t is not None for t in portfolio_timestamps):
            idx = pd.DatetimeIndex(pd.to_datetime(portfolio_timestamps, utc=True).tz_convert(None))
        else:
            idx = None
        returns = pd.Series(portfolio_values, index=idx).pct_change().dropna()
        close = data["close"].dropna()
        benchmark = (
            pd.Series(close.values[:len(portfolio_values)], index=idx).pct_change().dropna()
            if idx is not None else close.pct_change().dropna()
        )
        benchmark.name = "Benchmark"

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            tmp_path = f.name

        qs.reports.html(returns, benchmark=benchmark, output=tmp_path,
                        title=f"{bot_name} – {metric_folder}",
                        download_filename="report.html")

        client = boto3.client(
            "s3",
            endpoint_url="https://storage.googleapis.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east1",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path", "payload_signing_enabled": True},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        key = f"{bot_name}/{metric_folder}/report.html"
        with open(tmp_path, "rb") as fh:
            html_bytes = fh.read()
        client.put_object(Bucket=bucket, Key=key, Body=html_bytes, ContentType="text/html")
        logger.info(f"QuantStats report → gs://{bucket}/{key}")

    except Exception:
        raise
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _compute_backtest_metrics(
    portfolio_values: list,
    interval: str,
    risk_free_rate: float,
) -> Dict[str, Any]:
    """Shared metrics computation for single- and multi-ticker backtests."""
    if len(portfolio_values) < 2:
        raise ValueError(
            "Insufficient post-warmup portfolio value data for metrics calculation. "
            "The dataset may be too short (most rows are in the TA warmup period). "
            "Use a longer period or a shorter interval."
        )

    final_value = portfolio_values[-1]
    initial_value = portfolio_values[0]
    yearly_return = (final_value - initial_value) / initial_value if initial_value > 0 else 0.0

    portfolio_series = pd.Series(portfolio_values)
    period_returns = portfolio_series.pct_change().dropna()
    periods_per_year = _get_periods_per_year(interval)

    if len(period_returns) == 0:
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        win_rate = 0.0
        volatility = 0.0
    else:
        std_return = period_returns.std()
        if std_return == 0 or not np.isfinite(std_return):
            sharpe_ratio = 0.0
        else:
            annualized_return = period_returns.mean() * periods_per_year
            annualized_vol = std_return * np.sqrt(periods_per_year)
            sharpe_ratio = (annualized_return - risk_free_rate) / annualized_vol if annualized_vol > 0 else 0.0

        # Sortino — penalises downside returns only
        downside = period_returns[period_returns < 0]
        downside_std = downside.std() if len(downside) > 0 else 0.0
        if downside_std > 0 and np.isfinite(downside_std):
            annualized_return = period_returns.mean() * periods_per_year
            annualized_downside = downside_std * np.sqrt(periods_per_year)
            sortino_ratio = (annualized_return - risk_free_rate) / annualized_downside
        else:
            sortino_ratio = 0.0
        if not np.isfinite(sortino_ratio):
            sortino_ratio = 0.0

        # Win rate — fraction of bars with positive return
        win_rate = float((period_returns > 0).mean()) if len(period_returns) > 0 else 0.0

        # Annualised volatility
        volatility = float(period_returns.std() * np.sqrt(periods_per_year)) if len(period_returns) > 0 else 0.0
        if not np.isfinite(volatility):
            volatility = 0.0

    portfolio_array = np.array(portfolio_values)
    running_max = np.maximum.accumulate(portfolio_array)
    drawdowns = (running_max - portfolio_array) / running_max
    maxdrawdown = float(np.max(drawdowns))
    if not np.isfinite(maxdrawdown):
        maxdrawdown = 0.0

    # Calmar — return per unit of max drawdown
    calmar_ratio = float(yearly_return / maxdrawdown) if maxdrawdown > 0 else 0.0
    if not np.isfinite(calmar_ratio):
        calmar_ratio = 0.0

    return {
        "yearly_return": float(yearly_return),
        "sharpe_ratio": float(sharpe_ratio),
        "maxdrawdown": float(maxdrawdown),
        "sortino_ratio": float(sortino_ratio),
        "calmar_ratio": float(calmar_ratio),
        "win_rate": float(win_rate),
        "volatility": float(volatility),
    }


def _save_backtest_to_db(
    bot: Bot,
    symbol_key: str,
    result: dict,
    portfolio_values: list,
    portfolio_timestamps: list,
    data_for_qs: pd.DataFrame,
) -> None:
    """Persist best backtest result to DB and upload QuantStats report."""
    updated_metrics: List[str] = []
    try:
        from sqlalchemy import and_
        from .db import BacktestResult, get_db_session
        _bot_name = bot.bot_name
        _interval = getattr(bot, "interval", None)
        with get_db_session() as session:
            for metric, new_value, compare_col in [
                ("best_sharpe", result["sharpe_ratio"], "sharpe_ratio"),
                ("best_yearly_return", result["yearly_return"], "yearly_return"),
            ]:
                existing = session.query(BacktestResult).filter(
                    and_(
                        BacktestResult.bot_name == _bot_name,
                        BacktestResult.symbol == symbol_key,
                        BacktestResult.interval == _interval,
                        BacktestResult.metric == metric,
                    )
                ).first()
                existing_val = getattr(existing, compare_col, None)
                new_params = dict(getattr(bot, "params", {}) or {})
                
                # Only write if it's better, or if it's the same score but different in params/return/sharpe
                is_different = (
                    existing is None or
                    existing.params != new_params or
                    existing.yearly_return != result["yearly_return"] or
                    existing.sharpe_ratio != result["sharpe_ratio"]
                )
                
                if existing is None or existing_val is None or new_value > existing_val or (new_value == existing_val and is_different):
                    if existing is not None:
                        session.delete(existing)
                        session.flush()
                    session.add(BacktestResult(
                        bot_name=_bot_name,
                        symbol=symbol_key,
                        interval=_interval,
                        period=getattr(bot, "period", None),
                        metric=metric,
                        params=new_params,
                        yearly_return=result["yearly_return"],
                        sharpe_ratio=result["sharpe_ratio"],
                        nrtrades=result["nrtrades"],
                        maxdrawdown=result["maxdrawdown"],
                        buy_hold_return=result["buy_hold_return"],
                        sortino_ratio=result.get("sortino_ratio"),
                        calmar_ratio=result.get("calmar_ratio"),
                        win_rate=result.get("win_rate"),
                        volatility=result.get("volatility"),
                    ))
                    updated_metrics.append(metric)
    except Exception as e:
        logger.warning(f"Failed to save backtest result to DB: {e}")

    # Always upload the report for every run — DB update is a "best result" tracker
    # but the report reflects the current run regardless of whether the score improved.
    for folder in ("sharpewinner", "yearlyreturnwinner"):
        _upload_quantstats_report(
            bot_name=bot.bot_name,
            portfolio_values=portfolio_values,
            portfolio_timestamps=portfolio_timestamps,
            data=data_for_qs,
            metric_folder=folder,
        )


def backtest_bot(
    bot: Bot,
    initial_capital: float = 10000.0,
    save_to_db: bool = True,
    data: Optional[Union[pd.DataFrame, Dict[str, pd.DataFrame]]] = None,
    slippage_pct: float = 0.0005,
    commission_pct: float = 0.0,
    risk_free_rate: float = 0.0,
    save_results_to_db: bool = True,
) -> dict:
    """
    Backtest a trading bot over historical data.

    Works for both single-ticker and multi-ticker bots that implement
    decisionFunction(). Multi-ticker bots use equal-weight position sizing:
    each ticker targets total_portfolio_value / N.

    Args:
        bot: Bot instance to backtest (must have decisionFunction implemented).
        initial_capital: Starting capital in USD (default: $10,000).
        save_to_db: Whether to save fetched data to database (default: True).
        data: Optional pre-fetched data.
              - Single-ticker: pd.DataFrame with timestamp/close/TA columns.
              - Multi-ticker: dict[str, pd.DataFrame] keyed by ticker symbol.
              If None, data is fetched automatically for all bot.tickers.
        slippage_pct: One-way slippage as fraction of price (default: 0.05%).
        commission_pct: Commission as fraction of trade value (default: 0.0).
        risk_free_rate: Annualized risk-free rate for Sharpe (default: 0.0).
        save_results_to_db: Whether to save best result to database.

    Returns:
        Dictionary with keys: yearly_return, buy_hold_return, sharpe_ratio,
        nrtrades, maxdrawdown.

    Raises:
        NotImplementedError: If bot doesn't implement decisionFunction.
        ValueError: If insufficient data is available for backtesting.
    """
    if type(bot).decisionFunction is Bot.decisionFunction:
        raise NotImplementedError(
            "Bot must implement decisionFunction() for backtesting. "
            "Bots that only override makeOneIteration() are not supported."
        )

    tickers = getattr(bot, "tickers", None) or ([bot.symbol] if bot.symbol else [])
    if not tickers:
        raise ValueError("Bot must have tickers or symbol defined for backtesting.")
    N = len(tickers)

    # ------------------------------------------------------------------ #
    #  Multi-ticker path (N > 1)                                          #
    # ------------------------------------------------------------------ #
    if N > 1:
        backtest_period = None
        data_dict: Dict[str, pd.DataFrame] = {}

        if isinstance(data, dict):
            data_dict = data
        elif data is not None:
            raise ValueError(
                "For multi-ticker bots, 'data' must be a dict[str, pd.DataFrame]. "
                "Pass None to fetch automatically."
            )
        else:
            backtest_period = _get_backtest_period(bot.interval)
            for ticker in tickers:
                try:
                    df = bot.getYFDataWithTA(
                        symbol=ticker,
                        interval=bot.interval,
                        period=backtest_period,
                        saveToDB=save_to_db,
                    )
                    data_dict[ticker] = df
                except Exception as e:
                    raise ValueError(f"Failed to fetch data for {ticker}: {e}")

        # Sort and index each DataFrame by timestamp
        indexed: Dict[str, pd.DataFrame] = {}
        for ticker, df in data_dict.items():
            if df.empty or len(df) < 2:
                raise ValueError(f"Insufficient data for ticker {ticker}")
            if "timestamp" in df.columns:
                df = df.sort_values("timestamp").reset_index(drop=True)
                data_dict[ticker] = df
                indexed[ticker] = df.set_index("timestamp")
            else:
                indexed[ticker] = df.sort_index()

        if backtest_period:
            bot.datasettings = (bot.interval, backtest_period)

        # Inner-join on common timestamps
        common_ts = indexed[tickers[0]].index
        for t in tickers[1:]:
            common_ts = common_ts.intersection(indexed[t].index)
        common_ts = sorted(common_ts)
        if len(common_ts) < 2:
            raise ValueError(
                "Insufficient common timestamps across tickers for multi-ticker backtest."
            )

        has_ta_columns = all("trend_adx" in indexed[t].columns for t in tickers)

        portfolio: Dict[str, float] = {"USD": initial_capital}
        portfolio_values: list = []
        portfolio_timestamps: list = []
        nrtrades = 0

        for ts in common_ts:
            rows = {t: indexed[t].loc[ts] for t in tickers}

            # Update bot's datas cache with current slice to prevent look-ahead bias
            # if the bot uses self.datas[ticker] inside decisionFunction.
            bot.datas = {t: indexed[t].loc[:ts] for t in tickers}

            # Validate prices for all tickers
            prices: Dict[str, float] = {}
            valid = True
            for ticker, row in rows.items():
                try:
                    price = float(row["close"])
                    if price <= 0 or not np.isfinite(price):
                        valid = False
                        break
                    prices[ticker] = price
                except (KeyError, ValueError, TypeError):
                    valid = False
                    break
            if not valid:
                continue

            # Skip warmup bars (any ticker with trend_adx == 0 = still warming up)
            if has_ta_columns and any(rows[t]["trend_adx"] == 0.0 for t in tickers):
                continue

            total_value = portfolio.get("USD", 0.0) + sum(
                portfolio.get(t, 0.0) * prices[t] for t in tickers
            )
            target = total_value / N

            for ticker in tickers:
                try:
                    bot._current_ticker = ticker
                    decision = bot.decisionFunction(rows[ticker])
                except Exception as e:
                    logger.warning(f"Error in decisionFunction for {ticker} at {ts}: {e}")
                    decision = 0

                price = prices[ticker]
                holding = portfolio.get(ticker, 0.0)
                holding_value = holding * price

                if decision == 1:
                    shortfall = target - holding_value
                    if shortfall > 0:
                        cash = portfolio.get("USD", 0.0)
                        buy_amount = min(shortfall, cash)
                        if buy_amount > 0:
                            commission_cost = buy_amount * commission_pct
                            available = buy_amount - commission_cost
                            execution_price = price * (1 + slippage_pct)
                            qty = available / execution_price
                            portfolio["USD"] = cash - buy_amount
                            portfolio[ticker] = holding + qty
                            nrtrades += 1
                elif decision == -1 and holding > 0:
                    execution_price = price * (1 - slippage_pct)
                    cash_proceeds = holding * execution_price
                    commission_cost = cash_proceeds * commission_pct
                    net_proceeds = cash_proceeds - commission_cost
                    portfolio["USD"] = portfolio.get("USD", 0.0) + net_proceeds
                    portfolio[ticker] = 0.0
                    nrtrades += 1

            current_total = portfolio.get("USD", 0.0) + sum(
                portfolio.get(t, 0.0) * prices[t] for t in tickers
            )
            portfolio_values.append(current_total)
            portfolio_timestamps.append(ts)

        metrics = _compute_backtest_metrics(portfolio_values, bot.interval, risk_free_rate)

        # Buy-and-hold: equal-weight mean of individual B&H returns across tickers
        bh_returns = []
        for ticker, df in data_dict.items():
            close = df["close"].dropna()
            if len(close) >= 2:
                first = float(close.iloc[0])
                last = float(close.iloc[-1])
                if first > 0 and np.isfinite(first) and np.isfinite(last):
                    bh_returns.append((last - first) / first)
        buy_hold_return = float(np.mean(bh_returns)) if bh_returns else 0.0

        result = {**metrics, "nrtrades": int(nrtrades), "buy_hold_return": buy_hold_return}

        if save_results_to_db:
            _save_backtest_to_db(
                bot=bot,
                symbol_key=",".join(tickers),
                result=result,
                portfolio_values=portfolio_values,
                portfolio_timestamps=portfolio_timestamps,
                data_for_qs=data_dict[tickers[0]],
            )

        return result

    # ------------------------------------------------------------------ #
    #  Single-ticker path (N == 1)                                        #
    # ------------------------------------------------------------------ #
    symbol = tickers[0]
    backtest_period = None

    if data is not None:
        if isinstance(data, dict):
            # Unwrap single-ticker dict (e.g., passed from hyperparameter tuner)
            data = data.get(symbol, next(iter(data.values())))
        if "close" not in data.columns or "timestamp" not in data.columns:
            raise ValueError(
                "Provided data must have 'close' and 'timestamp' columns. "
                "It should also include all TA indicators required by decisionFunction."
            )
    else:
        backtest_period = _get_backtest_period(bot.interval)
        try:
            data = bot.getYFDataWithTA(
                symbol=symbol,
                interval=bot.interval,
                period=backtest_period,
                saveToDB=save_to_db,
            )
        except Exception as e:
            raise ValueError(f"Failed to fetch historical data: {e}")

    if data.empty:
        raise ValueError("No historical data available for backtesting")
    if len(data) < 2:
        raise ValueError("Insufficient data points for backtesting (need at least 2)")

    if "timestamp" in data.columns:
        data = data.sort_values("timestamp").reset_index(drop=True)
    elif data.index.name in ["timestamp", "date", "datetime"]:
        data = data.sort_index()

    if backtest_period:
        bot.datasettings = (bot.interval, backtest_period)

    # trend_adx has ~26-bar warmup; warmup rows have trend_adx == 0.0 after fillna.
    has_ta_columns = "trend_adx" in data.columns

    portfolio = {"USD": initial_capital}
    portfolio_values = []
    portfolio_timestamps = []
    nrtrades = 0

    for idx, row in data.iterrows():
        # Update bot's data cache with current slice to prevent look-ahead bias
        # if the bot uses self.data inside decisionFunction.
        bot.data = data.iloc[:idx+1]

        try:
            current_price = float(row["close"])
        except (KeyError, ValueError, TypeError):
            continue
        if current_price <= 0 or not np.isfinite(current_price):
            continue
        if has_ta_columns and row["trend_adx"] == 0.0:
            continue

        try:
            decision = bot.decisionFunction(row)
        except Exception as e:
            logger.warning(f"Error in decisionFunction at row {idx}: {e}")
            decision = 0

        cash = portfolio.get("USD", 0.0)
        holdings = portfolio.get(symbol, 0.0)

        if decision == 1:
            if cash > 0:
                execution_price = current_price * (1 + slippage_pct)
                commission_cost = cash * commission_pct
                available = cash - commission_cost
                quantity = available / execution_price
                portfolio["USD"] = 0.0
                portfolio[symbol] = holdings + quantity
                nrtrades += 1
        elif decision == -1:
            if holdings > 0:
                execution_price = current_price * (1 - slippage_pct)
                cash_proceeds = holdings * execution_price
                commission_cost = cash_proceeds * commission_pct
                net_proceeds = cash_proceeds - commission_cost
                portfolio["USD"] = cash + net_proceeds
                portfolio[symbol] = 0.0
                nrtrades += 1

        current_cash = portfolio.get("USD", 0.0)
        current_holdings = portfolio.get(symbol, 0.0)
        portfolio_value = current_cash + (current_holdings * current_price)
        portfolio_values.append(portfolio_value)
        portfolio_timestamps.append(row["timestamp"] if "timestamp" in row.index else None)

    metrics = _compute_backtest_metrics(portfolio_values, bot.interval, risk_free_rate)

    close = data["close"].dropna()
    if len(close) < 2:
        buy_hold_return = 0.0
    else:
        first_close = float(close.iloc[0])
        last_close = float(close.iloc[-1])
        if first_close > 0 and np.isfinite(first_close) and np.isfinite(last_close):
            buy_hold_return = float((last_close - first_close) / first_close)
        else:
            buy_hold_return = 0.0

    result = {**metrics, "nrtrades": int(nrtrades), "buy_hold_return": buy_hold_return}

    if save_results_to_db:
        _save_backtest_to_db(
            bot=bot,
            symbol_key=symbol,
            result=result,
            portfolio_values=portfolio_values,
            portfolio_timestamps=portfolio_timestamps,
            data_for_qs=data,
        )

    return result
