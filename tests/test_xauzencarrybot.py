"""
Unit tests for XAUZenCarryBot's mirroring logic.

These never touch a database: the bot's whole job is to turn two parent
portfolio dicts into one target-weight dict, so the parents are stubbed at
BotRepository and the effect is asserted on the rebalancePortfolio call.
"""

from datetime import datetime, timedelta

import pytest

from tradingbot.xauzencarrybot import (
    ALPHA_BOT,
    ALPHA_SYMBOL,
    CARRY_BOT,
    XAUZenCarryBot,
)
from utils.core import Bot

FRESH = datetime.utcnow() - timedelta(minutes=5)


@pytest.fixture
def make_bot(mocker):
    """Build an XAUZenCarryBot with the DB-touching base __init__ stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _make(exclude_iau: bool = False):
        bot = XAUZenCarryBot(exclude_iau=exclude_iau)
        bot.rebalancePortfolio = mocker.MagicMock()
        bot.getLatestPricesBatch = mocker.MagicMock(return_value={})
        return bot

    return _make


@pytest.fixture
def parents(mocker):
    """
    Stub both parents' `bots.portfolio` rows and last successful run times.

    Returns a setter: parents(alpha={...}, carry={...}, alpha_run=..., carry_run=...).
    Passing None for a portfolio means "that bot has no row".
    """
    portfolios: dict = {}
    runs: dict = {}

    mocker.patch(
        "tradingbot.xauzencarrybot.BotRepository.read_portfolio",
        side_effect=lambda name: portfolios.get(name),
    )
    mocker.patch(
        "tradingbot.xauzencarrybot.BotRepository.last_successful_run",
        side_effect=lambda name: runs.get(name),
    )

    def _set(alpha=None, carry=None, alpha_run=FRESH, carry_run=FRESH):
        portfolios[ALPHA_BOT] = alpha
        portfolios[CARRY_BOT] = carry
        runs[ALPHA_BOT] = alpha_run
        runs[CARRY_BOT] = carry_run

    return _set


def _target(bot) -> dict:
    """The weights the bot passed to rebalancePortfolio."""
    bot.rebalancePortfolio.assert_called_once()
    return bot.rebalancePortfolio.call_args[0][0]


# ----------------------------------------------------------------------
# Priority stacking
# ----------------------------------------------------------------------


def test_alpha_leg_preempts_carry(make_bot, parents):
    """A live ^XAU position wins outright — the carry basket is not blended in."""
    bot = make_bot()
    parents(
        alpha={"USD": 0.0, ALPHA_SYMBOL: 32.7},
        carry={"USD": 5000.0, "VTI": 10.0},
    )

    assert bot.makeOneIteration() == 1
    assert _target(bot) == {ALPHA_SYMBOL: 1.0}


def test_carry_leg_owns_idle_days(make_bot, parents):
    """When the alpha parent is flat, the carry basket's weights are mirrored."""
    bot = make_bot()
    parents(
        alpha={"USD": 10915.71},
        carry={"USD": 2000.0, "VTI": 10.0, "TLT": 20.0},
    )
    bot.getLatestPricesBatch.return_value = {"VTI": 300.0, "TLT": 100.0}

    assert bot.makeOneIteration() == 0
    # 3000 VTI + 2000 TLT + 2000 cash = 7000 total
    target = _target(bot)
    assert target["VTI"] == pytest.approx(3000 / 7000)
    assert target["TLT"] == pytest.approx(2000 / 7000)
    assert target["USD"] == pytest.approx(2000 / 7000)
    assert sum(target.values()) == pytest.approx(1.0)


def test_dust_position_is_not_a_signal(make_bot, parents):
    """A residual ^XAU quantity below the dust floor must not trigger the alpha leg."""
    bot = make_bot()
    parents(
        alpha={"USD": 10000.0, ALPHA_SYMBOL: 1e-9},
        carry={"USD": 10000.0},
    )

    assert bot.makeOneIteration() == 0
    assert _target(bot) == {"USD": 1.0}


# ----------------------------------------------------------------------
# Carry weight derivation
# ----------------------------------------------------------------------


def test_all_cash_carry_parent_maps_to_cash(make_bot, parents):
    bot = make_bot()
    parents(alpha={"USD": 10000.0}, carry={"USD": 9000.0})

    assert bot.makeOneIteration() == 0
    assert _target(bot) == {"USD": 1.0}
    bot.getLatestPricesBatch.assert_not_called()


def test_exclude_iau_moves_gold_weight_to_cash(make_bot, parents):
    """
    Excluding IAU must park that sleeve in cash, NOT lever up the equity legs.

    The regression this pins: leaving IAU out of the denominator as well as the
    weights would have made VTI 100% of the book instead of 50%.
    """
    bot = make_bot(exclude_iau=True)
    parents(
        alpha={"USD": 10000.0},
        carry={"USD": 0.0, "VTI": 10.0, "IAU": 100.0},
    )
    bot.getLatestPricesBatch.return_value = {"VTI": 300.0, "IAU": 30.0}

    bot.makeOneIteration()

    target = _target(bot)
    assert "IAU" not in target
    # 3000 VTI + 3000 IAU = 6000 total; the gold half becomes cash.
    assert target["VTI"] == pytest.approx(0.5)
    assert target["USD"] == pytest.approx(0.5)
    # IAU still has to be priced — it is part of the parent's total value.
    assert "IAU" in bot.getLatestPricesBatch.call_args[0][0]


def test_unknown_asset_in_parent_is_treated_as_cash(make_bot, parents):
    """A holding outside the carry universe is excluded, never silently mirrored."""
    bot = make_bot()
    parents(
        alpha={"USD": 10000.0},
        carry={"USD": 5000.0, "VTI": 10.0, "MEME": 1000.0},
    )
    bot.getLatestPricesBatch.return_value = {"VTI": 500.0, "MEME": 5.0}

    bot.makeOneIteration()

    target = _target(bot)
    # 5000 VTI + 5000 MEME + 5000 cash = 15000 total
    assert "MEME" not in target
    assert target["VTI"] == pytest.approx(1 / 3)
    assert target["USD"] == pytest.approx(2 / 3)


def test_missing_price_aborts_instead_of_reweighting(make_bot, parents):
    """Dropping an unpriceable leg would silently change the strategy."""
    bot = make_bot()
    parents(
        alpha={"USD": 10000.0},
        carry={"USD": 0.0, "VTI": 10.0, "TLT": 20.0},
    )
    bot.getLatestPricesBatch.return_value = {"VTI": 300.0}

    with pytest.raises(RuntimeError, match="No price"):
        bot.makeOneIteration()
    bot.rebalancePortfolio.assert_not_called()


# ----------------------------------------------------------------------
# Parent health guards
# ----------------------------------------------------------------------


def test_stale_alpha_parent_aborts_the_run(make_bot, parents):
    """A dead XAUZenbotTreeBot CronJob leaves a fossil position — don't mirror it."""
    bot = make_bot()
    parents(
        alpha={"USD": 0.0, ALPHA_SYMBOL: 32.7},
        carry={"USD": 10000.0},
        alpha_run=datetime.utcnow() - timedelta(hours=48),
    )

    with pytest.raises(RuntimeError, match="stale"):
        bot.makeOneIteration()
    bot.rebalancePortfolio.assert_not_called()


def test_weekly_carry_parent_is_not_considered_stale(make_bot, parents):
    """GoldenButterflyMomBot runs Mondays, so ~7 days old is normal, not broken."""
    bot = make_bot()
    parents(
        alpha={"USD": 10000.0},
        carry={"USD": 10000.0},
        carry_run=datetime.utcnow() - timedelta(days=7),
    )

    assert bot.makeOneIteration() == 0


def test_missing_parent_row_aborts_the_run(make_bot, parents):
    bot = make_bot()
    parents(alpha=None, carry={"USD": 10000.0})

    with pytest.raises(RuntimeError, match="no row"):
        bot.makeOneIteration()
    bot.rebalancePortfolio.assert_not_called()


def test_parent_that_never_ran_aborts_the_run(make_bot, parents):
    bot = make_bot()
    parents(alpha={"USD": 10000.0}, carry={"USD": 10000.0}, alpha_run=None)

    with pytest.raises(RuntimeError, match="never completed"):
        bot.makeOneIteration()
    bot.rebalancePortfolio.assert_not_called()
