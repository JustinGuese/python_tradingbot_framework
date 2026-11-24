"""
Forward paper prop-firm challenges, one snapshot per sim per trading day.

Not a bot and nothing trades. Replays the live sleeve returns from portfolio_worth through
FTMO, The5ers and Topstep rules at fixed leverage, plus a QQQ twin per sim as the control
(utils/prop_forward.py), and upserts one row per sim into `prop_challenge_sim`.

The start date is PROPSIM_START (ISO date) or 2026-11-24. Sims are a pure function of
portfolio_worth, so the job is idempotent and a missed day repairs itself.

Schedule: 40 22 * * 1-5, after the 22:00 UTC portfolio-worth job. Exits 1 if a source
series is more than 4 days stale, because every sim would then be frozen on old data.
"""

import logging
import os
import sys
from datetime import UTC, date, datetime

from tradingbot.utils.alpha_report import MAX_GAP_DAYS, load_irx, load_worth
from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import PropChallengeSim, get_db_session, init_db
from tradingbot.utils.prop_forward import DEFAULT_START, SIMS, SOURCES, SimSpec, SimState, latest_return_date, run_sim

logger = logging.getLogger(__name__)


def _start() -> date:
    raw = os.environ.get("PROPSIM_START")
    return date.fromisoformat(raw) if raw else DEFAULT_START


def run_daily(today: date | None = None, start: date | None = None) -> list[tuple[SimSpec, SimState]]:
    """Compute every sim, upsert today's snapshot, and log the table. Raises LookupError on stale or missing data."""
    today = today or datetime.now(UTC).date()
    start = start or _start()
    with get_db_session() as session:
        worth = load_worth(session)
        for source, weights in SOURCES.items():
            last = latest_return_date(worth, weights)
            if last is None:
                raise LookupError(f"source {source!r} has no portfolio_worth series")
            if (today - last).days > MAX_GAP_DAYS:
                raise LookupError(f"source {source!r} is stale: last row {last}, today {today}")
        irx = load_irx(start)
        results = [(spec, run_sim(spec, worth, irx if not irx.empty else None, start)) for spec in SIMS]
        existing = {r.sim_name: r for r in session.query(PropChallengeSim).filter(PropChallengeSim.as_of == today)}
        for spec, st in results:
            previous = (
                session.query(PropChallengeSim)
                .filter(PropChallengeSim.sim_name == spec.name, PropChallengeSim.as_of < today)
                .order_by(PropChallengeSim.as_of.desc())
                .first()
            )
            if previous is not None and (previous.stage, previous.status) != (st.stage, st.status):
                logger.warning("%s: %s/%s -> %s/%s", spec.name, previous.stage, previous.status, st.stage, st.status)
            rec = existing.get(spec.name) or PropChallengeSim(as_of=today, sim_name=spec.name)
            rec.preset, rec.source, rec.leverage, rec.start_date = spec.preset, spec.source, spec.leverage, start
            rec.stage, rec.status = st.stage, st.status
            rec.trading_days, rec.stage_day = st.trading_days, st.stage_day
            rec.equity, rec.limit_equity, rec.target_equity = st.equity, st.limit_equity, st.target_equity
            rec.cum_return, rec.payouts_usd = st.cum_return, st.payouts_usd
            session.add(rec)
    return results


def format_table(results: list[tuple[SimSpec, SimState]]) -> str:
    lines = [f"{'sim':<30} {'stage':<8} {'status':<9} {'days':>4} {'equity':>7} {'limit':>7} {'target':>7} {'cum':>7}"]
    for spec, st in results:
        eq = f"{st.equity:.3f}" if st.equity is not None else "-"
        lim = f"{st.limit_equity:.3f}" if st.limit_equity is not None else "-"
        tgt = f"{st.target_equity:.3f}" if st.target_equity is not None else "-"
        lines.append(
            f"{spec.name:<30} {st.stage:<8} {st.status:<9} {st.trading_days:>4} {eq:>7} {lim:>7} {tgt:>7} "
            f"{st.cum_return * 100:>6.1f}%"
        )
    return "\n".join(lines)


def main() -> int:
    setup_logging()
    init_db()
    try:
        results = run_daily()
    except LookupError as e:
        logger.error("prop sim not written: %s", e)
        return 1
    logger.info("Prop challenge sims since %s:\n%s", _start(), format_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
