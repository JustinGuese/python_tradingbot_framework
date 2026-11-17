"""
Trial registry for tracking backtest runs and parameter evaluations (Step S1d).

Records alpha, alpha_t, beta, n for bookkeeping and multiple-testing tracking.
Bookkeeping only: no threshold changes anywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date, datetime
from typing import Any

from sqlalchemy import func

from .db import BacktestTrial, _utcnow_naive, get_db_session, init_db

logger = logging.getLogger(__name__)


def compute_params_hash(params: Any) -> str:
    """
    Deterministic sha256 hash (hex string) of bot parameters.
    Handles dicts (sorted keys), primitives, None, etc.
    """
    if params is None:
        params = {}
    if isinstance(params, dict):
        try:
            serialized = json.dumps(params, sort_keys=True, default=str)
        except Exception:
            serialized = str(sorted(params.items()))
    else:
        serialized = str(params)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _sanitize_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def record_trial(
    bot: str | Any,
    params: dict[str, Any] | None = None,
    params_hash: str | None = None,
    date: datetime | date | str | None = None,
    alpha: float | None = None,
    alpha_t: float | None = None,
    beta: float | None = None,
    n: int | None = None,
    **kwargs: Any,
) -> BacktestTrial | None:
    """
    Record a single backtest trial in `backtest_trials`.

    Failures to write are caught and swallowed (returns None), ensuring a DB
    outage or error never crashes a backtest.
    """
    try:
        init_db()

        # Resolve bot name
        if isinstance(bot, str):
            bot_name = bot
        elif hasattr(bot, "bot_name"):
            bot_name = str(bot.bot_name)
        elif hasattr(bot, "__name__"):
            bot_name = str(bot.__name__)
        else:
            bot_name = str(bot)

        # Resolve params and params_hash
        if params is None and hasattr(bot, "params"):
            params = getattr(bot, "params", {})

        if params_hash is None:
            params_hash = compute_params_hash(params)

        # Resolve date
        trial_dt: datetime
        if date is None:
            date_arg = kwargs.get("trial_date")
            if date_arg is not None:
                date = date_arg

        if date is None:
            trial_dt = _utcnow_naive()
        elif isinstance(date, datetime):
            trial_dt = date.replace(tzinfo=None) if date.tzinfo else date
        elif isinstance(date, (datetime,)):
            trial_dt = date
        elif (
            hasattr(date, "year") and hasattr(date, "month") and hasattr(date, "day") and not isinstance(date, datetime)
        ):
            trial_dt = datetime.combine(date, datetime.min.time())
        elif isinstance(date, str):
            try:
                parsed = datetime.fromisoformat(date)
                trial_dt = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except Exception:
                trial_dt = _utcnow_naive()
        else:
            trial_dt = _utcnow_naive()

        clean_alpha = _sanitize_float(alpha)
        clean_alpha_t = _sanitize_float(alpha_t)
        clean_beta = _sanitize_float(beta)
        clean_n = int(n) if n is not None else None

        row = BacktestTrial(
            bot=bot_name,
            params_hash=params_hash,
            date=trial_dt,
            alpha=clean_alpha,
            alpha_t=clean_alpha_t,
            beta=clean_beta,
            n=clean_n,
        )

        with get_db_session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)

        return row
    except Exception as exc:
        logger.warning("Failed to record backtest trial to DB: %s", exc)
        return None


def trial_count(bot: str | Any = "all") -> int:
    """
    Count trials recorded in `backtest_trials`.

    Args:
        bot: "all" or None for total trials across all bots, or a specific bot name/instance.

    Returns:
        Number of trials recorded, or 0 on database failure.
    """
    try:
        init_db()
        with get_db_session() as session:
            query = session.query(func.count(BacktestTrial.id))
            if bot is not None and bot != "all":
                if isinstance(bot, str):
                    bot_name = bot
                elif hasattr(bot, "bot_name"):
                    bot_name = str(bot.bot_name)
                elif hasattr(bot, "__name__"):
                    bot_name = str(bot.__name__)
                else:
                    bot_name = str(bot)
                query = query.filter(BacktestTrial.bot == bot_name)
            cnt = query.scalar()
            return int(cnt or 0)
    except Exception as exc:
        logger.warning("Failed to query trial_count from DB: %s", exc)
        return 0
