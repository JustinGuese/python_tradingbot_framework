"""Backtest the quants.md (QuantJourney) strategy bots against QQQ.

Offline, like onetime_institutional_flow_backtest.py: bot rows go to a scratch SQLite
file, market data comes from yfinance in memory, nothing touches Postgres.

Each bot runs once over its whole common history. The H1/H2 columns are alpha
stats on the two halves of that one equity curve (not separate runs): a bot's
lookbacks need warmup, so cutting the data in half would starve the second half.

Usage: uv run python scripts/onetime_quantsmd_backtest.py > /tmp/quantsmd.json
Results: docs/backtests/quantsmd-2026-11.md
"""

import importlib
import json
import logging
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import tradingbot.utils.db as db  # noqa: E402

SCRATCH = os.environ.get("RETUNE_SCRATCH", "/tmp")
FETCH_START = "2000-01-01"
# yfinance serves intraday bars only over a trailing window, so intraday fetches use period, not start.
PERIOD_FOR = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d"}

eng = create_engine(f"sqlite:///{SCRATCH}/quantsmd.db", connect_args={"check_same_thread": False})
db.Base.metadata.create_all(eng)
db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
db._schema_initialized = True

from tradingbot.utils.data_service import DataService  # noqa: E402

_cache: dict = {}


def _yf(self, symbol, interval="1d", period="1y", save_to_db=False, use_cache=True):
    key = (symbol, interval)
    if key not in _cache:
        raw = None
        for attempt in range(4):
            if interval == "1d":
                raw = yf.download(symbol, start=FETCH_START, interval=interval, auto_adjust=True, progress=False)
            else:
                raw = yf.download(
                    symbol, period=PERIOD_FOR[interval], interval=interval, auto_adjust=True, progress=False
                )
            if raw is not None and len(raw) > 10:
                break
            time.sleep(3 * (attempt + 1))
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.dropna().reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(columns={"date": "timestamp", "datetime": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df["symbol"] = symbol
        _cache[key] = df[["symbol", "timestamp", "open", "high", "low", "close", "volume"]]
    return _cache[key].copy()


DataService.get_yf_data = _yf

from tradingbot.utils.backtest import _close_series, _compute_alpha_metrics, backtest_bot  # noqa: E402

BOTS = [
    ("quantsmd_continuous_futures_trend", "QuantsmdContinuousFuturesTrendBot", 0.62),
    ("quantsmd_vol_target_momentum", "QuantsmdVolTargetMomentumBot", 0.57),
    ("quantsmd_monthly_rotation", "QuantsmdMonthlyRotationBot", 0.53),
    ("quantsmd_sma_daily", "QuantsmdSmaDailyBot", 0.45),
    ("quantsmd_monthly_inverse_vol", "QuantsmdMonthlyInverseVolBot", 0.45),
    ("quantsmd_risk_parity_capped", "QuantsmdRiskParityCappedBot", 0.45),
    ("quantsmd_quarterly_te_cost_gate", "QuantsmdQuarterlyTeCostGateBot", 0.44),
    ("quantsmd_bollinger_reversion", "QuantsmdBollingerReversionBot", 0.42),
    ("quantsmd_vol_target_trend", "QuantsmdVolTargetTrendBot", 0.36),
    ("quantsmd_weekly_rsi_reversion", "QuantsmdWeeklyRsiReversionBot", 0.34),
    ("quantsmd_monthly_drift_etf", "QuantsmdMonthlyDriftEtfBot", 0.31),
    ("quantsmd_quarterly_dual_momentum", "QuantsmdQuarterlyDualMomentumBot", 0.12),
    ("quantsmd_signal_change_defensive", "QuantsmdSignalChangeDefensiveBot", 0.26),
    ("quantsmd_intraday_rsi_15m", "QuantsmdIntradayRsi15mBot", None),
    ("quantsmd_intraday_1m_ema_scalp", "QuantsmdIntraday1mEmaScalpBot", None),
    ("quantsmd_intraday_1h_sma_trend", "QuantsmdIntraday1hSmaTrendBot", None),
    ("quantsmd_monthly_circuit_breaker", "QuantsmdMonthlyCircuitBreakerBot", None),
    ("quantsmd_daily_partial_drift", "QuantsmdDailyPartialDriftBot", 0.30),
    ("quantsmd_pairs_ratio_zscore", "QuantsmdPairsRatioZscoreBot", -0.05),
    ("quantsmd_pairs_hedge_ratio", "QuantsmdPairsHedgeRatioBot", -0.30),
    ("quantsmd_cross_sectional_momentum", "QuantsmdCrossSectionalMomentumBot", -0.24),
    ("quantsmd_cross_sectional_reversal", "QuantsmdCrossSectionalReversalBot", -0.24),
    ("quantsmd_risk_parity_multiasset", "QuantsmdRiskParityMultiassetBot", 0.16),
    ("quantsmd_macd_trend", "QuantsmdMacdTrendBot", 0.22),
    ("quantsmd_fx_time_series_momentum", "QuantsmdFxTimeSeriesMomentumBot", -0.59),
    ("quantsmd_fx_cross_sectional_momentum", "QuantsmdFxCrossSectionalMomentumBot", -1.00),
    ("quantsmd_market_sma_cross", "QuantsmdMarketSmaCrossBot", 0.21),
    ("quantsmd_market_rsi_reversion", "QuantsmdMarketRsiReversionBot", -0.17),
    ("quantsmd_limit_rsi_dip", "QuantsmdLimitRsiDipBot", 0.14),
    ("quantsmd_limit_trend_pullback", "QuantsmdLimitTrendPullbackBot", -0.04),
    ("quantsmd_stop_breakout_entry", "QuantsmdStopBreakoutEntryBot", -0.19),
    ("quantsmd_stop_loss_protection", "QuantsmdStopLossProtectionBot", 0.00),
    ("quantsmd_stop_limit_breakout", "QuantsmdStopLimitBreakoutBot", -0.05),
    ("quantsmd_stop_limit_protection", "QuantsmdStopLimitProtectionBot", 0.02),
    ("quantsmd_trailing_stop_trend", "QuantsmdTrailingStopTrendBot", -0.33),
    ("quantsmd_trailing_stop_rsi", "QuantsmdTrailingStopRsiBot", -0.41),
    ("quantsmd_trailing_stop_limit", "QuantsmdTrailingStopLimitBot", -0.38),
    ("quantsmd_bracket_trend", "QuantsmdBracketTrendBot", -0.38),
    ("quantsmd_bracket_rsi_reversion", "QuantsmdBracketRsiReversionBot", -0.43),
    ("quantsmd_oco_dip_or_breakout", "QuantsmdOcoDipOrBreakoutBot", -0.08),
    ("quantsmd_intraday_5m_bracket_reversion", "QuantsmdIntraday5mBracketReversionBot", None),
    ("quantsmd_intraday_30m_stop_breakout", "QuantsmdIntraday30mStopBreakoutBot", None),
    ("quantsmd_signal_change_rotation_orders", "QuantsmdSignalChangeRotationOrdersBot", 0.22),
    ("quantsmd_fx_momentum_lots", "QuantsmdFxMomentumLotsBot", -0.71),
    ("quantsmd_futures_donchian_contracts", "QuantsmdFuturesDonchianContractsBot", 0.30),
    ("quantsmd_macd_trend_v2", "QuantsmdMacdTrendV2Bot", None),
    ("quantsmd_daily_partial_drift_v2", "QuantsmdDailyPartialDriftV2Bot", None),
    ("quantsmd_cross_sectional_reversal_v2", "QuantsmdCrossSectionalReversalV2Bot", None),
    ("quantsmd_cross_sectional_momentum_v2", "QuantsmdCrossSectionalMomentumV2Bot", None),
    ("quantsmd_fx_etf_time_series_momentum", "QuantsmdFxEtfTimeSeriesMomentumBot", None),
    ("quantsmd_order_ensemble", "QuantsmdOrderEnsembleBot", None),
    ("quantsmd_pairs_basket_zscore", "QuantsmdPairsBasketZscoreBot", None),
    ("quantsmd_sma_daily_gated", "QuantsmdSmaDailyGatedBot", None),
    ("quantsmd_quarterly_te_cost_gate_gated", "QuantsmdQuarterlyTeCostGateGatedBot", None),
    ("quantsmd_intraday_gated_ema", "QuantsmdIntradayGatedEmaBot", None),
    ("quantsmd_sma_daily_gated_v3", "QuantsmdSmaDailyGatedV3Bot", None),
    ("quantsmd_gated_blend", "QuantsmdGatedBlendBot", None),
]


def _max_dd(values) -> float:
    v = np.asarray(values, dtype=float)
    return float((v / np.maximum.accumulate(v) - 1).min()) if len(v) else 0.0


def _stats(values, stamps, qqq, interval) -> dict:
    m = _compute_alpha_metrics(values, stamps, qqq, interval)
    return {"alpha": m["alpha"], "alpha_t": m["alpha_t"], "beta": m["beta"], "corr": m["benchmark_corr"]}


def run_one(module: str, cls_name: str, source_sharpe: float, qqq_frame: pd.DataFrame) -> dict:
    cls = getattr(importlib.import_module(f"tradingbot.{module}"), cls_name)
    bot = cls()
    interval = bot.interval
    intraday = interval != "1d"
    period = PERIOD_FOR[interval] if intraday else "max"
    frames = {t: bot.getYFDataWithTA(symbol=t, interval=interval, period=period) for t in bot.tickers}
    qqq = _close_series(_yf(None, "QQQ", interval=interval) if intraday else qqq_frame)
    r = backtest_bot(
        bot, data=frames, benchmark_close=qqq, save_to_db=False, save_results_to_db=False, return_series=True
    )
    values = [p["v"] for p in r["equity_curve"]]
    stamps = [pd.Timestamp(p["t"]) for p in r["equity_curve"]]
    bh = [p["v"] for p in r["buy_hold_curve"]]
    mid = len(values) // 2
    q = qqq.copy()
    q.index = pd.to_datetime(q.index)
    years = max((stamps[-1] - stamps[0]).days / 365.25, 1e-9)
    qqq_window = q.reindex(stamps, method="ffill")
    return {
        "bot": cls_name,
        "source_sharpe": source_sharpe,
        "start": str(stamps[0].date()),
        "end": str(stamps[-1].date()),
        "bars": len(values),
        "alpha": r["alpha"],
        "alpha_t": r["alpha_t"],
        "beta": r["beta"],
        "corr": r["benchmark_corr"],
        "sharpe": r["sharpe_ratio"],
        "cagr": r["yearly_return"],
        "maxdd": _max_dd(values),
        "trades": r["nrtrades"],
        "qqq_cagr": float((qqq_window.iloc[-1] / qqq_window.iloc[0]) ** (1 / years) - 1),
        "bh_cagr": float((bh[-1] / bh[0]) ** (1 / years) - 1) if bh else None,
        "intraday": intraday,
        "h1": _stats(values[:mid], stamps[:mid], q, interval),
        "h2": _stats(values[mid:], stamps[mid:], q, interval),
    }


def main():
    t0 = time.time()
    qqq_frame = _yf(None, "QQQ")
    only = set(sys.argv[1:])
    out = []
    for module, cls_name, sharpe in BOTS:
        if only and module not in only:
            continue
        try:
            out.append(run_one(module, cls_name, sharpe, qqq_frame))
            print(f"{cls_name}: alpha_t {out[-1]['alpha_t']}", file=sys.stderr)
        except Exception as exc:
            out.append({"bot": cls_name, "error": repr(exc)})
            print(f"{cls_name}: FAILED {exc!r}", file=sys.stderr)
    print(json.dumps(out, default=float, indent=1))
    print(f"done in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
