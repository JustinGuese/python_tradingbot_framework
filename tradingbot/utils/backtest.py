"""Backtesting functionality for trading bots."""

import contextlib
import logging
from typing import Any, cast

import numpy as np
import pandas as pd

from .botclass import Bot
from .config import DEFAULT_COMMISSION_PCT, DEFAULT_SLIPPAGE_PCT, EXECUTION_CONFIG
from .portfolio_manager import should_trade

logger = logging.getLogger(__name__)

# What alpha is measured against. QQQ exposure is free (anyone can buy QQQ), so
# a bot is only worth running for the return it adds beyond its QQQ beta — see
# CLAUDE.md "The target: alpha vs QQQ, not return".
ALPHA_BENCHMARK = "QQQ"
ALPHA_KEYS = ("alpha", "alpha_t", "beta", "benchmark_corr")
# Intervals whose bars are one per session, so timestamps align on the date.
_SESSION_INTERVALS = {"1d", "5d", "1wk", "1mo", "3mo"}


def _iso(timestamp: Any) -> str | None:
    """Render a bar timestamp as ISO-8601 for the API/chart layer, or None if unusable."""
    if timestamp is None:
        return None
    try:
        return pd.Timestamp(timestamp).isoformat()
    except (TypeError, ValueError):
        return None


def _build_series(
    portfolio_values: list,
    portfolio_timestamps: list,
    benchmark_values: list,
    trade_log: list[dict],
) -> dict:
    """
    Package the intra-backtest series for charting.

    The three lists are appended in lockstep inside the bar loop, so `strict=True`
    is a real assertion: a mismatch means a bar updated equity without updating the
    benchmark (or vice versa), which would silently mis-align the two chart lines.
    """
    return {
        "equity_curve": [
            {"t": _iso(t), "v": float(v)} for t, v in zip(portfolio_timestamps, portfolio_values, strict=True)
        ],
        "buy_hold_curve": [
            {"t": _iso(t), "v": float(v)} for t, v in zip(portfolio_timestamps, benchmark_values, strict=True)
        ],
        "trades": trade_log,
    }


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


def _resolve_backtest_period(bot: Bot) -> str:
    """
    The history to fetch for `bot`: its BACKTEST_PERIOD override, else the
    interval default.

    The override exists because the interval default is chosen for strategies
    whose signal needs a handful of bars, and it is silently wrong for one whose
    lookback approaches it. _get_backtest_period("1d") is "1y", so a 12-month
    momentum bot would be handed exactly enough data to compute its first signal
    on the last bar — and the backtest would report a Sharpe ratio derived from
    almost no trades rather than failing, which is the dangerous kind of wrong.
    """
    override = getattr(bot, "BACKTEST_PERIOD", None)
    if override:
        return str(override)
    return _get_backtest_period(bot.interval)


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
    import quantstats as qs
    from botocore.config import Config

    bucket = os.environ.get("GCS_BUCKET_NAME", "tradingbotrunresults")

    tmp_path = None
    try:
        if all(t is not None for t in portfolio_timestamps):
            idx = pd.DatetimeIndex(pd.to_datetime(portfolio_timestamps, utc=True).tz_convert(None).normalize())
        else:
            idx = None
        returns = pd.Series(portfolio_values, index=idx)
        returns = returns[~returns.index.duplicated(keep="last")].pct_change().dropna()
        close = data["close"].dropna()
        if "timestamp" in data.columns:
            ts_idx = pd.to_datetime(data.loc[close.index, "timestamp"], utc=True).dt.tz_convert(None).dt.normalize()
            close = pd.Series(close.values, index=ts_idx)
        else:
            close = close.reset_index(drop=True)
            close.index = returns.index[: len(close)]
        close = close[~close.index.duplicated(keep="last")]
        benchmark = close.pct_change().dropna()
        benchmark.name = "Benchmark"

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            tmp_path = f.name

        try:
            qs.reports.html(
                returns,
                benchmark=benchmark,
                output=tmp_path,
                title=f"{bot_name} – {metric_folder}",
                download_filename="report.html",
            )
        except (ValueError, TypeError):
            # Benchmark alignment failed (e.g. no overlap or zero variance);
            # fall back to a report without benchmark.
            qs.reports.html(
                returns, output=tmp_path, title=f"{bot_name} – {metric_folder}", download_filename="report.html"
            )

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
            with contextlib.suppress(Exception):
                os.unlink(tmp_path)


def _compute_backtest_metrics(
    portfolio_values: list,
    interval: str,
    risk_free_rate: float,
) -> dict[str, Any]:
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


def _close_series(df: pd.DataFrame) -> pd.Series:
    """Close prices indexed by timestamp, from a long-format OHLCV frame."""
    if "timestamp" in df.columns:
        return pd.Series(df["close"].to_numpy(dtype=float), index=pd.Index(df["timestamp"])).dropna()
    return df["close"].astype(float).dropna()


def _align_index(timestamps: Any, interval: str) -> pd.DatetimeIndex:
    """Tz-naive UTC timestamps, truncated to the date for session bars.

    Daily bars from different sources disagree on the time of day (midnight vs
    the close, local vs UTC), so an exact-timestamp join of the strategy curve
    against the benchmark can silently match nothing.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Index(timestamps), utc=True)).tz_convert(None)
    return idx.normalize() if interval in _SESSION_INTERVALS else idx


def _compute_alpha_metrics(
    portfolio_values: list,
    portfolio_timestamps: list,
    benchmark_close: pd.Series | None,
    interval: str,
) -> dict[str, float | None]:
    """
    Alpha, its t-stat, beta and correlation of the strategy against the benchmark.

    OLS of per-bar strategy returns on benchmark returns over the bars both
    series share: beta = cov/var, alpha = mean residual annualised, and
    t = mean residual / its std * sqrt(n). Every key is None when there is no
    benchmark to measure against — deliberately not 0.0, which would read as
    "measured, no edge" and let an optimizer rank on nothing.
    """
    unavailable: dict[str, float | None] = dict.fromkeys(ALPHA_KEYS)
    if benchmark_close is None or len(benchmark_close) < 3:
        return unavailable
    if not portfolio_timestamps or any(t is None for t in portfolio_timestamps):
        return unavailable

    port = pd.Series(portfolio_values, index=_align_index(portfolio_timestamps, interval), dtype=float)
    bench = pd.Series(
        benchmark_close.to_numpy(dtype=float), index=_align_index(benchmark_close.index, interval), dtype=float
    )
    port = port[~port.index.duplicated(keep="last")]
    bench = bench[~bench.index.duplicated(keep="last")]
    joined = pd.concat({"p": port, "b": bench}, axis=1, join="inner").sort_index()
    rets = joined.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if len(rets) < 3:
        return unavailable

    var_b = float(rets["b"].var())
    if not np.isfinite(var_b) or var_b <= 0:
        return unavailable

    beta = float(rets["p"].cov(rets["b"]) / var_b)
    resid = rets["p"] - beta * rets["b"]
    resid_std = float(resid.std())
    alpha_t = float(resid.mean() / resid_std * np.sqrt(len(resid))) if resid_std > 0 else 0.0
    # A flat (all-cash) curve has no correlation to define; corr would divide by 0.
    corr = float(rets["p"].corr(rets["b"])) if rets["p"].std() > 0 else 0.0
    return {
        "alpha": float(resid.mean() * _get_periods_per_year(interval)),
        "alpha_t": alpha_t if np.isfinite(alpha_t) else 0.0,
        "beta": beta,
        "benchmark_corr": corr if np.isfinite(corr) else 0.0,
    }


def _resolve_benchmark_close(
    bot: Bot,
    benchmark_close: pd.Series | None,
    data: pd.DataFrame | dict[str, pd.DataFrame],
    data_was_given: bool,
    period: str | None,
    save_to_db: bool,
) -> pd.Series | None:
    """
    The benchmark close series to measure alpha against, or None.

    Reuses the backtest's own data when it already contains the benchmark. A
    caller that supplied `data` owns data fetching (the UI backend, the tuner),
    so the benchmark is never fetched behind its back — it passes
    `benchmark_close` instead, or gets None.
    """
    if benchmark_close is not None:
        return benchmark_close
    if isinstance(data, dict) and ALPHA_BENCHMARK in data:
        return _close_series(data[ALPHA_BENCHMARK])
    if isinstance(data, pd.DataFrame) and bot.symbol == ALPHA_BENCHMARK:
        return _close_series(data)
    if data_was_given or period is None:
        return None
    try:
        # The data service, not bot.getYFData: on a single-ticker bot that would
        # overwrite bot.data with the benchmark's bars.
        return _close_series(
            bot._data_service.get_yf_data(
                symbol=ALPHA_BENCHMARK, interval=bot.interval, period=period, save_to_db=save_to_db, use_cache=True
            )
        )
    except Exception as e:
        logger.warning(f"Could not fetch {ALPHA_BENCHMARK} for alpha metrics: {e}")
        return None


def _save_backtest_to_db(
    bot: Bot,
    symbol_key: str,
    result: dict,
    portfolio_values: list,
    portfolio_timestamps: list,
    data_for_qs: pd.DataFrame,
) -> None:
    """Persist best backtest result to DB and upload QuantStats report."""
    updated_metrics: list[str] = []
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
                existing = (
                    session.query(BacktestResult)
                    .filter(
                        and_(
                            BacktestResult.bot_name == _bot_name,
                            BacktestResult.symbol == symbol_key,
                            BacktestResult.interval == _interval,
                            BacktestResult.metric == metric,
                        )
                    )
                    .first()
                )
                existing_val = getattr(existing, compare_col, None)
                new_params = dict(getattr(bot, "params", {}) or {})

                # Only write if it's better, or if it's the same score but different in params/return/sharpe
                is_different = (
                    existing is None
                    or existing.params != new_params
                    or existing.yearly_return != result["yearly_return"]
                    or existing.sharpe_ratio != result["sharpe_ratio"]
                )

                if (
                    existing is None
                    or existing_val is None
                    or new_value > existing_val
                    or (new_value == existing_val and is_different)
                ):
                    if existing is not None:
                        session.delete(existing)
                        session.flush()
                    session.add(
                        BacktestResult(
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
                        )
                    )
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
    data: pd.DataFrame | dict[str, pd.DataFrame] | None = None,
    # Defaulted from config.py's constants rather than repeating the literals.
    # config.py's ExecutionConfig carries a comment saying its values "MUST mirror
    # backtest_bot()'s defaults ... or the live equity curve stops being comparable
    # to the backtested one" — an invariant that was enforced only by 0.0005 being
    # typed correctly in two files. Now there is one source of truth.
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    commission_pct: float = DEFAULT_COMMISSION_PCT,
    risk_free_rate: float = 0.0,
    save_results_to_db: bool = True,
    return_series: bool = False,
    benchmark_close: pd.Series | None = None,
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
        return_series: Also return the intra-backtest series for charting
                       (default: False, so existing callers are unaffected).
        benchmark_close: ALPHA_BENCHMARK (QQQ) close prices indexed by timestamp,
                       for the alpha metrics. Optional: when omitted it is taken
                       from `data` if present there, else fetched — but only
                       when `data` is None too.

    Returns:
        Dictionary with keys: yearly_return, buy_hold_return, sharpe_ratio,
        nrtrades, maxdrawdown, sortino_ratio, calmar_ratio, win_rate, volatility,
        plus alpha, alpha_t, beta, benchmark_corr against ALPHA_BENCHMARK
        (see _compute_alpha_metrics; each is None when no benchmark was
        available).

        Note that `yearly_return` is the TOTAL return over the backtest window,
        not an annualized figure, despite the name. Presentation layers should
        relabel it rather than this function renaming it, which would break every
        bot and the `backtest_results` table.

        With return_series=True, three more keys are added:
          equity_curve:    [{"t": iso8601, "v": portfolio value in USD}, ...]
          buy_hold_curve:  same shape, same timestamps — equal-weight buy-and-hold
                           over the tradeable tickers, scaled to initial_capital
          trades:          [{"t", "symbol", "side": "buy"|"sell", "qty", "price"}, ...]
                           where price is the execution price actually paid or
                           received (slippage included), not the bar's close.

        The curves cover only bars the backtest actually evaluated: bars with an
        invalid price, and TA warmup bars (trend_adx == 0.0), are skipped, so both
        curves are shorter than the input data and start after the warmup.

    Raises:
        NotImplementedError: If bot implements neither decisionFunction nor targetWeights.
        ValueError: If insufficient data is available for backtesting.
    """
    # targetWeights takes precedence over decisionFunction — the same order
    # Bot.backtest_type and Bot.makeOneIteration use, so a bot cannot be sized
    # one way live and another way here.
    uses_weights = type(bot).targetWeights is not Bot.targetWeights
    if not uses_weights and type(bot).decisionFunction is Bot.decisionFunction:
        raise NotImplementedError(
            "Bot must implement decisionFunction() or targetWeights() for backtesting. "
            "Bots that only override makeOneIteration() are not supported."
        )

    data_was_given = data is not None
    tickers = getattr(bot, "tickers", None) or ([bot.symbol] if bot.symbol else [])
    if not tickers:
        raise ValueError("Bot must have tickers or symbol defined for backtesting.")
    N = len(tickers)

    # ------------------------------------------------------------------ #
    #  Multi-ticker path (N > 1), and every targetWeights bot             #
    # ------------------------------------------------------------------ #
    # A one-ticker targetWeights bot takes this path too: the inner-join loop
    # below is trivially correct for a single series, and the single-asset path
    # can only express all-in/all-out, which is the one thing a sizing strategy
    # must not be forced into.
    if N > 1 or uses_weights:
        # Benchmark tickers are loaded and kept aligned like any other ticker
        # (a strategy may need one as a relative-strength baseline) but are
        # excluded from the divisor and never traded — matching
        # Bot._multi_ticker_target_weights. getattr, not the property, because
        # backtest_bot accepts instances built with a stubbed __init__.
        benchmarks = set(getattr(bot, "benchmark_tickers", ()) or ())
        tradeable = [t for t in tickers if t not in benchmarks] or tickers
        n_trade = len(tradeable)

        backtest_period = None
        data_dict: dict[str, pd.DataFrame] = {}

        if isinstance(data, dict):
            data_dict = data
        elif data is not None:
            raise ValueError(
                "For multi-ticker bots, 'data' must be a dict[str, pd.DataFrame]. Pass None to fetch automatically."
            )
        else:
            backtest_period = _resolve_backtest_period(bot)
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
                    raise ValueError(f"Failed to fetch data for {ticker}: {e}") from e

        # Sort and index each DataFrame by timestamp
        indexed: dict[str, pd.DataFrame] = {}
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
            raise ValueError("Insufficient common timestamps across tickers for multi-ticker backtest.")

        has_ta_columns = all("trend_adx" in indexed[t].columns for t in tickers)

        portfolio: dict[str, float] = {"USD": initial_capital}
        portfolio_values: list = []
        portfolio_timestamps: list = []
        # Benchmark and trade log are only populated for return_series, but the
        # appends are unconditional: branching inside the hot loop for a flag that
        # costs two list appends per bar would be a worse trade than the memory.
        benchmark_values: list = []
        trade_log: list[dict] = []
        first_prices: dict[str, float] = {}
        nrtrades = 0

        for ts in common_ts:
            rows = {t: indexed[t].loc[ts] for t in tickers}

            # Update bot's datas cache with current slice to prevent look-ahead bias
            # if the bot uses self.datas[ticker] inside decisionFunction.
            bot.datas = {t: indexed[t].loc[:ts] for t in tickers}

            # Validate prices for all tickers
            prices: dict[str, float] = {}
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

            total_value = portfolio.get("USD", 0.0) + sum(portfolio.get(t, 0.0) * prices[t] for t in tradeable)

            # Resolve this bar into a target USD value per ticker, a per-ticker
            # no-trade band reference, and the set of full exits (which bypass
            # the band, so a position told to leave cannot be stranded by it).
            # Both bot types converge here, so everything below — exits before
            # entries, slippage, commission, the band — is shared code and
            # cannot drift between them.
            if uses_weights:
                # Decide for the whole universe at once: a cross-sectional bot
                # cannot rank its legs one at a time.
                try:
                    raw_weights = bot.targetWeights(rows)
                except Exception as e:
                    logger.warning(f"Error in targetWeights at {ts}: {e}")
                    raw_weights = {}
                # held_weights=None: a backtest portfolio starts as pure cash and
                # only ever trades tickers in the universe, so an untracked
                # holding cannot arise here. That case is live-path only.
                if raw_weights is None:
                    # "No rebalance this bar", mirroring the live path's early
                    # return: every target is its current value, so neither
                    # phase below trades and the bar is still recorded.
                    targets = {t: portfolio.get(t, 0.0) * prices[t] for t in tradeable}
                    band_ref = dict(targets)
                    full_exit = set()
                else:
                    weights = bot._coerce_target_weights(raw_weights, allowed=set(tradeable))
                    targets = {t: weights.get(t, 0.0) * total_value for t in tradeable}
                    # Band against the LARGER of target and current value, so
                    # trimming a big position uses a band scaled to that position
                    # rather than to the small target it is heading for.
                    band_ref = {t: max(targets[t], portfolio.get(t, 0.0) * prices[t]) for t in tradeable}
                    full_exit = {t for t in tradeable if targets[t] <= 0.0}
            else:
                # Decide for every tradeable ticker before trading any of them, so
                # exits can fund entries — mirroring the live path, where
                # rebalance_portfolio executes all sells before any buy.
                decisions: dict[str, int] = {}
                for ticker in tradeable:
                    try:
                        bot._current_ticker = ticker
                        decisions[ticker] = bot.decisionFunction(rows[ticker])
                    except Exception as e:
                        logger.warning(f"Error in decisionFunction for {ticker} at {ts}: {e}")
                        decisions[ticker] = 0
                sleeve = total_value / n_trade
                # decision 0 caps at one sleeve but is never funded; decision -1
                # exits fully. Identical arithmetic to the pre-targetWeights code.
                targets = {
                    t: 0.0
                    if decisions[t] == -1
                    else sleeve
                    if decisions[t] == 1
                    else min(portfolio.get(t, 0.0) * prices[t], sleeve)
                    for t in tradeable
                }
                band_ref = dict.fromkeys(tradeable, sleeve)
                full_exit = {t for t, d in decisions.items() if d == -1}

            # Phase 1: exits and trims.
            for ticker in tradeable:
                price = prices[ticker]
                holding = portfolio.get(ticker, 0.0)
                if holding <= 0:
                    continue
                holding_value = holding * price
                wanted = targets[ticker]
                excess = holding_value - wanted
                if excess <= (0.0 if ticker in full_exit else EXECUTION_CONFIG.no_trade_threshold(band_ref[ticker])):
                    continue
                qty = min(holding, excess / price)
                execution_price = price * (1 - slippage_pct)
                cash_proceeds = qty * execution_price
                net_proceeds = cash_proceeds - cash_proceeds * commission_pct
                portfolio["USD"] = portfolio.get("USD", 0.0) + net_proceeds
                portfolio[ticker] = holding - qty
                nrtrades += 1
                trade_log.append(
                    {
                        "t": _iso(ts),
                        "symbol": ticker,
                        "side": "sell",
                        "qty": float(qty),
                        "price": float(execution_price),
                    }
                )

            # Phase 2: entries and top-ups, funded by the proceeds above.
            # No decision filter is needed: for a hold the target is capped at
            # the current value and for an exit it is zero, so both give a
            # shortfall <= 0 and skip below exactly as the old `if decision != 1`
            # made them.
            for ticker in tradeable:
                price = prices[ticker]
                holding = portfolio.get(ticker, 0.0)
                shortfall = targets[ticker] - holding * price
                if shortfall <= EXECUTION_CONFIG.no_trade_threshold(band_ref[ticker]):
                    continue
                cash = portfolio.get("USD", 0.0)
                buy_amount = min(shortfall, cash)
                if buy_amount <= 0:
                    continue
                commission_cost = buy_amount * commission_pct
                available = buy_amount - commission_cost
                execution_price = price * (1 + slippage_pct)
                bought_qty = available / execution_price
                portfolio["USD"] = cash - buy_amount
                portfolio[ticker] = holding + bought_qty
                nrtrades += 1
                trade_log.append(
                    {
                        "t": _iso(ts),
                        "symbol": ticker,
                        "side": "buy",
                        "qty": float(bought_qty),
                        "price": float(execution_price),
                    }
                )

            current_total = portfolio.get("USD", 0.0) + sum(portfolio.get(t, 0.0) * prices[t] for t in tradeable)
            portfolio_values.append(current_total)
            portfolio_timestamps.append(ts)

            # Equal-weight buy-and-hold over the tradeable tickers, rebased to the
            # first bar the backtest actually evaluated (not the first bar of the
            # input data) so the two chart lines start at the same point.
            if not first_prices:
                first_prices = {t: prices[t] for t in tradeable}
            benchmark_values.append(
                initial_capital
                * float(np.mean([prices[t] / first_prices[t] for t in tradeable if first_prices.get(t)]))
            )

        metrics = _compute_backtest_metrics(portfolio_values, bot.interval, risk_free_rate)
        bench = _resolve_benchmark_close(bot, benchmark_close, data_dict, data_was_given, backtest_period, save_to_db)
        metrics.update(_compute_alpha_metrics(portfolio_values, portfolio_timestamps, bench, bot.interval))

        # Buy-and-hold: equal-weight mean of individual B&H returns across the
        # TRADEABLE tickers. Including a benchmark here would average SPY into
        # the very number SPY is the benchmark for.
        bh_returns = []
        for _ticker, df in ((t, data_dict[t]) for t in tradeable if t in data_dict):
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

        # Added after the DB save so the persisted `result` stays exactly the nine
        # scalars `backtest_results` expects.
        if return_series:
            result.update(_build_series(portfolio_values, portfolio_timestamps, benchmark_values, trade_log))

        return result

    # ------------------------------------------------------------------ #
    #  Single-ticker path (N == 1)                                        #
    # ------------------------------------------------------------------ #
    symbol = tickers[0]
    backtest_period = None

    if data is None:
        backtest_period = _resolve_backtest_period(bot)
        try:
            data = bot.getYFDataWithTA(
                symbol=symbol,
                interval=bot.interval,
                period=backtest_period,
                saveToDB=save_to_db,
            )
        except Exception as e:
            raise ValueError(f"Failed to fetch historical data: {e}") from e
    elif isinstance(data, dict):
        # Unwrap single-ticker dict (e.g., passed from hyperparameter tuner)
        data = cast(pd.DataFrame, data.get(symbol, next(iter(data.values()))))

    # Narrow type: assert data is not a dict (helps mypy)
    assert not isinstance(data, dict)
    if "close" not in data.columns or "timestamp" not in data.columns:
        raise ValueError(
            "Provided data must have 'close' and 'timestamp' columns. "
            "It should also include all TA indicators required by decisionFunction."
        )

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

    # Final type narrowing for mypy
    assert not isinstance(data, dict), "data should be a DataFrame at this point"

    # trend_adx has ~26-bar warmup; warmup rows have trend_adx == 0.0 after fillna.
    has_ta_columns = "trend_adx" in data.columns

    portfolio = {"USD": initial_capital}
    portfolio_values = []
    portfolio_timestamps = []
    # No annotations here: the multi-ticker branch above already declared these
    # names in this same function scope, and re-annotating is a mypy no-redef.
    benchmark_values = []
    trade_log = []
    first_price: float | None = None
    nrtrades = 0

    for idx, row in data.iterrows():
        # Update bot's data cache with current slice to prevent look-ahead bias
        # if the bot uses self.data inside decisionFunction.
        bot.data = data.iloc[: idx + 1]

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

        # No-trade band, mirroring the live path. This was previously applied on
        # the multi-ticker branch only, so a single-ticker bot traded on every
        # signal in backtest while the same bot live went through
        # PortfolioManager.should_trade and skipped sub-band adjustments. The
        # backtested trade count and equity curve therefore did not describe the
        # strategy that actually ran.
        position_value = holdings * current_price

        if decision == 1:
            # An entry does not bypass the band: easy to exit, hard to enter.
            if cash > 0 and should_trade(cash, cash + position_value):
                execution_price = current_price * (1 + slippage_pct)
                commission_cost = cash * commission_pct
                available = cash - commission_cost
                quantity = available / execution_price
                portfolio["USD"] = 0.0
                portfolio[symbol] = holdings + quantity
                nrtrades += 1
                trade_log.append(
                    {
                        "t": _iso(row["timestamp"] if "timestamp" in row.index else None),
                        "symbol": symbol,
                        "side": "buy",
                        "qty": float(quantity),
                        "price": float(execution_price),
                    }
                )
        # Selling the whole position is a full exit, which always trades —
        # otherwise a position smaller than the band could never be closed.
        elif decision == -1 and holdings > 0 and should_trade(position_value, position_value, is_full_exit=True):
            execution_price = current_price * (1 - slippage_pct)
            cash_proceeds = holdings * execution_price
            commission_cost = cash_proceeds * commission_pct
            net_proceeds = cash_proceeds - commission_cost
            portfolio["USD"] = cash + net_proceeds
            portfolio[symbol] = 0.0
            nrtrades += 1
            trade_log.append(
                {
                    "t": _iso(row["timestamp"] if "timestamp" in row.index else None),
                    "symbol": symbol,
                    "side": "sell",
                    "qty": float(holdings),
                    "price": float(execution_price),
                }
            )

        current_cash = portfolio.get("USD", 0.0)
        current_holdings = portfolio.get(symbol, 0.0)
        portfolio_value = current_cash + (current_holdings * current_price)
        portfolio_values.append(portfolio_value)
        portfolio_timestamps.append(row["timestamp"] if "timestamp" in row.index else None)

        # Buy-and-hold rebased to the first evaluated bar, so it starts level with
        # the strategy curve rather than at the pre-warmup close.
        if first_price is None:
            first_price = current_price
        benchmark_values.append(initial_capital * current_price / first_price)

    metrics = _compute_backtest_metrics(portfolio_values, bot.interval, risk_free_rate)
    bench = _resolve_benchmark_close(bot, benchmark_close, data, data_was_given, backtest_period, save_to_db)
    metrics.update(_compute_alpha_metrics(portfolio_values, portfolio_timestamps, bench, bot.interval))

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

    if return_series:
        result.update(_build_series(portfolio_values, portfolio_timestamps, benchmark_values, trade_log))

    return result
