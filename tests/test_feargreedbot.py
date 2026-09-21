"""FearGreedBotQQQInverse is flat by default: in QQQ only between fear and neutral."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingbot.feargreedbot import INVERSE_BUY_AT_OR_BELOW, INVERSE_EXIT_AT_OR_ABOVE, FearGreedBotQQQ


def _bot(fg, usd, qqq):
    # Bypass Bot.__init__: it runs DDL and inserts a portfolio row.
    bot = FearGreedBotQQQ.__new__(FearGreedBotQQQ)
    bot.bot_name = "FearGreedBotQQQInverse"
    bot.symbol = "QQQ"
    bot.currentFearGreed = fg
    bot.dbBot = SimpleNamespace(portfolio={"USD": usd, "QQQ": qqq})
    bot.buy = MagicMock()
    bot.sell = MagicMock()
    return bot


@pytest.mark.parametrize(
    ("fg", "usd", "qqq", "expected"),
    [
        (INVERSE_BUY_AT_OR_BELOW, 10_000, 0, 1),  # fear, in cash -> buy
        (INVERSE_EXIT_AT_OR_ABOVE - 1, 10_000, 0, 0),  # neutral, in cash -> stay flat (used to buy)
        (INVERSE_EXIT_AT_OR_ABOVE - 1, 0, 50, 0),  # recovering, invested -> hold
        (INVERSE_EXIT_AT_OR_ABOVE, 0, 50, -1),  # back to neutral -> exit
        (90, 10_000, 0, 0),  # greed, in cash -> nothing
    ],
)
def test_inverse_rule(fg, usd, qqq, expected):
    bot = _bot(fg, usd, qqq)
    assert bot.makeOneIteration() == expected
    assert bot.buy.called == (expected == 1)
    assert bot.sell.called == (expected == -1)
