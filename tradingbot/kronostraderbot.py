"""
KronosTraderBot — Trades based on Kronos OHLCV forecasts stored by kronosbot.

Reads next-day predicted close prices from the `kronos_predictions` table
(written nightly by kronosbot at 22:05 UTC) and takes long/short/flat
positions based on the expected percentage move.

Tickers are loaded dynamically from the DB at startup — whatever kronosbot
predicted last night is what this bot trades. No hardcoded list.

Not backtestable: relies on live DB predictions, not historical yfinance data.
"""
import logging
from datetime import datetime, timedelta, timezone

from utils.core import Bot, get_db_session
from utils.db import KronosPrediction

logger = logging.getLogger(__name__)


def _load_predicted_tickers() -> list[str]:
    """Return all symbols that have a Kronos prediction for tomorrow or later."""
    try:
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with get_db_session() as session:
            rows = (
                session.query(KronosPrediction.symbol)
                .filter(KronosPrediction.target_date >= tomorrow)
                .distinct()
                .all()
            )
        tickers = [r.symbol for r in rows]
        logger.info(f"KronosTraderBot: loaded {len(tickers)} tickers from DB: {tickers}")
        return tickers
    except Exception as exc:
        logger.warning(f"KronosTraderBot: could not load tickers from DB ({exc}), falling back to defaults")
        return ["SPY", "QQQ", "GLD", "BTC-USD"]


class KronosTraderBot(Bot):
    param_grid = {
        "buy_threshold": [0.01, 0.02, 0.03],
        "sell_threshold": [0.005, 0.01, 0.02],
    }

    def __init__(self, buy_threshold: float = 0.02, sell_threshold: float = 0.01, **kwargs):
        """
        Args:
            buy_threshold:  Minimum predicted upside (fraction) to trigger a buy. Default 2%.
            sell_threshold: Minimum predicted downside (fraction) to trigger a sell. Default 1%.
        """
        tickers = _load_predicted_tickers()
        super().__init__(
            "KronosTraderBot",
            tickers=tickers,
            interval="1d",
            period="1y",
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
            **kwargs,
        )
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self._pred_cache: dict[str, float | None] = {}  # one DB query per ticker per run

    def _get_predicted_close(self, symbol: str) -> float | None:
        """Return the predicted close price for tomorrow for *symbol*, or None."""
        try:
            tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            with get_db_session() as session:
                pred = (
                    session.query(KronosPrediction)
                    .filter(
                        KronosPrediction.symbol == symbol,
                        KronosPrediction.target_date >= tomorrow,
                    )
                    .order_by(
                        KronosPrediction.target_date.asc(),
                        KronosPrediction.prediction_made_at.desc(),
                    )
                    .first()
                )
                # Extract value inside the session — ORM objects detach on session close
                return float(pred.predicted_close) if pred is not None else None  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning(f"KronosTraderBot: DB query failed for {symbol}: {exc}")
            return None

    def decisionFunction(self, row) -> int:
        symbol = self._current_ticker

        # Cache per symbol — one DB query per ticker, not one per historical row
        if symbol not in self._pred_cache:
            self._pred_cache[symbol] = self._get_predicted_close(symbol)
        predicted_close = self._pred_cache[symbol]

        if predicted_close is None:
            return 0

        current_close = row["close"]
        if not current_close or current_close <= 0:
            return 0

        pct_change = (predicted_close - current_close) / current_close

        # Only log on the most recent bar to avoid 252 log lines per ticker
        if self.data is not None and row.name == self.data.index[-1]:
            logger.info(
                f"KronosTraderBot: {symbol} current={current_close:.2f} "
                f"predicted={predicted_close:.2f} ({pct_change:+.2%})"
            )

        if pct_change > self.buy_threshold:
            return 1   # buy
        if pct_change < -self.sell_threshold:
            return -1  # sell
        return 0       # hold


bot = KronosTraderBot()
bot.run()
