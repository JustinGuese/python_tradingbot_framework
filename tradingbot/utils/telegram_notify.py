import logging
import httpx

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = 4000
TELEGRAM_TIMEOUT_SECONDS = 15.0


def _split_message(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into chunks of at most max_length characters on newline boundaries if possible."""
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_len = 0

    for line in lines:
        line_len = len(line)
        # Account for newline if chunk is non-empty
        extra_len = 1 if current_chunk else 0

        if current_len + extra_len + line_len <= max_length:
            current_chunk.append(line)
            current_len += extra_len + line_len
        else:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0

            # If a single line exceeds max_length, hard-split it
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]

            if line:
                current_chunk.append(line)
                current_len = len(line)

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    """Send a message to a Telegram chat via Bot API.

    Splits messages longer than 4000 characters on line boundaries.
    Raises RuntimeError on failure. The error message is sanitized so that
    the bot token is never exposed in logs or exceptions.
    """
    if not token:
        raise RuntimeError("Telegram bot token is required")
    if not chat_id:
        raise RuntimeError("Telegram chat_id is required")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _split_message(text)
    if not chunks:
        return

    def _sanitize(msg: str) -> str:
        if token and token in msg:
            return msg.replace(token, "[REDACTED_TOKEN]")
        return msg

    try:
        with httpx.Client(timeout=TELEGRAM_TIMEOUT_SECONDS) as client:
            for chunk in chunks:
                payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }
                resp = client.post(url, json=payload)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Telegram API request failed with status {resp.status_code}: {_sanitize(resp.text)}"
                    )

                try:
                    data = resp.json()
                except Exception as e:
                    raise RuntimeError(f"Telegram API returned invalid JSON: {_sanitize(str(e))}") from None

                if not data.get("ok"):
                    desc = data.get("description", "Unknown error")
                    raise RuntimeError(f"Telegram API returned ok=false: {_sanitize(str(desc))}")
    except RuntimeError:
        raise
    except Exception as e:
        sanitized_err = _sanitize(str(e))
        raise RuntimeError(f"Failed to send Telegram message: {sanitized_err}") from None
