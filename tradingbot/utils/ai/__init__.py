"""
AI utilities for trading bots.

This subpackage exposes a narrow, stable API around the LangChain /
OpenRouter powered helpers used by `Bot.run_ai*` methods.

It is intentionally thin: the heavy lifting still lives in
`tradingbot.utils.aitools`, which remains the single implementation
module. New code should import from `utils.ai` rather than depending
on the concrete LangChain wiring.
"""

from ..aitools import (
    run_ai_simple,
    run_ai_simple_with_fallback,
    run_ai_with_tools,
)

__all__ = [
    "run_ai_simple",
    "run_ai_simple_with_fallback",
    "run_ai_with_tools",
]
