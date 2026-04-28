# AI Tools (LangChain + OpenRouter)

> [!WARNING]
> **DISCLAIMER:** This software is for educational and research purposes only. AI-generated analysis and trading decisions are not financial advice and involve significant risk of loss. Use of these features is strictly at your own risk.

The framework includes AI tools that let an LLM access market data, portfolio status, and recent trades (including profit on sells) via LangChain and the OpenRouter API.

## Two LLMs: Main and Cheap

- **Main LLM** (used by `run_ai` / `run_ai_with_tools` when tools are needed): complex, multi-turn flows with tool use. Set via **OPENROUTER_MAIN_MODEL**; default `deepseek/deepseek-v3.2`.
- **Cheap LLM** (used by `run_ai_simple`): simple single-turn text tasks that do not need tools (summarization, extraction, classification, rewriting). Set via **OPENROUTER_CHEAP_MODEL**; default `openai/gpt-oss-120b`.

Use the cheap LLM for tasks that only need one round of text in/out (e.g. "summarize this", "extract key points", "classify as buy/hold/sell", "rewrite in one sentence"). Use the main LLM when the model must call tools (market data, portfolio, trades).

### Cheap-first with fallback

To save cost while still guaranteeing sane results, use **`run_ai_simple_with_fallback`**: it runs the cheap LLM first, validates the output with a sanity check, and retries with the main LLM if validation fails.

- **sanity_check**: Optional callable `(response: str) -> bool`. If `None`, a default check is used (non-empty, reasonable length, no obvious refusal or error prefix such as "I cannot", "Error:").
- **fallback_to_main**: If `True` (default) and the sanity check fails, the same prompt is run again with the main model and that result is returned.

```python
# Prefer this over run_ai_simple when you want cheap-first but safe fallback
result = bot.run_ai_simple_with_fallback(
    system_prompt="You classify sentiment as buy/hold/sell.",
    user_message="Classify: ...",
    sanity_check=None,   # use default
    fallback_to_main=True,
)
```

Custom sanity check example:

```python
def my_check(response: str) -> bool:
    return "buy" in response.lower() or "hold" in response.lower() or "sell" in response.lower()

result = bot.run_ai_simple_with_fallback(
    system_prompt="...",
    user_message="...",
    sanity_check=my_check,
    fallback_to_main=True,
)
```

### Sanity-checking main-LLM output (e.g. DeepSeekToolBot)

For demanding flows that use the main LLM with tools (e.g. portfolio weights), you can still use the cheap LLM to **verify** the main model’s output. Example: **DeepSeekToolBot** asks the main LLM to research and submit portfolio weights; then it runs a cheap-LLM sanity check (“Are these weights reasonable? YES or NO and one reason”). If the cheap LLM says NO, the bot retries once with the main LLM; if it still fails or no weights are submitted, rebalancing is skipped. This keeps heavy work on the main model while using the cheap model only for lightweight validation.

## Prerequisites

- **OPENROUTER_API_KEY**: Set this environment variable to your [OpenRouter](https://openrouter.ai/) API key. It is required for all AI features.
- Optional: **OPENROUTER_MAIN_MODEL**, **OPENROUTER_CHEAP_MODEL** to override the default models.
- Dependencies `langchain` and `langchain-openai` are included in the project.

## How to Use

### From a Bot Instance

```python
from tradingbot.utils.botclass import Bot

class MyBot(Bot):
    def __init__(self):
        super().__init__("MyBot", "QQQ", interval="1m", period="1d")
    def decisionFunction(self, row):
        return 0

bot = MyBot()
response = bot.run_ai(
    system_prompt="You are a trading assistant.",
    user_message="Summarize my portfolio and recent trades including profit on sells."
)
print(response)  # Model response as string
```

### Standalone (with tools, main LLM)

```python
from tradingbot.utils.aitools import run_ai_with_tools

response = run_ai_with_tools(
    system_prompt="You are a trading assistant.",
    user_message="What is my current portfolio worth?",
    bot=my_bot_instance,
)
print(response)
```

### Simple tasks (no tools, cheap LLM)

For single-turn text tasks that do not need tools (summarization, extraction, classification, rewriting), use `run_ai_simple` to save cost:

```python
from tradingbot.utils.aitools import run_ai_simple

summary = run_ai_simple(
    system_prompt="You summarize text concisely.",
    user_message="Summarize in one sentence: ...",
)
# Or from a bot:
summary = bot.run_ai_simple(
    system_prompt="You classify sentiment.",
    user_message="Classify as buy/hold/sell: ...",
)
```

Optional parameters:

- **run_ai** / **run_ai_with_tools**: **model** (default: from OPENROUTER_MAIN_MODEL), **max_tool_rounds** (default: `5`).
- **run_ai_simple**: **model** (default: from OPENROUTER_CHEAP_MODEL).
- **run_ai_simple_with_fallback**: **sanity_check** (optional callable; default: non-empty, no refusal/error prefix), **fallback_to_main** (default: `True`).

### Custom tools (extensibility)

Subclasses can override **`Bot.get_ai_tools(self)`** to return a list of custom LangChain tools; `run_ai()` merges them with the base tools automatically. You can also pass **`extra_tools=`** and optional **`tool_names=`** (whitelist of base tool names) to `run_ai()` or `run_ai_with_tools()`.

```python
class MyBot(Bot):
    def get_ai_tools(self):
        from langchain_core.tools import tool
        @tool
        def get_my_custom_data() -> str:
            """Returns custom data for this bot."""
            return "Custom data..."
        return [get_my_custom_data]
```

## Tools Available to the Model

The AI can call these tools during the conversation:

### 1. get_market_data(symbol, period)

Returns market data (OHLCV) for the given symbol and period.

- **symbol**: Optional. Uses the bot's primary symbol if not provided.
- **period**: Default `"14d"` (two weeks). Examples: `"5d"`, `"1mo"`.

Returns a compact summary and the last 14 rows of data.

### 2. get_portfolio_status()

Returns the current portfolio worth in USD and the holdings dictionary (symbol → quantity) for this bot. No parameters.

### 3. get_recent_trades(limit)

Returns the most recent trades for this bot.

- **limit**: Default `10`. Number of trades to return.

Each trade line includes: timestamp, symbol, buy/sell, quantity, price. For **sells**, the **profit** of the closed trade is included (e.g. `profit=123.45`).

### 4. get_stock_news(symbol, limit)

Returns recent news for a symbol from the database (title, link, publisher, published_at).

- **symbol**: Optional. Uses the bot's primary symbol if not provided.
- **limit**: Default `10`. Number of news items to return.

Data must already be loaded (e.g. by the fundamentals loader cronjob).

### 5. get_stock_earnings(symbol, limit)

Returns recent earnings dates and EPS (estimate, reported, surprise %) for a symbol from the database.

- **symbol**: Optional. Uses the bot's primary symbol if not provided.
- **limit**: Default `10`. Number of earnings rows to return.

### 6. get_stock_insider_trades(symbol, limit)

Returns recent insider transactions (insider name, type, shares, value) for a symbol from the database.

- **symbol**: Optional. Uses the bot's primary symbol if not provided.
- **limit**: Default `10`. Number of insider trades to return.

## Example

**With tools (main LLM):**

```python
bot = MyBot()
response = bot.run_ai(
    system_prompt="You are a trading assistant. Use the tools to get data when needed.",
    user_message="Summarize my portfolio and recent trades including profit on sells.",
)
# The model may call get_portfolio_status and get_recent_trades, then return a summary.
print(response)
```

**Simple task (cheap LLM):**

```python
# No tools needed; use cheap model
one_liner = bot.run_ai_simple(
    system_prompt="You reply in one short sentence.",
    user_message="What is the main risk of this trade? ...",
)
```

## API Reference

See [AITools API](../api/aitools.md) for the full module documentation, including `run_ai_with_tools`, `run_ai_simple`, `run_ai_simple_with_fallback`, and `_default_sanity_check`.
