"""
Shared `if __name__ == "__main__":` entrypoint for bot modules.

No Bot or db dependency beyond what's passed in — just constructs and runs a bot.
"""

from typing import Any


def run_bot(bot_cls: type, **kwargs: Any) -> None:
    """
    Construct `bot_cls(**kwargs)` and call `.run()` on it.

    Guarded by `if __name__ == "__main__":` in every bot module: without that
    guard, importing the module executes the bot and trades a live paper
    portfolio as an import side effect. The Helm CronJob invokes
    `python <name>.py`, so `__name__ == "__main__"` there and production
    behaviour is unchanged.

    `Bot.run()` (tradingbot/utils/botclass.py) always returns `None` — it logs
    the outcome to the database rather than signalling success/failure via a
    return value — so this passes that `None` straight through rather than
    inventing an exit code.
    """
    bot = bot_cls(**kwargs)
    return bot.run()
