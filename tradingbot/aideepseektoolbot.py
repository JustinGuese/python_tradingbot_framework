"""DeepSeek Tool Bot: AI-driven portfolio research and rebalancing via tools."""

import json
import logging
from typing import Optional

from langchain_core.tools import tool
from utils.core import Bot
from utils.portfolio import TRADEABLE

logger = logging.getLogger(__name__)


def _sanity_check_weights_cheap_llm(bot: "Bot", weights: dict) -> bool:
    """
    Use cheap LLM to verify portfolio weights are reasonable. Returns True if
    the cheap LLM says YES (sane), False if NO or unparseable.
    """
    import re
    weights_str = json.dumps(weights, sort_keys=True)
    system = (
        "You are a strict validator. Answer only YES or NO and one short reason. "
        "YES means the portfolio allocation is reasonable (diversified, weights sum to 1, symbols look valid). "
        "NO means it is unreasonable (e.g. single asset 100%%, unknown symbols, or clearly bad)."
    )
    user = f"Are these portfolio weights reasonable? Weights: {weights_str}"
    try:
        response = bot.run_ai_simple(system_prompt=system, user_message=user)
        text = (response or "").strip().upper()
        # Treat explicit NO as failure
        if re.search(r"\bNO\b", text):
            return False
        if re.search(r"\bYES\b", text):
            return True
        # Unparseable: default to reject so we can fall back to main retry
        return False
    except Exception as e:
        logger.error(f"Sanity check failed with error: {e}")
        return False



class DeepSeekToolBot(Bot):
    """
    Bot that uses AI with tools to research symbols and decide portfolio weights,
    then rebalances. The AI calls submit_portfolio_weights when ready.
    """

    def __init__(self):
        super().__init__("DeepSeekToolBot", symbol=None)
        self._submitted_weights: Optional[dict] = None
        self.tradeable_symbols = TRADEABLE

    def get_ai_tools(self):
        # Custom tools that close over self
        @tool
        def get_tradeable_symbols() -> str:
            """Returns the list of symbols available to trade."""
            return ", ".join(self.tradeable_symbols)

        @tool
        def submit_portfolio_weights(weights_json: str) -> str:
            """Submit final portfolio weights when ready. weights_json is a JSON object mapping symbol to weight (0.0-1.0). Weights must sum to 1.0. Call this ONLY when you have finished your analysis and are ready to execute."""
            try:
                weights = json.loads(weights_json)
                if not isinstance(weights, dict):
                    return "Error: weights_json must be a JSON object {symbol: weight}"
                total = sum(weights.values())
                if abs(total - 1.0) > 0.01:
                    return f"Error: weights must sum to 1.0, got {total:.4f}"
                self._submitted_weights = weights
                return "Weights submitted successfully. You may now stop."
            except json.JSONDecodeError as e:
                return f"Error parsing JSON: {e}"

        return [get_tradeable_symbols, submit_portfolio_weights]

    def makeOneIteration(self) -> int:
        self._submitted_weights = None

        system_prompt = """You are a portfolio manager. Use the tools to:
1. Get the list of tradeable symbols.
2. Research them: check market data, news, earnings, insider trades.
3. Check current portfolio status and recent trades.
4. Decide on target portfolio weights (must sum to 1.0).
5. You MUST end by calling submit_portfolio_weights with a JSON object like {"AAPL": 0.3, "MSFT": 0.2, ...}. Do not finish with only a text reply—always call submit_portfolio_weights with your final weights before stopping."""

        user_message = "Analyze the tradeable symbols and submit your recommended portfolio weights."

        response = self.run_ai(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tool_rounds=50,
        )
        logger.info(f"AI response: {response}")

        weights = self._submitted_weights
        if weights is None:
            logger.warning("Warning: AI did not submit portfolio weights; requesting submission (submit-only tools).")
            # Restrict to submit tools only; include symbol list so model can submit without calling get_tradeable_symbols
            symbols_list = ", ".join(self.tradeable_symbols)
            submit_only_prompt = (
                "You must call the submit_portfolio_weights tool with a JSON object mapping symbols to weights that sum to 1.0. "
                "Do not reply with text—only call the tool. Use only symbols from the list provided in the user message."
            )
            response = self.run_ai(
                system_prompt=submit_only_prompt,
                user_message=(
                    f"Call submit_portfolio_weights now. Tradeable symbols: {symbols_list}. "
                    "Example: {{\"AAPL\": 0.2, \"MSFT\": 0.2, \"QQQ\": 0.6}}. Weights must sum to 1.0."
                ),
                max_tool_rounds=5,
                tool_names=[],  # No base tools: only get_tradeable_symbols + submit_portfolio_weights
            )
            logger.info(f"Follow-up AI response: {response}")
            weights = self._submitted_weights
        if weights is None:
            logger.warning("Warning: AI did not submit portfolio weights")
            return 0

        # Sanity-check weights with cheap LLM; if rejected, retry once with main LLM
        if not _sanity_check_weights_cheap_llm(self, weights):
            logger.warning("Cheap LLM sanity check rejected weights; retrying with main LLM once.")
            self._submitted_weights = None
            retry_message = (
                "Your previous portfolio submission was rejected as unreasonable. "
                "Analyze again and submit new weights using submit_portfolio_weights."
            )
            self.run_ai(
                system_prompt=system_prompt,
                user_message=retry_message,
                max_tool_rounds=15,
            )
            weights = self._submitted_weights
            if weights is None:
                logger.warning("Warning: AI did not submit portfolio weights after retry")
                return 0
            if not _sanity_check_weights_cheap_llm(self, weights):
                logger.warning("Warning: Sanity check rejected weights again; skipping rebalance")
                return 0

        logger.info(f"Submitted weights: {weights}")

        # Normalize if needed
        total = sum(w for w in weights.values() if w > 0)
        if total > 0 and abs(total - 1.0) > 0.001:
            weights = {k: v / total for k, v in weights.items()}

        self.rebalancePortfolio(weights, onlyOver50USD=True)
        logger.info("Rebalancing completed")
        return 0

if __name__ == "__main__":
    bot = DeepSeekToolBot()
    bot.run()