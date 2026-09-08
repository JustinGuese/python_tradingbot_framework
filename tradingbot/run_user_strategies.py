"""
Execute every live user-built strategy, once.

This is the *single* CronJob behind the strategy builder. User strategies are
rows, not modules, so one generic runner covers all of them — there is no
per-user CronJob, no generated manifest and no RBAC for this service to create
Kubernetes objects.

    values.yaml -> bots: [{name: run_user_strategies, schedule: "..."}]

Ownership: the `userstrategy` table belongs to **tradingbot-ui-backend**, which
runs its DDL. This module only reads it, through an explicit column list, so
columns added on the API side cannot break this job. That narrow list is the
contract between the two services; `REQUIRED_COLUMNS` states it, and the job
fails loudly at startup rather than silently trading nothing if it is not met.

Schedule it after the bots that hold its data dependencies and before
`calculate_portfolio_worth`, so a strategy's trades land on the same day its
equity is recorded.
"""

import json
import logging
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import _utcnow_naive, get_db_session, init_db
from tradingbot.utils.rule_bot import RuleBot
from tradingbot.utils.strategy_spec import SpecError, validate_spec

logger = logging.getLogger(__name__)

STRATEGY_TABLE = "userstrategy"

# The columns this job reads. Anything else the API stores is its own business.
REQUIRED_COLUMNS = ("id", "bot_name", "spec", "status", "last_run_at", "last_error")

_SELECT_LIVE = text(
    """
    SELECT id, bot_name, spec
    FROM userstrategy
    WHERE status = 'live'
    ORDER BY created_at
    """
)

_MARK_RUN = text(
    """
    UPDATE userstrategy
    SET last_run_at = :ran_at, last_error = :error
    WHERE id = :strategy_id
    """
)


def _assert_table_ready(session: Any) -> None:
    """
    Fail loudly if the API's table is missing or has lost a column we read.

    Without this the job would either crash with a bare ProgrammingError, or —
    worse, if the table merely went empty — exit 0 having traded nothing, which
    looks exactly like "no live strategies today".
    """
    # Inspector rather than information_schema: the latter is Postgres-only, so
    # it cannot be exercised by the SQLite-backed tests — meaning the one check
    # whose whole job is to catch drift would itself be untested.
    inspector = sa_inspect(session.get_bind())

    if STRATEGY_TABLE not in inspector.get_table_names():
        raise RuntimeError(
            f"Table '{STRATEGY_TABLE}' does not exist. It is created by "
            "tradingbot-ui-backend on boot; deploy that service before enabling "
            "this CronJob."
        )

    present = {column["name"] for column in inspector.get_columns(STRATEGY_TABLE)}
    missing = set(REQUIRED_COLUMNS) - present
    if missing:
        raise RuntimeError(
            f"Table '{STRATEGY_TABLE}' is missing columns this job reads: "
            f"{sorted(missing)}. The API's schema and this runner have diverged."
        )


def _as_dict(value: Any) -> dict:
    """`spec` arrives as a dict from Postgres JSON, or a string from SQLite."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str | bytes):
        return json.loads(value)
    raise TypeError(f"spec must be an object, got {type(value).__name__}")


def load_live_strategies(session: Any) -> list[tuple[str, str, dict]]:
    """(id, bot_name, spec) for every strategy currently marked live."""
    strategies = []
    for row in session.execute(_SELECT_LIVE).fetchall():
        strategy_id, bot_name, raw_spec = row[0], row[1], row[2]
        try:
            strategies.append((strategy_id, bot_name, _as_dict(raw_spec)))
        except (TypeError, ValueError) as exc:
            logger.error(f"{bot_name}: spec is not readable JSON, skipping: {exc}")
    return strategies


def run_one(bot_name: str, raw_spec: dict) -> int:
    """
    Run a single strategy's live iteration.

    Re-validates the spec before executing it. The API validated it on write,
    but a spec can become invalid afterwards — a `ta` upgrade renaming a column,
    or the allowed interval list narrowing — and executing a half-understood
    spec with real position sizing is worse than skipping it.
    """
    spec = validate_spec(raw_spec)
    bot = RuleBot(spec, bot_name, attach_db=True)
    bot.run()
    return len(spec.tickers)


def main() -> int:
    setup_logging()
    # Once per process rather than once per bot: Bot.__init__ calls this too,
    # and it is memoized, but calling it here means a schema problem surfaces
    # before the first strategy rather than during it.
    init_db()

    failures = 0
    with get_db_session() as session:
        _assert_table_ready(session)
        strategies = load_live_strategies(session)
        logger.info(f"Found {len(strategies)} live user strategies")

        for strategy_id, bot_name, raw_spec in strategies:
            error: str | None = None
            try:
                tickers = run_one(bot_name, raw_spec)
                logger.info(f"{bot_name}: ran over {tickers} ticker(s)")
            except SpecError as exc:
                # Not retryable: the spec itself is now invalid. Record it so the
                # user sees why their strategy stopped instead of silence.
                error = f"Invalid strategy: {exc}"
                logger.error(f"{bot_name}: {error}")
                failures += 1
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.error(f"{bot_name}: run failed: {error}", exc_info=True)
                failures += 1

            # Truncated to fit the API's column and to keep a runaway traceback
            # out of the database.
            session.execute(
                _MARK_RUN,
                {
                    "strategy_id": strategy_id,
                    "ran_at": _utcnow_naive(),
                    "error": error[:2000] if error else None,
                },
            )
            # Commit per strategy: one bad strategy must not roll back the
            # bookkeeping for the ones that already ran.
            session.commit()

    if failures:
        logger.warning(f"{failures} strategy/strategies failed this run")
    # Exit 0 even with failures: individual bad specs are recorded per row and
    # are a user problem, not a job problem. A non-zero exit would mark the
    # CronJob failed and hide the runs that did succeed.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
