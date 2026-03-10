"""
Signals Bank Bot

Reads trading signals from the Telegram channel -1001998690333
("The Signals Bank - FREE") stored in the telegram_messages table,
parses BUY/SELL entry signals via AI, and executes trades.

acted_on=True is set on the TelegramMessage row atomically after each
message is evaluated, so signals are never acted on twice even if the
bot crashes mid-run.

Symbol resolution is handled by the AI during classification — no
hardcoded symbol map needed. The AI returns the correct Yahoo Finance
ticker (e.g. "^GSPC" for US500, "BTC-USD" for BTC, "GC=F" for XAUUSD).
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from utils.botclass import Bot
from utils.db import TelegramMessage, get_db_session

logger = logging.getLogger(__name__)

CHANNEL_ID = "-1001998690333"
LOOKBACK_DAYS = 3       # Only consider signals from the last N days
POSITION_SIZE_PCT = 0.2  # Spend 20% of available cash per signal


def _classify_signal(text: str, summary: str) -> dict | None:
    """
    Ask the AI whether a message is a new BUY or SELL entry signal,
    and if so, what the correct Yahoo Finance ticker is.

    Returns:
        {"is_signal": bool, "direction": "BUY"|"SELL"|null, "yf_ticker": str|null}
        or None on parse failure.
    """
    from utils.ai import run_ai_simple

    system = (
        "You are a trading signal classifier. Given a Telegram message, "
        "decide if it is a NEW entry signal (a message telling traders to open "
        "a BUY or SELL position). Do NOT classify these as signals: "
        "take-profit / stop-loss hit notifications, performance recaps, "
        "market analysis without a concrete entry, or general commentary.\n\n"
        "If it is a signal, also return the correct Yahoo Finance ticker symbol. "
        "Examples: US500/US500.c → \"^GSPC\", DJ30/DJ30.c → \"^DJI\", "
        "BTC → \"BTC-USD\", XAUUSD → \"GC=F\", XAGUSD → \"SI=F\", "
        "EURUSD → \"EURUSD=X\", CADJPY → \"CADJPY=X\", GBPUSD → \"GBPUSD=X\".\n\n"
        "Respond with valid JSON only: "
        '{"is_signal": true/false, "direction": "BUY" or "SELL" or null, '
        '"yf_ticker": "<yahoo_finance_symbol>" or null}'
    )
    user = f"Text:\n{text[:1200]}\n\nAI summary:\n{summary}"
    try:
        raw = run_ai_simple(system_prompt=system, user_message=user)
        return json.loads(raw)
    except Exception as exc:
        logger.warning("AI signal classification failed: %s", exc)
        return None


class TelegramSignalsBankBot(Bot):
    """
    Follows trading signals from 'The Signals Bank - FREE' Telegram channel.

    Uses the complex makeOneIteration() pattern (like AIHedgeFundBot) because
    signal data comes from a database table rather than market data.
    """

    def __init__(self):
        super().__init__("TelegramSignalsBankBot", symbol=None)

    def makeOneIteration(self) -> int:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=LOOKBACK_DAYS)

        with get_db_session() as session:
            messages = (
                session.query(TelegramMessage)
                .filter(
                    TelegramMessage.channel == CHANNEL_ID,
                    TelegramMessage.symbol.isnot(None),
                    TelegramMessage.acted_on == False,  # noqa: E712
                    TelegramMessage.published_at >= cutoff,
                )
                .order_by(TelegramMessage.published_at.asc())
                .all()
            )
            # Detach: snapshot the data we need before closing the session
            pending = [
                (m.id, m.message_id, m.symbol or "", m.text or "", m.summary or "")
                for m in messages
            ]

        print(f">> {len(pending)} unacted message(s) to evaluate.")
        if not pending:
            return 0

        for db_id, message_id, channel_symbol, text, summary in pending:
            # Mark acted_on=True first — even if we can't trade, we won't re-evaluate
            with get_db_session() as session:
                msg = session.query(TelegramMessage).filter_by(id=db_id).one()
                msg.acted_on = True

            result = _classify_signal(text, summary)
            if not result or not result.get("is_signal"):
                print(f"  Msg {message_id} ({channel_symbol}): not a signal — skip")
                continue

            direction = result.get("direction")
            yf_symbol = result.get("yf_ticker")
            if not yf_symbol:
                print(f"  Msg {message_id} ({channel_symbol}): AI returned no yf_ticker — skip")
                continue

            print(f"  Msg {message_id}: {channel_symbol} → {yf_symbol} | {direction}")

            self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
            cash = self.dbBot.portfolio.get("USD", 0)
            holding = self.dbBot.portfolio.get(yf_symbol, 0)
            position_usd = round(cash * POSITION_SIZE_PCT, 2)

            if direction == "BUY" and position_usd > 10:
                print(f"    >> BUY {yf_symbol} ${position_usd:.2f} ({POSITION_SIZE_PCT:.0%} of ${cash:.2f})")
                self.buy(yf_symbol, quantityUSD=position_usd)
            elif direction == "SELL" and holding > 0:
                print(f"    >> SELL {yf_symbol} (holding: {holding})")
                self.sell(yf_symbol)
            else:
                print(f"    >> {direction} {yf_symbol}: nothing to act on "
                      f"(cash={cash:.2f}, holding={holding})")

        return 0


bot = TelegramSignalsBankBot()
bot.run()
