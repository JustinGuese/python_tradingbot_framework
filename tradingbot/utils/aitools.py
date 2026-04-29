"""AI tools for the Bot class using LangChain and OpenRouter.

LangSmith tracing (EU): If LANGSMITH_API_KEY is set, tracing is enabled and the EU
endpoint (LANGSMITH_ENDPOINT) is used when not set. Set LANGSMITH_TRACING=false to disable.

Two LLMs are supported:
- Main LLM (OPENROUTER_MAIN_MODEL, default deepseek/deepseek-v3.2): used by run_ai_with_tools
  for complex, multi-turn flows with tool use (market data, portfolio, trades).
- Cheap LLM (OPENROUTER_CHEAP_MODEL, default openrouter/free): used by run_ai_simple for
  simple single-turn text tasks that do not need tools, e.g. summarization, extraction,
  classification, rewriting, or formatting. Prefer run_ai_simple for these to save cost.

Cheap-first with fallback: Use run_ai_simple_with_fallback() (or Bot.run_ai_simple_with_fallback)
to try the cheap LLM first, verify output for sanity, and retry with the main LLM if the
result fails validation. This keeps cost low while guaranteeing sane results.

Extensibility: Subclasses can override Bot.get_ai_tools() to add custom tools; run_ai() merges
them automatically. run_ai_with_tools() accepts extra_tools= and optional tool_names= to
whitelist which base tools to include.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Callable, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from .db import (
    StockEarnings,
    StockInsiderTrade,
    StockNews,
    Trade,
    get_db_session,
)
from .portfolio_utils import calculate_portfolio_worth

logger = logging.getLogger(__name__)

# Optional: set level from env (e.g. AI_TOOLS_LOG_LEVEL=DEBUG in .env)
_level = os.environ.get("AI_TOOLS_LOG_LEVEL", "").upper()
if _level in ("DEBUG", "INFO", "WARNING", "ERROR"):
    logger.setLevel(getattr(logging, _level))
    # Ensure DEBUG/INFO are visible: add a handler if none (root handler may filter by level)
    if _level in ("DEBUG", "INFO") and not logger.handlers:
        _handler = logging.StreamHandler()
        _handler.setLevel(getattr(logging, _level))
        _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        logger.addHandler(_handler)
        logger.propagate = False  # we handle it ourselves so root doesn't filter


if TYPE_CHECKING:
    from .botclass import Bot


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# LangSmith tracing (EU region). Set LANGSMITH_API_KEY in env; optional LANGSMITH_TRACING, LANGSMITH_ENDPOINT.
LANGSMITH_EU_ENDPOINT = "https://eu.api.smith.langchain.com"


def _configure_langsmith_eu() -> None:
    """Enable LangSmith tracing with EU endpoint when LANGSMITH_API_KEY is set."""
    if not os.environ.get("LANGSMITH_API_KEY"):
        return
    if os.environ.get("LANGSMITH_TRACING", "true").lower() in ("true", "1", "yes"):
        if "LANGSMITH_ENDPOINT" not in os.environ:
            os.environ["LANGSMITH_ENDPOINT"] = LANGSMITH_EU_ENDPOINT


# Apply LangSmith EU config at import so LangChain picks it up
_configure_langsmith_eu()

# Main LLM: complex tasks with tool use (run_ai_with_tools). Default: deepseek/deepseek-v3.2
# Cheap LLM: simple single-turn text tasks (run_ai_simple). Default: openrouter/free
ENV_MAIN_MODEL = "OPENROUTER_MAIN_MODEL"
ENV_CHEAP_MODEL = "OPENROUTER_CHEAP_MODEL"
DEFAULT_MAIN_MODEL = "deepseek/deepseek-v3.2"
DEFAULT_CHEAP_MODEL = ""


def _get_main_model() -> str:
    """Model for tool-using, multi-turn flows. Override with OPENROUTER_MAIN_MODEL."""
    return os.environ.get(ENV_MAIN_MODEL, DEFAULT_MAIN_MODEL)


def _get_cheap_model() -> str:
    """Model for simple single-turn text tasks. Override with OPENROUTER_CHEAP_MODEL."""
    return os.environ.get(ENV_CHEAP_MODEL, DEFAULT_CHEAP_MODEL)


def _create_llm(model: str, api_key: str) -> ChatOpenAI:
    """Build ChatOpenAI for OpenRouter with the given model and API key."""
    return ChatOpenAI(
        model=model,
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        default_headers={
            "X-Title": "python_tradingbot_framework",
            'HTTP-Referer': 'https://github.com/JustinGuese/python_tradingbot_framework',
            "X-OpenRouter-Categories" : "personal-agent,cloud-agent"
        },
    )


def _build_tools(bot: "Bot") -> list:
    """Build LangChain tools bound to the given bot (closure over bot)."""

    # Reject common malformed args from XML/JSON parsing (e.g. "parameter", "true")
    _INVALID_SYMBOL_VALUES = frozenset({"parameter", "true", "false", "string", "null", ""})

    @tool
    def get_market_data(symbol: Optional[str] = None, period: str = "14d") -> str:
        """Get market data (OHLCV) for the last two weeks by default.
        Uses the bot's primary symbol if symbol is not provided.
        Period examples: 14d (two weeks), 5d, 1mo."""
        try:
            raw = (symbol.strip() if isinstance(symbol, str) and symbol.strip() else None) or None
            if raw and raw.lower() in _INVALID_SYMBOL_VALUES:
                logger.warning("get_market_data: invalid symbol value %r (likely malformed tool args)", raw)
                return f"Error: Invalid symbol value {raw!r}. Provide a valid ticker (e.g. AAPL, MSFT)."
            sym = raw or getattr(bot, "symbol", None)
            if not sym:
                logger.warning("get_market_data: no symbol provided and bot has no primary symbol")
                return "Error: No symbol provided and this bot has no primary symbol. Please provide a symbol."
            # Normalize period if it got mangled (e.g. "14invoke" -> "14d")
            if isinstance(period, str) and not (period.endswith("d") or period.endswith("mo")):
                digits = "".join(c for c in period if c.isdigit())
                if digits:
                    period = digits + "d"
                else:
                    period = "14d"
            logger.info("get_market_data: requested symbol=%r, period=%s, resolved sym=%r", symbol, period, sym)
            data = bot.getYFData(symbol=sym, interval="1d", period=period)
            if data is None or len(data) == 0:
                logger.warning("get_market_data: no data for sym=%r", sym)
                return f"No market data found for {sym} over period {period}."
            # Ensure we only use rows for the requested symbol (defensive against cache/API mixups)
            if "symbol" in data.columns:
                data = data[data["symbol"] == sym].copy()
                if data.empty:
                    logger.warning("get_market_data: no rows for sym=%r (data had other symbols)", sym)
                    return f"No market data found for {sym} over period {period}."
            actual_symbols = data["symbol"].unique().tolist() if "symbol" in data.columns else []
            logger.debug("get_market_data: sym=%r rows=%s actual_symbols=%s", sym, len(data), actual_symbols)
            summary = (
                f"Symbol {sym}, period {period}: {len(data)} trading intervals. "
                f"Min Close: {data['close'].min():.2f}, Max Close: {data['close'].max():.2f}, "
                f"Avg Close: {data['close'].mean():.2f}, Latest Close: {data['close'].iloc[-1]:.2f}. "
                f"Avg Volume: {data['volume'].mean():.0f}."
            )
            return summary
        except Exception as e:
            logger.exception("get_market_data failed: symbol=%r period=%s", symbol, period)
            return f"Error fetching market data: {e!s}"

    @tool
    def get_portfolio_status() -> str:
        """Get the current portfolio worth (USD) and holdings for this bot."""
        try:
            bot.dbBot = bot._bot_repository.create_or_get_bot(bot.bot_name)
            worth = calculate_portfolio_worth(bot.dbBot, bot._data_service)
            holdings = dict(bot.dbBot.portfolio)
            return f"portfolio_worth: {worth:.2f} USD, holdings: {holdings}"
        except Exception as e:
            return f"Error getting portfolio status: {e!s}"

    @tool
    def get_recent_trades(limit: int = 10) -> str:
        """Get the most recent trades for this bot (timestamp, symbol, buy/sell, quantity, price).
        For sells, profit of the closed trade is included."""
        try:
            with get_db_session() as session:
                results = (
                    session.query(Trade)
                    .filter_by(bot_name=bot.bot_name)
                    .order_by(Trade.timestamp.desc())
                    .limit(limit)
                    .all()
                )
            if not results:
                return f"No trades found for bot {bot.bot_name}."
            lines = []
            for r in results:
                side = "buy" if r.isBuy else "sell"
                line = f"  {r.timestamp} | {r.symbol} | {side} | quantity={r.quantity} | price={r.price}"
                if not r.isBuy and r.profit is not None:
                    line += f" | profit={r.profit:.2f}"
                lines.append(line)
            return "Recent trades:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error fetching trades: {e!s}"

    @tool
    def get_stock_news(symbol: Optional[str] = None, limit: int = 10) -> str:
        """Get recent news for a symbol (title, link, publisher, published_at) from the database."""
        try:
            sym = symbol if symbol else getattr(bot, "symbol", None)
            if not sym:
                return "Error: No symbol provided and this bot has no primary symbol. Please provide a symbol."
            with get_db_session() as session:
                results = (
                    session.query(StockNews)
                    .filter_by(symbol=sym)
                    .order_by(StockNews.published_at.desc())
                    .limit(limit)
                    .all()
                )
            if not results:
                return f"No news found for {sym}."
            lines = []
            for r in results:
                pub = r.published_at
                title = (r.title or "")[:80]
                link = r.link or ""
                pub_name = r.publisher or "N/A"
                lines.append(f"  {pub} | {title} | {link} | {pub_name}")
            return "Stock news:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error fetching stock news: {e!s}"

    @tool
    def get_stock_earnings(symbol: Optional[str] = None, limit: int = 10) -> str:
        """Get recent earnings dates and EPS (estimate, reported, surprise %) for a symbol from the database."""
        try:
            sym = symbol if symbol else getattr(bot, "symbol", None)
            if not sym:
                return "Error: No symbol provided and this bot has no primary symbol. Please provide a symbol."
            with get_db_session() as session:
                results = (
                    session.query(StockEarnings)
                    .filter_by(symbol=sym)
                    .order_by(StockEarnings.report_date.desc())
                    .limit(limit)
                    .all()
                )
            if not results:
                return f"No earnings found for {sym}."
            lines = []
            for r in results:
                est = r.eps_estimate if r.eps_estimate is not None else "N/A"
                rep = r.reported_eps if r.reported_eps is not None else "N/A"
                sur = f"{r.surprise_pct:.2f}%" if r.surprise_pct is not None else "N/A"
                lines.append(f"  {r.report_date} | eps_estimate={est} | reported_eps={rep} | surprise_pct={sur}")
            return "Stock earnings:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error fetching stock earnings: {e!s}"

    @tool
    def get_stock_insider_trades(symbol: Optional[str] = None, limit: int = 10) -> str:
        """Get recent insider transactions (insider, type, shares, value) for a symbol from the database."""
        try:
            sym = symbol if symbol else getattr(bot, "symbol", None)
            if not sym:
                return "Error: No symbol provided and this bot has no primary symbol. Please provide a symbol."
            with get_db_session() as session:
                results = (
                    session.query(StockInsiderTrade)
                    .filter_by(symbol=sym)
                    .order_by(StockInsiderTrade.transaction_date.desc())
                    .limit(limit)
                    .all()
                )
            if not results:
                return f"No insider trades found for {sym}."
            lines = []
            for r in results:
                name = r.insider_name or "N/A"
                ttype = r.transaction_type or "N/A"
                shares = r.shares if r.shares is not None else "N/A"
                value = f"{r.value:.2f}" if r.value is not None else "N/A"
                lines.append(f"  {r.transaction_date} | {name} | {ttype} | shares={shares} | value={value}")
            return "Stock insider trades:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error fetching stock insider trades: {e!s}"

    return [
        get_market_data,
        get_portfolio_status,
        get_recent_trades,
        get_stock_news,
        get_stock_earnings,
        get_stock_insider_trades,
    ]


def run_ai_with_tools(
    system_prompt: str,
    user_message: str,
    bot: "Bot",
    model: Optional[str] = None,
    max_tool_rounds: int = 5,
    extra_tools: Optional[List] = None,
    tool_names: Optional[List[str]] = None,
) -> str:
    """
    Run the AI with the given system prompt and user message, using tools bound to the bot.
    Uses the main LLM (OPENROUTER_MAIN_MODEL, default deepseek/deepseek-v3.2) for
    complex, multi-turn tool use. Pass model= to override.

    extra_tools: Optional list of LangChain tools to add (e.g. from bot.get_ai_tools()).
        Tools with the same name as a base tool override the base tool.
    tool_names: Optional whitelist of base tool names to include (e.g. ["get_market_data", "get_portfolio_status"]).
        If None, all base tools are included.

    Returns the final model response as a string.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Set it to your OpenRouter API key."
        )
    model = model or _get_main_model()
    llm = _create_llm(model, api_key)
    base_tools = _build_tools(bot)
    if tool_names is not None:
        base_tools = [t for t in base_tools if t.name in tool_names]
    tools = base_tools + (extra_tools or [])
    tools_by_name = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]
    response = None
    logger.info("run_ai_with_tools: model=%s max_tool_rounds=%s tool_count=%s", model, max_tool_rounds, len(tools_by_name))
    for round_num in range(max_tool_rounds):
        response = llm_with_tools.invoke(messages)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            logger.debug("run_ai_with_tools: round %s no tool_calls, done", round_num + 1)
            break
        logger.info("run_ai_with_tools: round %s tool_calls=%s", round_num + 1, [t.get("name") for t in tool_calls])
        messages.append(response)
        for tool_call in tool_calls:
            name = tool_call.get("name")
            args = tool_call.get("args") or {}
            tid = tool_call.get("id", "")
            logger.debug("run_ai_with_tools: tool name=%s args=%s", name, args)
            if name not in tools_by_name:
                logger.warning("run_ai_with_tools: unknown tool name=%s", name)
                messages.append(
                    ToolMessage(content=f"Unknown tool: {name}", tool_call_id=tid)
                )
                continue
            try:
                result = tools_by_name[name].invoke(args)
                content = result if isinstance(result, str) else str(result)
                logger.info("run_ai_with_tools: tool name=%s result_len=%s preview=%s", name, len(content), (content[:80] + "..." if len(content) > 80 else content))
            except Exception as e:
                logger.exception("run_ai_with_tools: tool name=%s error", name)
                content = f"Tool error: {e!s}"
            messages.append(ToolMessage(content=content, tool_call_id=tid))
    if response is None:
        logger.warning("run_ai_with_tools: no response after %s rounds", max_tool_rounds)
        return ""
    out = response.content if isinstance(response.content, str) else str(response.content)
    logger.debug("run_ai_with_tools: final response len=%s", len(out))
    return out


def _default_sanity_check(response: str) -> bool:
    """
    Default sanity check for cheap LLM output: non-empty, reasonable length,
    and no obvious refusal or error prefix.
    """
    if not response or not isinstance(response, str):
        return False
    text = response.strip()
    if len(text) < 3:
        return False
    lower = text.lower()
    refusal_start = (
        "i cannot",
        "i can't",
        "i'm unable",
        "i am unable",
        "error:",
        "i don't",
        "i do not",
    )
    if any(lower.startswith(p) for p in refusal_start):
        return False
    return True


def run_ai_simple(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
) -> str:
    """
    Run the AI for a single-turn, no-tools task (summarization, extraction, classification,
    rewriting). Uses the cheap LLM (OPENROUTER_CHEAP_MODEL, default openrouter/free).
    Pass model= to override. Use run_ai_with_tools when you need tool access (market data,
    portfolio, trades).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Set it to your OpenRouter API key."
        )
    model = model or _get_cheap_model()
    logger.debug("run_ai_simple: model=%s prompt_len=%s user_len=%s", model, len(system_prompt), len(user_message))
    llm = _create_llm(model, api_key)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]
    response = llm.invoke(messages)
    out = response.content if isinstance(response.content, str) else str(response.content)
    logger.debug("run_ai_simple: response_len=%s", len(out))
    return out


def run_ai_simple_with_fallback(
    system_prompt: str,
    user_message: str,
    sanity_check: Optional[Callable[[str], bool]] = None,
    fallback_to_main: bool = True,
) -> str:
    """
    Run a simple (no-tools) task with cheap LLM first; verify output for sanity;
    if validation fails, retry with main LLM. Use this to save cost when the task
    does not require tools.

    sanity_check: Callable that takes the response string and returns True if sane.
        If None, uses _default_sanity_check (non-empty, no refusal/error prefix).
    fallback_to_main: If True and sanity_check fails, run again with main model
        (OPENROUTER_MAIN_MODEL) and return that result.

    Returns the first sane response, or the main-model response after fallback.
    """
    check = sanity_check if sanity_check is not None else _default_sanity_check
    response = run_ai_simple(system_prompt, user_message, model=_get_cheap_model())
    sane = check(response)
    logger.debug("run_ai_simple_with_fallback: cheap response_len=%s sane=%s", len(response), sane)
    if sane:
        return response
    if fallback_to_main:
        logger.info("run_ai_simple_with_fallback: sanity check failed, retrying with main model")
        return run_ai_simple(system_prompt, user_message, model=_get_main_model())
    return response
